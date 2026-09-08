#!/usr/bin/env python3
from __future__ import annotations

import argparse
import importlib.util
import json
import pathlib
import sys
from typing import Any, Sequence

import assumption_cli


class WorkflowLoadError(Exception):
    def __init__(self, details: dict[str, Any]):
        super().__init__("compatible context-core workflow helper is unavailable")
        self.details = details
        self.exit_code = 5

    def envelope(self) -> dict[str, Any]:
        return {
            "ok": False,
            "error": {
                "code": "core_incompatible",
                "message": "context-core does not provide the ASM inline workflow; choose a listed candidate and start a new session",
                "details": self.details,
            },
        }


def _workflow(core_cli_value: str):
    core_cli = assumption_cli.required_core_surface(core_cli_value)
    helper = core_cli.with_name("owner_workflow.py")
    if not helper.is_file():
        finder = getattr(assumption_cli, "compatible_core_candidates", None)
        candidates = finder(core_cli_value, minimum_version="1.0.0") if callable(finder) else []
        raise WorkflowLoadError({
            "required_feature": "owner-inline-workflow/v1",
            "compatible_core_candidates": candidates,
            "candidate_policy": "diagnostic_only_no_automatic_substitution",
        })
    spec = importlib.util.spec_from_file_location("context_assumption_owner_workflow", helper)
    if spec is None or spec.loader is None:
        raise WorkflowLoadError({"required_feature": "owner-inline-workflow/v1"})
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="assumption_workflow.py")
    sub = parser.add_subparsers(dest="command", required=True)
    preview = sub.add_parser("preview", description="Build one inline ASM preview; no vault artifact is recorded.")
    preview.add_argument("--host", choices=("codex", "claude-code"), required=True)
    preview.add_argument("--core-cli", default=str(pathlib.Path(__file__).resolve().parents[3] / "skills/context/scripts/context_cli.py"))
    preview.add_argument("--vault")
    preview.add_argument("--inline", action="store_true")
    preview.add_argument("--title", required=True)
    preview.add_argument("--summary", required=True)
    preview.add_argument("--scope", required=True)
    preview.add_argument("--sec-assumption", required=True)
    preview.add_argument("--sec-basis", action="append", required=True)
    preview.add_argument("--sec-confirm", action="append", default=[])
    preview.add_argument("--sec-refute", action="append", default=[])
    preview.add_argument("--impacted-decision", action="append", default=[])
    preview.add_argument("--captured-from", choices=("conversation", "workspace", "manual", "import"), default="conversation")
    preview.add_argument("--source-ref", action="append", default=[])
    preview.add_argument("--tag", action="append", default=[])
    preview.add_argument("--search-term", action="append", default=[])
    preview.add_argument("--filename")
    preview.add_argument("--receipt-file")
    preview.add_argument("--attest-assumption-present", action="store_true", required=True)
    preview.add_argument("--attest-unverified-ok", action="store_true", required=True)
    preview.add_argument("--json", action="store_true")

    apply = sub.add_parser("apply", description="Apply one unchanged, user- or policy-authorized ASM preview.")
    apply.add_argument("--core-cli", default=str(pathlib.Path(__file__).resolve().parents[3] / "skills/context/scripts/context_cli.py"))
    apply.add_argument("--vault")
    apply.add_argument("--receipt-file", required=True)
    apply.add_argument("--approved-digest", required=True)
    apply.add_argument("--approval-source", choices=("user", "policy"), default="user")
    apply.add_argument("--policy-decision", choices=("record", "ask"))
    apply.add_argument("--policy-reason")
    apply.add_argument("--json", action="store_true")
    return parser


def dispatch(args: argparse.Namespace) -> dict[str, Any]:
    workflow = _workflow(args.core_cli)
    if args.command == "apply":
        return workflow.apply(
            assumption_cli,
            owner="context-assumption",
            core_cli_value=args.core_cli,
            vault_value=args.vault,
            receipt_file=args.receipt_file,
            approved_digest=args.approved_digest,
            approval_source=args.approval_source,
            policy_decision=args.policy_decision,
            policy_reason=args.policy_reason,
        )
    assumption = workflow.load_body_argument(args.sec_assumption)
    owner_inputs: dict[str, Any] = {
        "assumption": assumption,
        "basis": [workflow.load_body_argument(value) for value in args.sec_basis],
        "unverified_ok": True,
    }
    for field, values in (
        ("confirm_conditions", args.sec_confirm),
        ("refute_conditions", args.sec_refute),
        ("impacted_decisions", args.impacted_decision),
    ):
        if values:
            owner_inputs[field] = [workflow.load_body_argument(value) for value in values]
    return workflow.preview(
        assumption_cli,
        owner="context-assumption",
        kind="assumption",
        host=args.host,
        core_cli_value=args.core_cli,
        vault_value=args.vault,
        inline=args.inline,
        title=args.title,
        summary=args.summary,
        claim=assumption,
        scope=args.scope,
        owner_inputs=owner_inputs,
        assertions=(
            ("assumption_present", ("/owner_inputs/assumption/assumption",)),
            ("unverified_ok", ("/owner_inputs/assumption/unverified_ok",)),
        ),
        captured_from=args.captured_from,
        tags=args.tag,
        search_terms=args.search_term,
        source_refs=args.source_ref,
        filename=args.filename,
        receipt_file=args.receipt_file,
    )


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    compact = bool(getattr(args, "json", False))
    try:
        with assumption_cli._bobbin_settings("project_environment"):
            result = dispatch(args)
        envelope = {"ok": True, "result": result}
        print(json.dumps(envelope, ensure_ascii=False, separators=(",", ":")) if compact else json.dumps(envelope, ensure_ascii=False, indent=2))
        return 0
    except (assumption_cli.AssumptionError, WorkflowLoadError) as error:
        envelope = error.envelope()
        print(assumption_cli.canonical_json(envelope) if compact else json.dumps(envelope, ensure_ascii=False, indent=2))
        return error.exit_code
    except Exception as error:
        if hasattr(error, "envelope") and hasattr(error, "exit_code"):
            envelope = error.envelope()
            print(assumption_cli.canonical_json(envelope) if compact else json.dumps(envelope, ensure_ascii=False, indent=2))
            return int(error.exit_code)
        raise


if __name__ == "__main__":
    raise SystemExit(main())
