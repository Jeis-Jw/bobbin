#!/usr/bin/env python3
from __future__ import annotations

import argparse
import datetime
import hashlib
import json
import os
import pathlib
import re
import stat
import subprocess
import sys
import tempfile
import time
import uuid
from typing import Any, Sequence

import core_compatibility
import decision_cli


RECEIPT_SCHEMA = "context-decision-workflow-receipt/v1"
APPROVAL_SCHEMA = "context-decision-workflow-approval/v1"
MAX_INPUT_BYTES = 64 * 1024
MAX_RECEIPT_BYTES = 16 * 1024 * 1024
RECEIPT_DIRECTORY_NAME = "context-decision"
RECEIPT_TTL_SECONDS = 24 * 60 * 60
CANDIDATE_ID_RE = re.compile(r"^cand_[0-9a-f]{32}$")
DEFAULT_RECEIPT_NAME_RE = re.compile(r"^(cand_[0-9a-f]{32})\.json$")
REPREVIEW_MESSAGE = "State changed before storage; create a new preview."
CORE_MISMATCH_MESSAGE = "The installed core is not same-major compatible; install a compatible core and start a new session."


class WorkflowError(Exception):
    def __init__(self, code: str, message: str, details: dict[str, Any] | None = None, exit_code: int = 2):
        super().__init__(message)
        self.code = code
        self.message = message
        self.details = details or {}
        self.exit_code = exit_code

    def envelope(self) -> dict[str, Any]:
        return {"ok": False, "error": {"code": self.code, "message": self.message, "details": self.details}}


class CoreCommandError(Exception):
    def __init__(self, payload: dict[str, Any], exit_code: int):
        super().__init__("context-core command failed")
        self.payload = payload
        self.exit_code = exit_code


def _emit(value: dict[str, Any], *, compact: bool) -> None:
    sys.stdout.write(
        json.dumps(value, ensure_ascii=False, separators=(",", ":")) + "\n"
        if compact
        else json.dumps(value, ensure_ascii=False, indent=2) + "\n"
    )


def _load_json_argument(value: str) -> Any:
    if not value.startswith("@") or value == "@-":
        raise WorkflowError("usage_invalid", "JSON input must use a named @file")
    path = pathlib.Path(value[1:])
    try:
        if path.is_symlink() or path.stat().st_size > MAX_INPUT_BYTES:
            raise WorkflowError("input_unavailable", "JSON input is unsafe or too large", {"path": str(path)}, 5)
        return json.loads(path.read_text(encoding="utf-8"))
    except WorkflowError:
        raise
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise WorkflowError("input_unavailable", "JSON input could not be read", {"path": str(path)}, 3) from error


def _core_cli(value: str | None) -> pathlib.Path:
    """Resolve the context-core entrypoint.

    An explicit ``--core-cli`` keeps the historical contract. Without it, the
    sibling ``context-core`` package next to this plugin is resolved
    deterministically: exactly one same-major, manifest-validated candidate is
    accepted and its actual SHA-256 is bound by every later core call. Zero or
    several candidates fail closed with the diagnostic list; nothing is guessed.
    """
    if value:
        return decision_cli.required_core_surface(value)
    candidates = core_compatibility.discover_core_candidates(
        __file__,
        None,
        compatible_major=decision_cli.CORE_COMPATIBLE_MAJOR,
        minimum_version="1.0.0",
    )
    if len(candidates) != 1:
        raise WorkflowError(
            "core_cli_required",
            "pass --core-cli <path>: Bobbin's embedded core could not be resolved",
            {"candidates": candidates},
            exit_code=2,
        )
    return decision_cli.required_core_surface(candidates[0]["entrypoint"])


def _file_digest(path: pathlib.Path) -> str:
    return "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()


def _run_core(
    core_cli: pathlib.Path,
    repo: pathlib.Path,
    *arguments: str,
    expected_sha256: str | None = None,
) -> dict[str, Any]:
    decision_cli.required_core_surface(str(core_cli), expected_sha256=expected_sha256)
    vault_arguments = [] if arguments[0] in {"schema", "capabilities"} else ["--vault", str(repo)]
    completed = subprocess.run(
        [sys.executable, str(core_cli), *vault_arguments, *arguments, "--json"],
        env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
        text=True,
        capture_output=True,
    )
    try:
        payload = json.loads(completed.stdout)
    except json.JSONDecodeError as error:
        raise WorkflowError(
            "core_result_invalid",
            "context-core returned invalid JSON",
            {"command": " ".join(arguments)},
            7,
        ) from error
    if completed.returncode != 0:
        if not isinstance(payload, dict) or payload.get("ok") is not False:
            raise WorkflowError("core_result_invalid", "context-core error envelope is invalid", exit_code=7)
        raise CoreCommandError(payload, completed.returncode)
    if not isinstance(payload, dict) or payload.get("ok") is not True or not isinstance(payload.get("result"), dict):
        raise WorkflowError("core_result_invalid", "context-core success envelope is invalid", exit_code=7)
    return payload["result"]


def _vault_identity(repo: pathlib.Path) -> dict[str, Any]:
    try:
        root = repo.resolve(strict=True)
        metadata = root.stat()
        if not stat.S_ISDIR(metadata.st_mode):
            raise OSError("vault root is not a directory")
    except (OSError, RuntimeError) as error:
        raise WorkflowError("vault_not_found", "vault directory could not be resolved", exit_code=3) from error
    return {
        "schema": "context-vault-identity/v1",
        "root": {"path": str(root), "device": str(metadata.st_dev), "inode": str(metadata.st_ino)},
    }


def _assert_receipt_outside_vault(path: pathlib.Path, repo: pathlib.Path) -> None:
    root = repo.resolve(strict=True)
    if path == root or root in path.parents:
        raise WorkflowError(
            "receipt_path_invalid",
            "transient receipt must be outside the vault",
            {"path": str(path)},
            5,
        )


