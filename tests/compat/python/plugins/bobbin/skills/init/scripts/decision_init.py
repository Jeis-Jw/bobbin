#!/usr/bin/env python3
from __future__ import annotations

import argparse
import importlib.util
import json
import os
import pathlib
import subprocess
import sys
import tempfile
from typing import Any, Sequence


DECISION_CLI = pathlib.Path(__file__).resolve().parents[2] / "decision/scripts/decision_cli.py"


def _emit(value: dict[str, Any], *, compact: bool) -> None:
    print(
        json.dumps(value, ensure_ascii=False, separators=(",", ":"))
        if compact
        else json.dumps(value, ensure_ascii=False, indent=2)
    )


def _forward(completed: subprocess.CompletedProcess[str]) -> int:
    if completed.stdout:
        sys.stdout.write(completed.stdout)
    if completed.stderr:
        sys.stderr.write(completed.stderr)
    return completed.returncode


def _run(command: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        command,
        env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
        text=True,
        capture_output=True,
    )


def _load_decision_cli():
    spec = importlib.util.spec_from_file_location("context_decision_init_semantic_cli", DECISION_CLI)
    if spec is None or spec.loader is None:
        raise RuntimeError("context-decision semantic CLI could not be loaded")
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


def _result(completed: subprocess.CompletedProcess[str], code: str, message: str) -> dict[str, Any]:
    try:
        payload = json.loads(completed.stdout)
        if completed.returncode == 0 and set(payload) == {"ok", "result"} and payload["ok"] is True and isinstance(payload["result"], dict):
            return payload["result"]
    except (TypeError, json.JSONDecodeError):
        pass
    raise ValueError((code, message))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="decision_init.py",
        description="Initialize context-core storage and the DEC owner with a same-major compatible core CLI.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""core trust:
  Before subprocess execution, --core-cli must have the public entrypoint suffix and
  same-major context-core manifests. The adapter then handshakes schema,
  context-common/v2, required commands, owner-descriptor feature, and doctor state.
  The actual core digest is held constant for the whole init operation.

capture contract (not consumed by init):
  Semantic body values support literal text, @file, and @@literal. DEC decision is
  limited to 1,200 codepoints, common primary claims to 2,000 codepoints, canonical
  owner input to 8 KiB, and the full candidate envelope to 16 KiB. The DEC workflow
  writes its sensitive mode-0600 receipt outside the vault. The agent retains the
  approval_digest internally, and successful apply or reject removes the default receipt;
  users are never asked to locate, enter, or delete either transport detail.
""",
    )
    parser.add_argument("--host", choices=("codex", "claude-code"), required=True)
    parser.add_argument("--core-cli", required=True)
    parser.add_argument("--vault", help="Directory containing context/; defaults to the nearest context/ ancestor or cwd.")
    parser.add_argument("--json", action="store_true")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    decision_cli = _load_decision_cli()
    try:
        vault = decision_cli.vault_root(args.vault)
        core_cli = decision_cli.required_core_surface(args.core_cli)
        core_cli_sha256 = decision_cli.bytes_digest(core_cli.read_bytes())
        schema_command = _run_core(decision_cli, core_cli, "schema", vault=vault, expected_sha256=core_cli_sha256)
        if schema_command.returncode != 0:
            return _forward(schema_command)
        schema = _result(schema_command, "core_handshake_invalid", "context-core schema output is invalid")
        decision_cli.validate_core_schema_handshake(schema, core_cli_value=str(core_cli))
        doctor_command = _run_core(decision_cli, core_cli, "doctor", vault=vault, expected_sha256=core_cli_sha256)
        if doctor_command.returncode != 0:
            return _forward(doctor_command)
        doctor = _result(doctor_command, "core_handshake_invalid", "context-core doctor output is invalid")
        decision_cli.validate_core_doctor_handshake(doctor, allowed_states={"absent", "partial", "invalid", "ready"})
        plan = decision_cli.build_init_plan(
            {
                "host": args.host,
                "observed": {
                    "repository_state": doctor["repository_state"],
                    "entrypoint": str(core_cli),
                    "entrypoint_sha256": core_cli_sha256,
                },
            }
        )
    except decision_cli.DecisionError as error:
        _emit(error.envelope(), compact=args.json)
        return error.exit_code
    except (ValueError, KeyError, TypeError, json.JSONDecodeError):
        _emit(
            {
                "ok": False,
                "error": {
                    "code": "init_plan_invalid",
                    "message": "context-decision init plan output is invalid",
                    "details": {"write_policy": {"repository": "none", "host_configuration": "none"}},
                },
            },
            compact=args.json,
        )
        return 7

    with tempfile.TemporaryDirectory(prefix="context-decision-init-") as temp:
        temp_root = pathlib.Path(temp)
        descriptor = temp_root / "descriptor.json"
        seed = temp_root / "decision.index.md"
        descriptor.write_text(
            json.dumps(plan["owner_descriptor"], ensure_ascii=False, separators=(",", ":")),
            encoding="utf-8",
        )
        seed.write_text(plan["index_seed"], encoding="utf-8")
        completed = _run_core(
            decision_cli,
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
    if completed.returncode != 0:
        return _forward(completed)
    try:
        bootstrap = json.loads(completed.stdout)["result"]
    except (KeyError, TypeError, json.JSONDecodeError):
        _emit(
            {
                "ok": False,
                "error": {
                    "code": "core_bootstrap_result_invalid",
                    "message": "context-core bootstrap output is invalid",
                    "details": {"write_policy": {"host_configuration": "none"}},
                },
            },
            compact=args.json,
        )
        return 7

    _emit(
        {
            "ok": True,
            "result": {
                "schema": "context-decision-init-result/v1",
                "core_repository_state_before": plan["core_repository_state"],
                "preflight": {
                    "required_plugin": plan["required_plugin"],
                    "repository_state": plan["core_repository_state"],
                },
                "phases": bootstrap["phases"],
                "changed_paths": bootstrap["changed_paths"],
                "doctor": bootstrap["doctor"],
                "policy": bootstrap["policy"],
                "bootstrap": bootstrap,
            },
        },
        compact=args.json,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
