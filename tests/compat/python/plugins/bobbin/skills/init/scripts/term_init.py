#!/usr/bin/env python3
"""Explicit TERM initialization adapter; repository bytes are delegated to context-core."""
from __future__ import annotations

import argparse
import importlib.util
import json
import os
import pathlib
import re
import subprocess
import sys
import tempfile
from typing import Any, Callable, Sequence


TERM_CLI = pathlib.Path(__file__).resolve().parents[2] / "term/scripts/term_cli.py"
REQUIRED_FEATURE = "context-owner-descriptor/v2"


def _run(command: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        command,
        env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
        text=True,
        capture_output=True,
    )


def _emit(value: dict[str, Any], compact: bool) -> None:
    print(json.dumps(value, ensure_ascii=False, separators=(",", ":")) if compact else json.dumps(value, ensure_ascii=False, indent=2))


def _forward(completed: subprocess.CompletedProcess[str]) -> int:
    if completed.stdout:
        sys.stdout.write(completed.stdout)
    if completed.stderr:
        sys.stderr.write(completed.stderr)
    return completed.returncode


def _load_term_cli():
    spec = importlib.util.spec_from_file_location("context_term_init_semantic_cli", TERM_CLI)
    if spec is None or spec.loader is None:
        raise RuntimeError("context-term semantic CLI could not be loaded")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _run_core(
    module,
    core_cli: pathlib.Path,
    *arguments: str,
    vault: pathlib.Path | None = None,
    expected_sha256: str | None = None,
) -> subprocess.CompletedProcess[str]:
    module.required_core_surface(str(core_cli), expected_sha256=expected_sha256)
    vault_arguments = ["--vault", str(vault)] if vault is not None and arguments[0] not in {"schema", "capabilities"} else []
    return _run([sys.executable, str(core_cli), *vault_arguments, *arguments, "--json"])


def _result(completed: subprocess.CompletedProcess[str]) -> dict[str, Any]:
    payload = json.loads(completed.stdout)
    if set(payload) != {"ok", "result"} or payload.get("ok") is not True or not isinstance(payload.get("result"), dict):
        raise ValueError("public core result envelope is invalid")
    return payload["result"]


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="term_init.py",
        description="Initialize context-core storage and the experimental TERM owner with a same-major compatible core CLI.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""core trust:
  Before subprocess execution, --core-cli must have the public entrypoint suffix and
  same-major context-core manifests. The adapter then handshakes schema,
  context-common/v2, required commands, owner-descriptor feature, and doctor state.
  The actual core digest is held constant for the whole init operation.

semantic input contract (outside init):
  TERM claim and decline receive structured candidate JSON through --candidate @file.
  Common primary claims and TERM definitions are limited to 2,000 codepoints, canonical
  owner input to 8 KiB, and the full candidate envelope to 16 KiB.