def _default_receipt_dir(repo: pathlib.Path) -> pathlib.Path:
    try:
        temp_root = pathlib.Path(tempfile.gettempdir()).resolve(strict=True)
    except OSError as error:
        raise WorkflowError("receipt_directory_invalid", "default receipt root is unavailable", exit_code=5) from error
    directory = temp_root / RECEIPT_DIRECTORY_NAME
    _assert_receipt_outside_vault(directory, repo)
    created = False
    try:
        os.mkdir(directory, 0o700)
        created = True
    except FileExistsError:
        pass
    except OSError as error:
        raise WorkflowError(
            "receipt_directory_invalid",
            "default receipt directory could not be created",
            {"path": str(directory)},
            5,
        ) from error
    if created:
        try:
            os.chmod(directory, 0o700, follow_symlinks=False)
        except OSError as error:
            raise WorkflowError(
                "receipt_directory_invalid",
                "default receipt directory mode could not be secured",
                {"path": str(directory)},
                5,
            ) from error
    try:
        metadata = os.lstat(directory)
    except OSError as error:
        raise WorkflowError(
            "receipt_directory_invalid",
            "default receipt directory is unavailable",
            {"path": str(directory)},
            5,
        ) from error
    owner_mismatch = hasattr(os, "getuid") and metadata.st_uid != os.getuid()
    if (
        stat.S_ISLNK(metadata.st_mode)
        or not stat.S_ISDIR(metadata.st_mode)
        or stat.S_IMODE(metadata.st_mode) != 0o700
        or owner_mismatch
    ):
        raise WorkflowError(
            "receipt_directory_invalid",
            "default receipt directory must be an owner-only mode-0700 regular directory",
            {"path": str(directory)},
            5,
        )
    resolved = directory.resolve(strict=True)
    if resolved != directory:
        raise WorkflowError(
            "receipt_directory_invalid",
            "default receipt directory must not traverse a symlink",
            {"path": str(directory)},
            5,
        )
    _assert_receipt_outside_vault(resolved, repo)
    return resolved


def _require_candidate_id(value: str) -> str:
    if not CANDIDATE_ID_RE.fullmatch(value):
        raise WorkflowError("usage_invalid", "candidate id must use cand_ plus 32 lowercase hex characters")
    return value


def _receipt_path(value: str | pathlib.Path, repo: pathlib.Path, *, must_exist: bool) -> pathlib.Path:
    requested = pathlib.Path(value)
    if not requested.is_absolute():
        raise WorkflowError("receipt_path_invalid", "receipt file must use an absolute path")
    if requested.name in {"", ".", ".."}:
        raise WorkflowError("receipt_path_invalid", "receipt file must use a regular filename")
    try:
        parent = requested.parent.resolve(strict=True)
    except OSError as error:
        raise WorkflowError("receipt_path_invalid", "receipt parent is unavailable", {"path": str(requested.parent)}, 3) from error
    path = parent / requested.name
    _assert_receipt_outside_vault(path, repo)
    if must_exist:
        try:
            metadata = os.lstat(path)
        except OSError as error:
            raise WorkflowError("receipt_unavailable", REPREVIEW_MESSAGE, {"path": str(path)}, 3) from error
        if (
            stat.S_ISLNK(metadata.st_mode)
            or not stat.S_ISREG(metadata.st_mode)
            or stat.S_IMODE(metadata.st_mode) != 0o600
        ):
            raise WorkflowError("receipt_unavailable", "workflow receipt is unavailable", {"path": str(path)}, 3)
    else:
        try:
            os.lstat(path)
        except FileNotFoundError:
            pass
        except OSError as error:
            raise WorkflowError("receipt_path_invalid", "receipt path could not be inspected", {"path": str(path)}, 5) from error
        else:
            raise WorkflowError("receipt_exists", "workflow receipt already exists; reuse it for apply or reject", {"path": str(path)}, 5)
    return path


