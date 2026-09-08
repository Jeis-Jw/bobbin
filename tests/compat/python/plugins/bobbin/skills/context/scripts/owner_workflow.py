#!/usr/bin/env python3
"""Shared transport for one-command semantic-owner preview and apply workflows."""
from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import pathlib
import stat
import sys
import uuid
from typing import Any, Sequence


WORKFLOW_SCHEMA = "context-owner-inline-workflow/v1"
CORE_HELPER_FEATURE = "owner-inline-workflow/v1"
MAX_BODY_BYTES = 64 * 1024


class OwnerWorkflowError(Exception):
    def __init__(
        self,
        code: str,
        message: str,
        details: dict[str, Any] | None = None,
        exit_code: int = 5,
    ):
        super().__init__(message)
        self.code = code
        self.message = message
        self.details = details or {}
        self.exit_code = exit_code

    def envelope(self) -> dict[str, Any]:
        return {
            "ok": False,
            "error": {"code": self.code, "message": self.message, "details": self.details},
        }


def emit(value: dict[str, Any], *, compact: bool) -> None:
    print(
        json.dumps(value, ensure_ascii=False, separators=(",", ":"))
        if compact
        else json.dumps(value, ensure_ascii=False, indent=2)
    )


def load_body_argument(value: str) -> str:
    if value.startswith("@@"):
        return value[1:]
    if value == "@-":
        stream = getattr(sys.stdin, "buffer", None)
        payload = stream.read(MAX_BODY_BYTES + 1) if stream is not None else sys.stdin.read(MAX_BODY_BYTES + 1).encode("utf-8")
        if len(payload) > MAX_BODY_BYTES:
            raise OwnerWorkflowError(
                "input_too_large",
                "inline body input exceeds 64 KiB",
                {"path": "stdin", "maximum_bytes": MAX_BODY_BYTES},
            )
        try:
            return payload.decode("utf-8")
        except UnicodeError as error:
            raise OwnerWorkflowError(
                "input_unavailable",
                "inline body input is not valid UTF-8",
                {"path": "stdin"},
                3,
            ) from error
    if not value.startswith("@"):
        return value
    path = pathlib.Path(value[1:])
    descriptor: int | None = None
    try:
        metadata = os.lstat(path)
        if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISREG(metadata.st_mode):
            raise OSError("body input is not a regular file")
        if metadata.st_size > MAX_BODY_BYTES:
            raise OwnerWorkflowError(
                "input_too_large",
                "inline body input exceeds 64 KiB",
                {"path": str(path), "maximum_bytes": MAX_BODY_BYTES},
            )
        flags = os.O_RDONLY
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        descriptor = os.open(path, flags)
        payload = os.read(descriptor, MAX_BODY_BYTES + 1)
        if len(payload) > MAX_BODY_BYTES:
            raise OwnerWorkflowError(
                "input_too_large",
                "inline body input exceeds 64 KiB",
                {"path": str(path), "maximum_bytes": MAX_BODY_BYTES},
            )
        return payload.decode("utf-8")
    except OwnerWorkflowError:
        raise
    except (OSError, UnicodeError) as error:
        raise OwnerWorkflowError(
            "input_unavailable",
            "inline body input is unavailable or not valid UTF-8",
            {"path": str(path)},
            3,
        ) from error
    finally:
        if descriptor is not None:
            os.close(descriptor)


def _load_core(core_cli: pathlib.Path):
    module_name = "context_core_inline_workflow_" + hashlib.sha256(str(core_cli).encode("utf-8")).hexdigest()[:16]
    loaded = sys.modules.get(module_name)
    if loaded is not None:
        return loaded
    spec = importlib.util.spec_from_file_location(module_name, core_cli)
    if spec is None or spec.loader is None:
        raise OwnerWorkflowError("core_surface_unavailable", "context-core runtime could not be loaded")
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    try:
        spec.loader.exec_module(module)
    except Exception as error:
        sys.modules.pop(module_name, None)
        raise OwnerWorkflowError(
            "core_surface_unavailable",
            "context-core runtime could not be loaded",
            {"path": str(core_cli)},
        ) from error
    return module


def _translate_core_error(error: Exception) -> OwnerWorkflowError:
    return OwnerWorkflowError(
        str(getattr(error, "code", "core_workflow_failed")),
        str(getattr(error, "message", "context-core workflow failed")),
        dict(getattr(error, "details", {}) or {}),
        int(getattr(error, "exit_code", 5)),
    )


def _core_surface(owner_cli: Any, value: str) -> tuple[pathlib.Path, str, Any]:
    core_cli = owner_cli.required_core_surface(value)
    before_digest = owner_cli.bytes_digest(core_cli.read_bytes())
    core = _load_core(core_cli)
    try:
        schema = core.schema_result()
        owner_cli.validate_core_schema_handshake(schema, core_cli_value=str(core_cli))
    except getattr(core, "ContextError", Exception) as error:
        raise _translate_core_error(error) from error
    features = schema.get("features") if isinstance(schema, dict) else None
    if not isinstance(features, list) or CORE_HELPER_FEATURE not in features:
        candidates = []
        finder = getattr(owner_cli, "compatible_core_candidates", None)
        if callable(finder):
            candidates = finder(value, minimum_version="1.0.0")
        raise OwnerWorkflowError(
            "core_incompatible",
            "context-core does not provide the inline owner workflow transport",
            {
                "required_feature": CORE_HELPER_FEATURE,
                "compatible_core_candidates": candidates,
                "candidate_policy": "diagnostic_only_no_automatic_substitution",
            },
        )
    return core_cli, before_digest, core