""",
    )
    parser.add_argument("--host", choices=("codex", "claude-code"), required=True)
    parser.add_argument("--core-cli", required=True)
    parser.add_argument("--vault", help="Directory containing context/; defaults to the nearest context/ ancestor or cwd.")
    parser.add_argument("--json", action="store_true")
    return parser


def _error(code: str, message: str, *, compact: bool, details: dict[str, Any] | None = None) -> int:
    _emit({"ok": False, "error": {"code": code, "message": message, "details": details or {}}}, compact)
    return 5


def _postcondition(
    repo: pathlib.Path,
    plan: dict[str, Any],
    doctor: dict[str, Any],
    validate_doctor: Callable[[Any], dict[str, Any]],
) -> dict[str, Any]:
    doctor = validate_doctor(doctor)
    if (
        doctor.get("repository_state") != "ready"
        or doctor.get("issues") != []
    ):
        raise ValueError("core doctor is not exact ready")
    descriptor = plan["owner_descriptor"]
    descriptor_line = json.dumps(descriptor, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    index_path = repo / "context/term/term.index.md"
    root_path = repo / "context/context.index.md"
    for path in (repo / "context", repo / "context/term", index_path, root_path):
        if path.is_symlink():
            raise ValueError("registered context path is a symlink")
    if not index_path.is_file() or not root_path.is_file():
        raise ValueError("registered index bytes are missing")
    index_text = index_path.read_text(encoding="utf-8")
    begin = "<!-- BEGIN CONTEXT GENERATED:owner-profile -->"
    end = "<!-- END CONTEXT GENERATED:owner-profile -->"
    if index_text.count(begin) != 1 or index_text.count(end) != 1:
        raise ValueError("area owner profile block is missing")
    profile_lines = [line for line in index_text.split(begin, 1)[1].split(end, 1)[0].strip("\n").splitlines() if line]
    if profile_lines != [descriptor_line]:
        raise ValueError("area owner profile differs from init descriptor")
    required_index_tokens = (
        'area: "term"',
        'owner: "context-term"',
        'artifact_schema: "context-term/v1"',
        'authority: "authoritative"',
    )
    if any(index_text.count(token) != 1 for token in required_index_tokens):
        raise ValueError("area index identity differs from init descriptor")
    root_text = root_path.read_text(encoding="utf-8")
    profile_value = {
        "area": "term",
        "descriptor_schema": "context-owner-descriptor/v2",
        "descriptor_digest": plan["descriptor_digest"],
    }
    profile_row = "<!-- context-owner-profile " + json.dumps(profile_value, ensure_ascii=False, separators=(",", ":")) + " -->"
    if root_text.count(profile_row) != 1:
        raise ValueError("root owner-profile registry differs from init descriptor")
    area_rows = []
    for match in re.finditer(r"<!-- context-area (\{.*?\}) -->", root_text):
        try:
            value = json.loads(match.group(1))
        except json.JSONDecodeError as error:
            raise ValueError("root area row is malformed") from error
        if value.get("area") == "term":
            area_rows.append(value)
    expected_area = {
        "area": "term",
        "path": "context/term/term.index.md",
        "owner": "context-term",
        "claims": ["term"],
        "artifact_schema": "context-term/v1",
        "authority": "authoritative",
    }
    if area_rows != [expected_area]:
        raise ValueError("root area registry differs from init descriptor")
    return {
        "doctor": "ready",
        "root_registry": "exact",
        "area_descriptor": "exact",
        "index_path": "context/term/term.index.md",
    }


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    term_cli = _load_term_cli()
    try:
        vault = term_cli.vault_root(args.vault)
        core_cli = term_cli.required_core_surface(args.core_cli)
        core_cli_sha256 = term_cli.bytes_digest(core_cli.read_bytes())
        handshake = _run_core(term_cli, core_cli, "schema", vault=vault, expected_sha256=core_cli_sha256)
        if handshake.returncode:
            return _forward(handshake)
        term_cli.validate_core_schema_handshake(_result(handshake), core_cli_value=str(core_cli))
        doctor_before_command = _run_core(term_cli, core_cli, "doctor", vault=vault, expected_sha256=core_cli_sha256)
        if doctor_before_command.returncode:
            return _forward(doctor_before_command)
        doctor_before = term_cli.validate_core_doctor(_result(doctor_before_command))
        plan = term_cli.build_init_plan(
            {
                "host": args.host,
                "observed": {
                    "repository_state": doctor_before["repository_state"],
                    "entrypoint": str(core_cli),
                    "entrypoint_sha256": core_cli_sha256,
                },
            }
        )
    except term_cli.TermError as error:
        _emit(error.envelope(), args.json)
        return error.exit_code
    except (KeyError, TypeError, json.JSONDecodeError, OSError, RuntimeError, ValueError) as error:
        return _error("core_handshake_invalid", "context-core init handshake is invalid", compact=args.json, details={"reason": str(error), "write_policy": {"repository": "none", "host_configuration": "none"}})

    with tempfile.TemporaryDirectory(prefix="context-term-init-") as temporary:
        temp_root = pathlib.Path(temporary)
        descriptor = temp_root / "descriptor.json"
        seed = temp_root / "term.index.md"
        descriptor.write_text(json.dumps(plan["owner_descriptor"], ensure_ascii=False, sort_keys=True, separators=(",", ":")), encoding="utf-8")
        seed.write_text(plan["index_seed"], encoding="utf-8")
        completed = _run_core(
            term_cli,
            core_cli,
            "bootstrap",
            "--descriptor",
            f"@{descriptor}",
            "--index-seed",
            f"@{seed}",
            "--host",
            args.host,
            vault=vault,
            expected_sha256=core_cli_sha256,
        )
    if completed.returncode:
        return _forward(completed)
    try:
        bootstrap = json.loads(completed.stdout)["result"]
    except (KeyError, TypeError, json.JSONDecodeError):
        return _error("core_bootstrap_result_invalid", "context-core bootstrap result is invalid", compact=args.json)
    post_doctor = _run_core(term_cli, core_cli, "doctor", vault=vault, expected_sha256=core_cli_sha256)
    if post_doctor.returncode:
        return _forward(post_doctor)
    try:
        doctor = json.loads(post_doctor.stdout)["result"]
        postcondition = _postcondition(vault, plan, doctor, term_cli.validate_core_doctor)
    except (term_cli.TermError, KeyError, TypeError, json.JSONDecodeError, OSError, UnicodeError, ValueError) as error:
        return _error(
            "core_bootstrap_postcondition_invalid",
            "context-core reported bootstrap success without exact TERM registration",
            compact=args.json,
            details={"reason": str(error), "required_feature": REQUIRED_FEATURE},
        )
    _emit(
        {
            "ok": True,
            "result": {
                "schema": "context-term-init-result/v1",
                "required_feature": REQUIRED_FEATURE,
                "descriptor_digest": plan["descriptor_digest"],
                "core_repository_state_before": plan["core_repository_state"],
                "changed_paths": bootstrap["changed_paths"],
                "phases": bootstrap["phases"],
                "doctor": bootstrap["doctor"],
                "policy": bootstrap["policy"],
                "postcondition": postcondition,
                "bootstrap": bootstrap,
            },
        },
        args.json,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