def _write_receipt(path: pathlib.Path, receipt: dict[str, Any]) -> None:
    payload = (json.dumps(receipt, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8")
    if len(payload) > MAX_RECEIPT_BYTES:
        raise WorkflowError("receipt_too_large", "workflow receipt exceeds 16 MiB", exit_code=5)
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    descriptor: int | None = None
    created = False
    try:
        descriptor = os.open(path, flags, 0o600)
        created = True
        os.fchmod(descriptor, 0o600)
        with os.fdopen(descriptor, "wb") as stream:
            descriptor = None
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
    except OSError as error:
        if descriptor is not None:
            os.close(descriptor)
        if created:
            try:
                path.unlink(missing_ok=True)
            except OSError:
                pass
        raise WorkflowError("receipt_write_failed", "workflow receipt could not be written", {"path": str(path)}, 5) from error


def _load_receipt(path: pathlib.Path) -> tuple[dict[str, Any], tuple[int, int], os.stat_result]:
    flags = os.O_RDONLY
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    descriptor: int | None = None
    try:
        descriptor = os.open(path, flags)
        metadata = os.fstat(descriptor)
        if (
            not stat.S_ISREG(metadata.st_mode)
            or stat.S_IMODE(metadata.st_mode) != 0o600
            or metadata.st_size > MAX_RECEIPT_BYTES
        ):
            raise WorkflowError("receipt_invalid", "workflow receipt exceeds 16 MiB", exit_code=5)
        with os.fdopen(descriptor, "rb") as stream:
            descriptor = None
            payload = stream.read(MAX_RECEIPT_BYTES + 1)
        if len(payload) > MAX_RECEIPT_BYTES:
            raise WorkflowError("receipt_invalid", "workflow receipt exceeds 16 MiB", exit_code=5)
        receipt = json.loads(payload.decode("utf-8"))
    except WorkflowError:
        raise
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise WorkflowError("receipt_invalid", REPREVIEW_MESSAGE, {"path": str(path)}, 5) from error
    finally:
        if descriptor is not None:
            os.close(descriptor)
    required = {
        "schema",
        "status",
        "created_at",
        "candidate_id",
        "operation",
        "approval_material",
        "approval_digest",
        "receipt_digest",
    }
    if (
        not isinstance(receipt, dict)
        or set(receipt) != required
        or receipt.get("schema") != RECEIPT_SCHEMA
        or receipt.get("status") != "pending"
        or receipt.get("operation") not in {"capture", "supersede", "withdraw"}
        or not isinstance(receipt.get("candidate_id"), str)
        or not CANDIDATE_ID_RE.fullmatch(receipt["candidate_id"])
    ):
        raise WorkflowError("receipt_invalid", "workflow receipt envelope is invalid", exit_code=5)
    try:
        created_at = datetime.datetime.fromisoformat(receipt["created_at"])
    except (TypeError, ValueError) as error:
        raise WorkflowError("receipt_invalid", "workflow receipt timestamp is invalid", exit_code=5) from error
    if created_at.tzinfo is None:
        raise WorkflowError("receipt_invalid", "workflow receipt timestamp is invalid", exit_code=5)
    material = dict(receipt)
    digest = material.pop("receipt_digest")
    if digest != decision_cli.canonical_digest(material):
        raise WorkflowError("receipt_invalid", "workflow receipt digest is invalid", exit_code=5)
    approval_material = receipt.get("approval_material")
    approval_fields = {
        "schema", "vault_identity", "core", "operation", "workflow_input_digest",
        "owner_result_digest", "core_approval_digest", "core_bundle",
    }
    if (
        not isinstance(approval_material, dict)
        or set(approval_material) != approval_fields
        or approval_material.get("schema") != APPROVAL_SCHEMA
        or approval_material.get("operation") != receipt["operation"]
    ):
        raise WorkflowError("receipt_invalid", "workflow approval material is invalid", exit_code=5)
    if receipt.get("approval_digest") != decision_cli.canonical_digest(approval_material):
        raise WorkflowError("approval_digest_mismatch", "workflow approval material changed after approval", exit_code=5)
    core = approval_material.get("core")
    bundle = approval_material.get("core_bundle")
    core_digest = approval_material.get("core_approval_digest")
    vault_identity = approval_material.get("vault_identity")
    if (
        not isinstance(core, dict)
        or set(core) != {"path", "sha256"}
        or not isinstance(core.get("path"), str)
        or not pathlib.Path(core["path"]).is_absolute()
        or not isinstance(core.get("sha256"), str)
        or not isinstance(bundle, dict)
        or bundle.get("schema") != "context-mutation-bundle/v1"
        or bundle.get("approval_digest") != core_digest
        or core_digest != decision_cli.canonical_digest(bundle.get("approval_material"))
        or not isinstance(bundle.get("approval_material"), dict)
        or bundle["approval_material"].get("vault_identity") != vault_identity
        or not all(
            isinstance(approval_material.get(field), str)
            and approval_material[field].startswith("sha256:")
            and len(approval_material[field]) == 71
            for field in ("workflow_input_digest", "owner_result_digest", "core_approval_digest")
        )
    ):
        raise WorkflowError("receipt_invalid", "workflow receipt bundle binding is invalid", exit_code=5)
    return receipt, (metadata.st_dev, metadata.st_ino), metadata


def _remove_receipt(path: pathlib.Path, identity: tuple[int, int]) -> None:
    metadata = os.lstat(path)
    if (
        stat.S_ISLNK(metadata.st_mode)
        or not stat.S_ISREG(metadata.st_mode)
        or (metadata.st_dev, metadata.st_ino) != identity
    ):
        raise OSError("receipt changed before cleanup")
    path.unlink()


def _default_receipt_entries(repo: pathlib.Path, directory: pathlib.Path) -> list[tuple[pathlib.Path, dict[str, Any], tuple[int, int], os.stat_result]]:
    entries: list[tuple[pathlib.Path, dict[str, Any], tuple[int, int], os.stat_result]] = []
    try:
        paths = list(directory.iterdir())
    except OSError as error:
        raise WorkflowError("receipt_directory_invalid", "default receipt directory could not be listed", exit_code=5) from error
    for path in paths:
        match = DEFAULT_RECEIPT_NAME_RE.fullmatch(path.name)
        if match is None:
            continue
        try:
            metadata = os.lstat(path)
        except OSError:
            continue
        if (
            stat.S_ISLNK(metadata.st_mode)
            or not stat.S_ISREG(metadata.st_mode)
            or stat.S_IMODE(metadata.st_mode) != 0o600
        ):
            continue
        try:
            checked = _receipt_path(path, repo, must_exist=True)
            receipt, identity, loaded_metadata = _load_receipt(checked)
        except WorkflowError:
            continue
        if receipt["candidate_id"] != match.group(1):
            continue
        entries.append((checked, receipt, identity, loaded_metadata))
    return entries


def _sweep_expired_receipts(repo: pathlib.Path, directory: pathlib.Path, *, now: float | None = None) -> None:
    current_time = time.time() if now is None else now
    for path, _receipt, identity, metadata in _default_receipt_entries(repo, directory):
        if current_time - metadata.st_mtime <= RECEIPT_TTL_SECONDS:
            continue
        try:
            _remove_receipt(path, identity)
        except OSError:
            # Cleanup is best effort. The expired receipt remains ineligible for
            # automatic selection and no unrelated directory entry is touched.
            continue


def _receipt_binding_matches(
    receipt: dict[str, Any],
    repo: pathlib.Path,
    core_cli: pathlib.Path,
) -> bool:
    approval_material = receipt["approval_material"]
    core = approval_material["core"]
    return (
        approval_material["vault_identity"] == _vault_identity(repo)
        and core["path"] == str(core_cli)
        and core["sha256"] == _file_digest(core_cli)
    )


_UNSAFE_PATH = object()


def _repository_file_digest(repo: pathlib.Path, relative: Any) -> str | None | object:
    if not isinstance(relative, str):
        return _UNSAFE_PATH
    pure = pathlib.PurePosixPath(relative)
    if pure.is_absolute() or not pure.parts or any(part in {"", ".", ".."} for part in pure.parts):
        return _UNSAFE_PATH
    repository = repo.resolve(strict=True)
    path = repository.joinpath(*pure.parts)
    current = repository
    try:
        for part in pure.parts[:-1]:
            current = current / part
            if current.exists() and (current.is_symlink() or not current.is_dir()):
                return _UNSAFE_PATH
        try:
            metadata = os.lstat(path)
        except FileNotFoundError:
            return None
        if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISREG(metadata.st_mode):
            return _UNSAFE_PATH
        return _file_digest(path)
    except OSError:
        return _UNSAFE_PATH


def _receipt_plan_is_pending(receipt: dict[str, Any], repo: pathlib.Path) -> bool:
    try:
        plan = receipt["approval_material"]["core_bundle"]["approval_material"]["plan"]
        operations = plan["operations"]
        read_preconditions = plan.get("read_preconditions", [])
    except (KeyError, TypeError):
        return False
    if not isinstance(operations, list) or not isinstance(read_preconditions, list):
        return False
    for precondition in read_preconditions:
        if (
            not isinstance(precondition, dict)
            or set(precondition) != {"id", "path", "sha256"}
            or _repository_file_digest(repo, precondition["path"]) != precondition["sha256"]
        ):
            return False

    virtual_state: dict[str, str | None | object] = {}

    def current_digest(relative: Any) -> str | None | object:
        if not isinstance(relative, str):
            return _UNSAFE_PATH
        if relative not in virtual_state:
            virtual_state[relative] = _repository_file_digest(repo, relative)
        return virtual_state[relative]

    for operation in operations:
        if not isinstance(operation, dict):
            return False
        kind = operation.get("op")
        if kind == "index_rebuild":
            before = operation.get("before_sha256")
            after = operation.get("after_sha256")
            # The index is a projection: its bytes may drift under an approved plan
            # (branch merge, unrelated write) and core re-derives it at apply, so only
            # the operation shape decides pendingness here.
            if not isinstance(before, dict) or not isinstance(after, dict) or set(after) != set(before):
                return False
            if all(current_digest(relative) == digest for relative, digest in after.items()):
                return False
            virtual_state.update(after)
        elif kind in {"file_create", "file_replace", "file_delete"}:
            relative = operation.get("path")
            if current_digest(relative) != operation.get("before_sha256"):
                return False
            if not isinstance(relative, str):
                return False
            virtual_state[relative] = operation.get("after_sha256")
        elif kind == "file_move":
            source = operation.get("from_path")
            destination = operation.get("to_path")
            if (
                current_digest(source) != operation.get("before_sha256")
                or current_digest(destination) != operation.get("destination_before_sha256")
            ):
                return False
            if not isinstance(source, str) or not isinstance(destination, str):
                return False
            virtual_state[source] = None
            virtual_state[destination] = operation.get("after_sha256")
        else:
            return False
    return bool(operations)


def _select_default_receipt(
    repo: pathlib.Path,
    core_cli: pathlib.Path,
    directory: pathlib.Path,
) -> tuple[pathlib.Path, dict[str, Any], tuple[int, int]]:
    now = time.time()
    matches = [
        (path, receipt, identity)
        for path, receipt, identity, metadata in _default_receipt_entries(repo, directory)
        if now - metadata.st_mtime <= RECEIPT_TTL_SECONDS
        and _receipt_binding_matches(receipt, repo, core_cli)
        and _receipt_plan_is_pending(receipt, repo)
    ]
    if not matches:
        raise WorkflowError("receipt_selection_none", REPREVIEW_MESSAGE, exit_code=5)
    if len(matches) != 1:
        raise WorkflowError(
            "receipt_selection_ambiguous",
            REPREVIEW_MESSAGE,
            {"matching_receipts": len(matches)},
            5,
        )
    return matches[0]


def _require_receipt_binding(receipt: dict[str, Any], repo: pathlib.Path, core_cli: pathlib.Path) -> None:
    approval_material = receipt["approval_material"]
    if approval_material["vault_identity"] != _vault_identity(repo):
        raise WorkflowError("vault_identity_mismatch", REPREVIEW_MESSAGE, exit_code=5)
    core = approval_material["core"]
    if core["path"] != str(core_cli) or core["sha256"] != _file_digest(core_cli):
        raise WorkflowError("core_surface_changed", REPREVIEW_MESSAGE, exit_code=5)


def _require_ready_core(host: str, core_cli: pathlib.Path, doctor: dict[str, Any]) -> dict[str, Any]:
    doctor = decision_cli.validate_core_doctor_handshake(doctor, allowed_states={"ready"})
    return {
        "code": "ready",
        "host": host,
        "required_plugin": dict(decision_cli.REQUIRED_PLUGIN),
        "observed": {
            "entrypoint": str(core_cli),
            "entrypoint_sha256": _file_digest(core_cli),
            "protocol": decision_cli.PROTOCOL,
            "repository_state": doctor["repository_state"],
        },
        "write_policy": {"repository": "none", "host_configuration": "none"},
    }


INLINE_FIELDS = (
    "candidate_id",
    "title",
    "summary",
    "scope",
    "decision_key",
    "captured_from",
    "commitment_evidence",
    "sec_decision",
    "sec_rationale",
    "sec_alternatives",
)
ATTESTATION_FLAGS = (
    "attest_explicit_choice",
    "attest_scope_identified",
    "attest_commitment_present",
)


def _semantic_inputs(args: argparse.Namespace) -> tuple[dict[str, Any], dict[str, Any]]:
    if not args.inline:
        inline_values = [
            name
            for name in (
                *INLINE_FIELDS, "sec_constraints", "sec_tradeoffs", "sec_revisit", "revisit_on",
                "source_ref", "tag", "search_term", "informed_by", *decision_cli.TYPED_RELATION_INPUTS,
                *ATTESTATION_FLAGS,
            )
            if getattr(args, name)
        ]
        if inline_values:
            raise WorkflowError("usage_invalid", "file input cannot be mixed with inline semantic fields", {"fields": inline_values})
        if args.attestation is None:
            raise WorkflowError("usage_invalid", "file input requires --attestation @file")
        candidate = _load_json_argument(args.candidate)
        decision_cli.validate_candidate(candidate)
        return candidate, _load_json_argument(args.attestation)

    if args.attestation is not None:
        raise WorkflowError("usage_invalid", "inline input cannot be mixed with --attestation")
    if args.candidate_id is None:
        args.candidate_id = "cand_" + uuid.uuid4().hex
    if args.captured_from is None:
        args.captured_from = "conversation"
    missing = [name for name in INLINE_FIELDS if not getattr(args, name)]
    if missing:
        raise WorkflowError("usage_invalid", "inline semantic input is incomplete", {"fields": missing})
    missing_assertions = [name for name in ATTESTATION_FLAGS if not getattr(args, name)]
    if missing_assertions:
        raise WorkflowError(
            "semantic_attestation_required",
            "inline preview requires the caller to explicitly attest all decision claim assertions",
            {"flags": ["--" + name.replace("_", "-") for name in missing_assertions]},
            5,
        )
    candidate = decision_cli.build_direct_candidate(args)
    attestation = {
        "schema": "context-semantic-attestation/v1",
        "operation": "claim",
        "input_schema": candidate["schema"],
        "input_digest": decision_cli.canonical_digest(candidate),
        "assertions": [
            {"name": "explicit_choice", "value": True, "evidence_pointers": ["/owner_inputs/decision/decision"]},
            {"name": "scope_identified", "value": True, "evidence_pointers": ["/scope_hint"]},
            {"name": "commitment_present", "value": True, "evidence_pointers": ["/evidence/0"]},
        ],
    }
    decision_cli.validate_attestation(attestation, candidate, "claim", decision_cli.decision_capability()["claim_assertions"])
    return candidate, attestation


def _prepare_operation_inputs(
    args: argparse.Namespace,
) -> tuple[str, str, dict[str, Any], dict[str, Any] | None, dict[str, Any] | None]:
    if args.withdraw is not None:
        if args.reason is None:
            raise WorkflowError("usage_invalid", "withdraw preview requires --reason")
        withdraw_fields = [
            name
            for name in (
                *INLINE_FIELDS[1:],
                "sec_constraints",
                "sec_tradeoffs",
                "sec_revisit",
                "revisit_on",
                "source_ref",
                "tag",
                "search_term",
                "informed_by",
                *decision_cli.TYPED_RELATION_INPUTS,
                *ATTESTATION_FLAGS,
                "ack_conflicts",
            )
            if getattr(args, name)
        ]
        if args.inline or args.candidate is not None or args.attestation is not None or withdraw_fields:
            raise WorkflowError(
                "usage_invalid",
                "withdraw preview cannot include candidate or attestation input",
                {"fields": withdraw_fields},
            )
        candidate_id = _require_candidate_id(args.candidate_id or ("cand_" + uuid.uuid4().hex))
        workflow_input = {
            "schema": "context-decision-workflow-input/v1",
            "operation": "withdraw",
            "candidate_id": candidate_id,
            "predecessor_id": args.withdraw,
            "reason": args.reason,
        }
        return "withdraw", candidate_id, workflow_input, None, None

    if args.reason is not None:
        raise WorkflowError("usage_invalid", "--reason is only valid with --withdraw")
    if not args.inline and args.candidate is None:
        raise WorkflowError("usage_invalid", "capture and supersede preview require --inline or --candidate")
    candidate, attestation = _semantic_inputs(args)
    candidate_id = _require_candidate_id(candidate["candidate_id"])
    operation = "supersede" if args.supersede is not None else "capture"
    workflow_input = {
        "schema": "context-decision-workflow-input/v1",
        "operation": operation,
        "candidate": candidate,
    }
    if args.supersede is not None:
        workflow_input["predecessor_id"] = args.supersede
    return operation, candidate_id, workflow_input, candidate, attestation


def preview(args: argparse.Namespace) -> dict[str, Any]:
    decision_cli._bobbin_settings("require_enabled", decision_cli.vault_root(getattr(args, "vault", None)), ["decision"])
    repo = decision_cli.vault_root(getattr(args, "vault", None))
    explicit_receipt_path = (
        _receipt_path(args.receipt_file, repo, must_exist=False)
        if args.receipt_file is not None
        else None
    )
    operation, candidate_id, workflow_input, candidate, attestation = _prepare_operation_inputs(args)
    if explicit_receipt_path is None:
        default_directory = _default_receipt_dir(repo)
        _sweep_expired_receipts(repo, default_directory)
        receipt_path = _receipt_path(default_directory / f"{candidate_id}.json", repo, must_exist=False)
    else:
        receipt_path = explicit_receipt_path
    core_cli = _core_cli(args.core_cli)
    core_cli_sha256 = _file_digest(core_cli)
    schema = _run_core(core_cli, repo, "schema", expected_sha256=core_cli_sha256)
    decision_values = candidate.get("owner_inputs", {}).get("decision", {}) if candidate is not None else {}
    require_typed_relations = any(decision_values.get(field) for field in decision_cli.TYPED_RELATION_INPUTS)
    decision_cli.validate_core_schema_handshake(
        schema,
        require_typed_relations=require_typed_relations,
        core_cli_value=str(core_cli),
    )
    doctor = _run_core(core_cli, repo, "doctor", expected_sha256=core_cli_sha256)
    preflight = _require_ready_core(args.host, core_cli, doctor)
    if operation == "capture":
        assert candidate is not None and attestation is not None
        owner_result = decision_cli.build_claim_result(
            candidate,
            attestation,
            acknowledged_conflicts=args.ack_conflicts,
            repo=repo,
        )
    elif operation == "supersede":
        assert candidate is not None and attestation is not None and args.supersede is not None
        owner_result = decision_cli.build_supersede_result(
            repo,
            args.supersede,
            candidate,
            attestation,
            acknowledged_conflicts=args.ack_conflicts,
        )
    else:
        assert args.withdraw is not None and args.reason is not None
        owner_result = decision_cli.build_withdraw_result(repo, args.withdraw, args.reason)
    validation = decision_cli.validate_batch(repo, owner_result)
    with tempfile.TemporaryDirectory(prefix="context-decision-workflow-") as temp:
        root = pathlib.Path(temp)
        owner_path = root / "owner-result.json"
        validation_path = root / "owner-validation.json"
        owner_path.write_text(json.dumps(owner_result, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
        validation_path.write_text(json.dumps(validation, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
        finalized = _run_core(
            core_cli,
            repo,
            "transaction",
            "preview",
            "--owner-result",
            f"@{owner_path}",
            "--owner-validation",
            f"@{validation_path}",
            expected_sha256=core_cli_sha256,
        )
    bundle = finalized.get("bundle")
    core_approval_digest = finalized.get("approval_digest")
    approval_preview = finalized.get("approval_preview")
    try:
        plan_id = bundle["approval_material"]["plan"]["plan_id"]
    except (KeyError, TypeError) as error:
        raise WorkflowError("core_result_invalid", "context-core preview omitted the exact plan", exit_code=7) from error
    if bundle.get("approval_digest") != core_approval_digest or not isinstance(approval_preview, dict):
        raise WorkflowError("core_result_invalid", "context-core preview binding is invalid", exit_code=7)
    if _file_digest(core_cli) != core_cli_sha256:
        raise WorkflowError("core_surface_changed", "context-core surface changed during preview; retry", exit_code=5)
    approval_material: dict[str, Any] = {
        "schema": APPROVAL_SCHEMA,
        "vault_identity": bundle["approval_material"].get("vault_identity"),
        "core": {"path": str(core_cli), "sha256": core_cli_sha256},
        "operation": operation,
        "workflow_input_digest": decision_cli.canonical_digest(workflow_input),
        "owner_result_digest": decision_cli.canonical_digest(owner_result),
        "core_approval_digest": core_approval_digest,
        "core_bundle": bundle,
    }
    approval_digest = decision_cli.canonical_digest(approval_material)
    receipt: dict[str, Any] = {
        "schema": RECEIPT_SCHEMA,
        "status": "pending",
        "created_at": decision_cli.now_rfc3339(),
        "candidate_id": candidate_id,
        "operation": operation,
        "approval_material": approval_material,
        "approval_digest": approval_digest,
    }
    receipt["receipt_digest"] = decision_cli.canonical_digest(receipt)
    _write_receipt(receipt_path, receipt)
    return {
        "schema": "context-decision-workflow-preview/v1",
        "receipt_file": str(receipt_path),
        "receipt_digest": receipt["receipt_digest"],
        "candidate_id": candidate_id,
        "operation": operation,
        "approval_preview": approval_preview,
        "approval_digest": approval_digest,
        "plan_id": plan_id,
        "preflight": {"code": preflight["code"], "observed": preflight["observed"]},
        "applied": False,
    }


def apply(args: argparse.Namespace) -> dict[str, Any]:
    repo = decision_cli.vault_root(getattr(args, "vault", None))
    core_cli = _core_cli(args.core_cli)
    if not isinstance(args.approved_digest, str) or not args.approved_digest:
        raise WorkflowError(
            "approval_digest_required",
            "apply requires the approval digest returned by preview",
            exit_code=2,
        )
    if args.receipt_file is not None:
        receipt_path = _receipt_path(args.receipt_file, repo, must_exist=True)
        receipt, receipt_identity, _ = _load_receipt(receipt_path)
    else:
        default_directory = _default_receipt_dir(repo)
        receipt_path, receipt, receipt_identity = _select_default_receipt(repo, core_cli, default_directory)
    _require_receipt_binding(receipt, repo, core_cli)
    if args.approved_digest != receipt["approval_digest"]:
        raise WorkflowError("approval_digest_mismatch", "approved digest does not match the frozen workflow receipt", exit_code=5)
    approval_material = receipt["approval_material"]
    with tempfile.TemporaryDirectory(prefix="context-decision-workflow-") as temp:
        bundle_path = pathlib.Path(temp) / "bundle.json"
        bundle_path.write_text(json.dumps(approval_material["core_bundle"], ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
        applied = _run_core(
            core_cli,
            repo,
            "transaction",
            "apply",
            "--plan-bundle",
            f"@{bundle_path}",
            "--approved-digest",
            approval_material["core_approval_digest"],
            "--approval-source", getattr(args, "approval_source", "user"),
            *(["--policy-decision", args.policy_decision] if getattr(args, "policy_decision", None) else []),
            *(["--policy-reason", args.policy_reason] if getattr(args, "policy_reason", None) else []),
            expected_sha256=approval_material["core"]["sha256"],
        )
    core_applied_digest = applied.get("approval_digest")
    warnings = list(applied.get("warnings", [])) if isinstance(applied.get("warnings", []), list) else []
    receipt_removed = False
    if not args.keep_receipt:
        try:
            _remove_receipt(receipt_path, receipt_identity)
            receipt_removed = True
        except OSError:
            warnings.append("receipt_cleanup_failed")
    result = {
        "schema": "context-decision-workflow-apply/v1",
        "receipt_file": str(receipt_path),
        "receipt_digest": receipt["receipt_digest"],
        **applied,
        "core_approval_digest": core_applied_digest,
        "approval_digest": receipt["approval_digest"],
        "receipt_removed": receipt_removed,
        "warnings": warnings,
    }
    return result


def record(args: argparse.Namespace) -> dict[str, Any]:
    """Preview and apply one DEC in a single call.

    The frozen receipt, approval_digest binding, pinned core SHA, CAS, lock and
    atomic write are unchanged: ``record`` runs the same ``preview`` and then the
    same ``apply`` on that unchanged receipt inside one process. The caller
    attests with ``--approved`` that the user gave direct, explicit,
    unconditional semantic approval in conversation; the digest never has to be
    relayed by the agent.
    """
    if not getattr(args, "approved", False) and getattr(args, "approval_source", "user") != "policy":
        raise WorkflowError(
            "approval_attestation_required",
            "record requires --approved: the user's direct, explicit, unconditional approval given in conversation",
            exit_code=2,
        )
    previewed = preview(args)
    apply_args = argparse.Namespace(
        vault=getattr(args, "vault", None),
        core_cli=previewed["preflight"]["observed"]["entrypoint"],
        receipt_file=previewed["receipt_file"],
        approved_digest=previewed["approval_digest"],
        keep_receipt=getattr(args, "keep_receipt", False),
        approval_source=getattr(args, "approval_source", "user"),
        policy_decision=getattr(args, "policy_decision", None),
        policy_reason=getattr(args, "policy_reason", None),
    )
    applied = apply(apply_args)
    return {
        "schema": "context-decision-workflow-record/v1",
        "candidate_id": previewed["candidate_id"],
        "operation": previewed["operation"],
        "approval_preview": previewed["approval_preview"],
        "approval_digest": previewed["approval_digest"],
        "plan_id": previewed["plan_id"],
        "preflight": previewed["preflight"],
        "applied": bool(applied.get("applied", True)),
        "changed_paths": applied.get("changed_paths", []),
        "index_paths": applied.get("index_paths", []),
        "receipt_removed": applied.get("receipt_removed", False),
        "warnings": applied.get("warnings", []),
        "authorization": applied.get("authorization"),
    }


def reject(args: argparse.Namespace) -> dict[str, Any]:
    repo = decision_cli.vault_root(getattr(args, "vault", None))
    core_cli = _core_cli(args.core_cli) if args.core_cli is not None else None
    if args.receipt_file is not None:
        receipt_path = _receipt_path(args.receipt_file, repo, must_exist=True)
        receipt, receipt_identity, _ = _load_receipt(receipt_path)
    elif args.candidate_id is not None:
        candidate_id = _require_candidate_id(args.candidate_id)
        directory = _default_receipt_dir(repo)
        receipt_path = _receipt_path(directory / f"{candidate_id}.json", repo, must_exist=True)
        receipt, receipt_identity, _ = _load_receipt(receipt_path)
        if receipt["candidate_id"] != candidate_id:
            raise WorkflowError("receipt_invalid", REPREVIEW_MESSAGE, exit_code=5)
    else:
        if core_cli is None:
            raise WorkflowError("usage_invalid", "automatic reject requires --core-cli")
        directory = _default_receipt_dir(repo)
        receipt_path, receipt, receipt_identity = _select_default_receipt(repo, core_cli, directory)

    if receipt["approval_material"]["vault_identity"] != _vault_identity(repo):
        raise WorkflowError("vault_identity_mismatch", REPREVIEW_MESSAGE, exit_code=5)
    if core_cli is not None:
        _require_receipt_binding(receipt, repo, core_cli)
    try:
        _remove_receipt(receipt_path, receipt_identity)
    except OSError as error:
        raise WorkflowError("receipt_cleanup_failed", "workflow receipt could not be rejected", exit_code=5) from error
    return {
        "schema": "context-decision-workflow-reject/v1",
        "candidate_id": receipt["candidate_id"],
        "receipt_file": str(receipt_path),
        "rejected": True,
        "repository_write": False,
    }


def _add_capture_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--host", choices=("codex", "claude-code"), required=True)
    parser.add_argument("--core-cli", help="context-core entrypoint; omit to resolve Bobbin's embedded core.")
    source = parser.add_mutually_exclusive_group()
    source.add_argument("--candidate")
    source.add_argument("--inline", action="store_true")
    parser.add_argument("--attestation")
    parser.add_argument("--candidate-id")
    parser.add_argument("--title")
    parser.add_argument("--summary")
    parser.add_argument("--scope")
    parser.add_argument("--decision-key")
    parser.add_argument("--captured-from", choices=("conversation", "workspace", "manual", "import"))
    parser.add_argument("--commitment-evidence", action="append", default=[])
    parser.add_argument("--sec-decision")
    parser.add_argument("--sec-rationale")
    parser.add_argument("--sec-alternatives", action="append", default=[])
    parser.add_argument("--sec-constraints", action="append", default=[])
    parser.add_argument("--sec-tradeoffs", action="append", default=[])
    parser.add_argument("--sec-revisit", action="append", default=[], help="Revisit condition text; repeat for multiple conditions.")
    parser.add_argument("--revisit-on", help="Calendar date only (YYYY-MM-DD); condition text: --sec-revisit.")
    parser.add_argument("--source-ref", action="append", default=[])
    parser.add_argument("--tag", action="append", default=[])
    parser.add_argument("--search-term", action="append", default=[])
    parser.add_argument("--informed-by", action="append", default=[])
    relation_help = "One ctx_ id per flag; repeat the flag for multiple ids (no comma list)."
    parser.add_argument("--serves-intent", dest="serves_intents", action="append", default=[], help=relation_help)
    parser.add_argument("--informed-by-observation", dest="informed_by_observations", action="append", default=[], help=relation_help)
    parser.add_argument("--informed-by-assumption", dest="informed_by_assumptions", action="append", default=[], help=relation_help)
    parser.add_argument("--affects-document", dest="affects_documents", action="append", default=[], help=relation_help)
    parser.add_argument("--attest-explicit-choice", action="store_true")
    parser.add_argument("--attest-scope-identified", action="store_true")
    parser.add_argument("--attest-commitment-present", action="store_true")
    parser.add_argument("--ack-conflicts", action="append", default=[])
    lifecycle = parser.add_mutually_exclusive_group()
    lifecycle.add_argument("--supersede")
    lifecycle.add_argument("--withdraw")
    parser.add_argument("--reason")
    parser.add_argument("--receipt-file")
    parser.add_argument("--json", action="store_true")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="decision_workflow.py",
        description="Create and apply a frozen, approval-gated DEC capture.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--vault", help="Directory containing context/; defaults to the nearest context/ ancestor or cwd.")
    sub = parser.add_subparsers(dest="command", required=True)
    preview_parser = sub.add_parser(
        "preview",
        description="Build one exact DEC preview and write its transient frozen receipt.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""semantic input:
  --candidate and --attestation require named @file JSON inputs.
  Inline --sec-* values are literals by default; @file reads a named regular UTF-8
  file and @@literal preserves one leading @. Path-like plain text stays literal.

limits:
  DEC decision: 1,200 codepoints; common primary claim: 2,000 codepoints;
  canonical owner input: 8 KiB; full candidate envelope: 16 KiB.

receipt and approval:
  The canonical path creates a mode-0600 receipt below the private mode-0700
  tempdir/context-decision directory. --receipt-file retains the explicit low-level
  path outside the vault. approval_digest binds vault
  identity, pinned core path/SHA, workflow/result digests, and the nested bundle;
  receipt_digest detects damage to the frozen envelope.
""",
    )
    _add_capture_arguments(preview_parser)
    record_parser = sub.add_parser(
        "record",
        description="Preview and apply one DEC in a single call after the user's semantic approval.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""record = preview + apply on the unchanged frozen receipt inside one process.
Pass --approved only after the user's direct, explicit, unconditional approval in
conversation. --core-cli is optional: Bobbin's embedded core is
resolved deterministically and SHA-bound; otherwise the call fails closed with
core_cli_required and the candidate list. The receipt is removed on success unless
--keep-receipt is set. Semantic input and limits are the same as preview.
""",
    )
    _add_capture_arguments(record_parser)
    record_parser.add_argument("--approved", action="store_true", help="Attest the user's direct, explicit, unconditional approval given in conversation.")
    record_parser.add_argument("--keep-receipt", action="store_true")
    record_parser.add_argument("--approval-source", choices=("user", "policy"), default="user")
    record_parser.add_argument("--policy-decision", choices=("record", "ask"))
    record_parser.add_argument("--policy-reason")
    apply_parser = sub.add_parser(
        "apply",
        description="Apply the unchanged frozen receipt after exact user approval.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""With no receipt locator, apply selects only one fresh pending receipt bound to
the current vault identity and core SHA. Zero or multiple matches fail closed.
The agent must forward --approved-digest unchanged from preview stdout; the receipt's
self-digests are not an independent approval channel. --receipt-file remains available
for explicit low-level selection.
Success removes the receipt unless --keep-receipt is set. Cleanup failure reports a
warning after the already successful apply and never retries the repository write.

An explicit receipt remains a mode-0600 sensitive file outside the vault.
approval_digest binds the frozen material; receipt_digest detects damage.
""",
    )
    apply_parser.add_argument("--core-cli", help="context-core entrypoint; omit to resolve Bobbin's embedded core.")
    apply_parser.add_argument("--receipt-file")
    apply_parser.add_argument("--approved-digest", required=True)
    apply_parser.add_argument("--approval-source", choices=("user", "policy"), default="user")
    apply_parser.add_argument("--policy-decision", choices=("record", "ask"))
    apply_parser.add_argument("--policy-reason")
    apply_parser.add_argument("--keep-receipt", action="store_true")
    apply_parser.add_argument("--json", action="store_true")
    reject_parser = sub.add_parser(
        "reject",
        description="Remove one pending frozen receipt without changing repository bytes.",
    )
    reject_parser.add_argument("--core-cli")
    locator = reject_parser.add_mutually_exclusive_group()
    locator.add_argument("--receipt-file")
    locator.add_argument("--candidate-id")
    reject_parser.add_argument("--json", action="store_true")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        with decision_cli._bobbin_settings("project_environment"):
            if args.command == "preview":
                result = preview(args)
            elif args.command == "apply":
                result = apply(args)
            elif args.command == "record":
                result = record(args)
            else:
                result = reject(args)
        _emit({"ok": True, "result": result}, compact=args.json)
        return 0
    except decision_cli.DecisionError as error:
        payload = error.envelope()
        if error.code == "decision_slot_conflict":
            current = error.details.get("current", {})
            title = current.get("title") if isinstance(current, dict) else None
            payload["error"]["message"] = (
                f"A Current decision named '{title}' exists; confirm whether to supersede it."
                if isinstance(title, str) and title
                else "A Current decision exists; confirm whether to supersede it."
            )
        elif error.code == "core_surface_mismatch":
            payload["error"]["message"] = CORE_MISMATCH_MESSAGE
        elif error.code == "precondition_changed":
            payload["error"]["message"] = REPREVIEW_MESSAGE
        _emit(payload, compact=args.json)
        return error.exit_code
    except CoreCommandError as error:
        payload = error.payload
        code = payload.get("error", {}).get("code") if isinstance(payload, dict) else None
        if code == "precondition_changed":
            payload["error"]["message"] = REPREVIEW_MESSAGE
        elif code == "core_surface_mismatch":
            payload["error"]["message"] = CORE_MISMATCH_MESSAGE
        _emit(payload, compact=args.json)
        return error.exit_code
    except WorkflowError as error:
        payload = error.envelope()
        if error.code in {
            "approval_digest_mismatch",
            "core_surface_changed",
            "precondition_changed",
            "receipt_invalid",
            "receipt_selection_ambiguous",
            "receipt_selection_none",
            "receipt_unavailable",
            "vault_identity_mismatch",
        }:
            payload["error"]["message"] = REPREVIEW_MESSAGE
        elif error.code == "core_surface_mismatch":
            payload["error"]["message"] = CORE_MISMATCH_MESSAGE
        _emit(payload, compact=args.json)
        return error.exit_code


if __name__ == "__main__":
    raise SystemExit(main())