def _doctor(owner_cli: Any, core: Any, vault: pathlib.Path) -> dict[str, Any]:
    try:
        doctor = core.doctor_repository(vault)
        owner_cli.validate_core_doctor(doctor)
    except getattr(core, "ContextError", Exception) as error:
        raise _translate_core_error(error) from error
    if doctor.get("repository_state") != "ready" or doctor.get("issues"):
        raise OwnerWorkflowError(
            "core_not_ready",
            "context-core doctor must be ready before owner preview",
            {
                "repository_state": doctor.get("repository_state"),
                "issues": doctor.get("issues"),
                "warnings": doctor.get("warnings"),
            },
        )
    return doctor


def _candidate(
    *,
    kind: str,
    title: str,
    summary: str,
    claim: str,
    captured_from: str,
    scope: str,
    owner_inputs: dict[str, Any],
    evidence: Sequence[str] = (),
    tags: Sequence[str] = (),
    search_terms: Sequence[str] = (),
    source_refs: Sequence[str] = (),
) -> dict[str, Any]:
    value: dict[str, Any] = {
        "schema": "context-capture-candidate/v1",
        "candidate_id": "cand_" + uuid.uuid4().hex,
        "title": title,
        "claim": claim,
        "summary": summary,
        "captured_from": captured_from,
        "requested_kind": kind,
        "specialized_kinds": [kind],
        "fallback_kind": None,
        "scope_hint": scope,
        "owner_inputs": {kind: owner_inputs},
    }
    for field, items in (
        ("evidence", evidence),
        ("tags", tags),
        ("search_terms", search_terms),
        ("source_refs", source_refs),
    ):
        if items:
            value[field] = list(items)
    return value


def _attestation(owner_cli: Any, candidate: dict[str, Any], assertions: Sequence[tuple[str, Sequence[str]]]) -> dict[str, Any]:
    return {
        "schema": "context-semantic-attestation/v1",
        "operation": "claim",
        "input_schema": candidate["schema"],
        "input_digest": owner_cli.canonical_digest(candidate),
        "assertions": [
            {"name": name, "value": True, "evidence_pointers": list(pointers)}
            for name, pointers in assertions
        ],
    }


def preview(
    owner_cli: Any,
    *,
    owner: str,
    kind: str,
    host: str,
    core_cli_value: str,
    vault_value: str | None,
    inline: bool,
    title: str,
    summary: str,
    claim: str,
    scope: str,
    owner_inputs: dict[str, Any],
    assertions: Sequence[tuple[str, Sequence[str]]],
    captured_from: str = "conversation",
    evidence: Sequence[str] = (),
    tags: Sequence[str] = (),
    search_terms: Sequence[str] = (),
    source_refs: Sequence[str] = (),
    filename: str | None = None,
    receipt_file: str | None = None,
) -> dict[str, Any]:
    if not inline:
        raise OwnerWorkflowError("usage_invalid", "capture preview requires --inline", exit_code=2)
    vault = owner_cli.vault_root(vault_value)
    core_cli, core_digest, core = _core_surface(owner_cli, core_cli_value)
    doctor = _doctor(owner_cli, core, vault)
    candidate = _candidate(
        kind=kind,
        title=title,
        summary=summary,
        claim=claim,
        captured_from=captured_from,
        scope=scope,
        owner_inputs=owner_inputs,
        evidence=evidence,
        tags=tags,
        search_terms=search_terms,
        source_refs=source_refs,
    )
    attestation = _attestation(owner_cli, candidate, assertions)
    owner_result = owner_cli.build_claim_result(candidate, attestation, filename=filename)
    owner_validation = owner_cli.validate_batch(vault, owner_result)
    if owner_cli.bytes_digest(core_cli.read_bytes()) != core_digest:
        raise OwnerWorkflowError(
            "core_surface_changed",
            "context-core entrypoint changed during the preview operation",
            {"path": str(core_cli)},
        )
    try:
        core_preview = core.finalize_owner_result(vault, owner_result, owner_validation)
        frozen = core.freeze_bundle_receipt(vault, core_preview, receipt_file)
    except getattr(core, "ContextError", Exception) as error:
        raise _translate_core_error(error) from error
    return {
        "schema": WORKFLOW_SCHEMA,
        "owner": owner,
        "operation": "preview",
        "host": host,
        "preflight": {
            "repository_state": doctor["repository_state"],
            "core_version": doctor["plugin_version"],
            "core_entrypoint": str(core_cli),
            "inventory": "derived_from_verified_manifests",
            "doctor": "verified_directly",
        },
        **frozen,
    }


def apply(
    owner_cli: Any,
    *,
    owner: str,
    core_cli_value: str,
    vault_value: str | None,
    receipt_file: str,
    approved_digest: str,
    approval_source: str = "user",
    policy_decision: str | None = None,
    policy_reason: str | None = None,
) -> dict[str, Any]:
    vault = owner_cli.vault_root(vault_value)
    core_cli, core_digest, core = _core_surface(owner_cli, core_cli_value)
    if owner_cli.bytes_digest(core_cli.read_bytes()) != core_digest:
        raise OwnerWorkflowError(
            "core_surface_changed",
            "context-core entrypoint changed during the apply operation",
            {"path": str(core_cli)},
        )
    try:
        result = core.apply_receipt(vault, receipt_file, approved_digest, approval_source=approval_source,
                                    policy_decision=policy_decision, policy_reason=policy_reason)
    except getattr(core, "ContextError", Exception) as error:
        raise _translate_core_error(error) from error
    return {
        "schema": WORKFLOW_SCHEMA,
        "owner": owner,
        "operation": "apply",
        "state": "applied" if result.get("applied") else "not_applied",
        **result,
    }
