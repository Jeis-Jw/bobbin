from __future__ import annotations

import hashlib
import importlib.util
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = next(p for p in Path(__file__).resolve().parents if (p / "pytest.ini").is_file())
CORE_CLI = ROOT / "plugins/bobbin/skills/context/scripts/context_cli.py"
DECISION_WORKFLOW = ROOT / "plugins/bobbin/skills/decision/scripts/decision_workflow.py"


def load(name: str, relative: str):
    path = ROOT / relative
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


core_cli = load("context_core_typed_relation_flow", "plugins/bobbin/skills/context/scripts/context_cli.py")
intent_cli = load("context_intent_typed_relation_flow", "plugins/bobbin/skills/intent/scripts/intent_cli.py")
document_cli = load("context_document_typed_relation_flow", "plugins/bobbin/skills/document/scripts/document_cli.py")
decision_cli = load("context_decision_typed_relation_flow", "plugins/bobbin/skills/decision/scripts/decision_cli.py")


def tree_digest(root: Path) -> str:
    digest = hashlib.sha256()
    for path in sorted(item for item in root.rglob("*") if item.is_file()):
        digest.update(path.relative_to(root).as_posix().encode("utf-8"))
        digest.update(path.read_bytes())
    return digest.hexdigest()


def run_workflow(repo: Path, *arguments: str) -> dict:
    completed = subprocess.run(
        [sys.executable, str(DECISION_WORKFLOW), "--vault", str(repo), *arguments, "--json"],
        cwd=repo,
        text=True,
        capture_output=True,
    )
    if completed.returncode != 0:
        raise AssertionError(completed.stdout + completed.stderr)
    return json.loads(completed.stdout)["result"]


def apply_result(repo: Path, owner, result: dict) -> dict:
    receipt = owner.validate_batch(repo, result)
    preview = core_cli.finalize_owner_result(repo, result, receipt)
    return core_cli.apply_bundle(repo, preview["bundle"], preview["approval_digest"])


def claim_attestation(owner, value: dict, assertions: list[tuple[str, list[str]]]) -> dict:
    return {
        "schema": "context-semantic-attestation/v1",
        "operation": "claim",
        "input_schema": value["schema"],
        "input_digest": owner.canonical_digest(value),
        "assertions": [
            {"name": name, "value": True, "evidence_pointers": pointers}
            for name, pointers in assertions
        ],
    }


def intent_candidate() -> dict:
    claim = "배포 전에 사용자와 운영자가 변경 영향을 이해할 수 있게 한다."
    return {
        "schema": "context-capture-candidate/v1",
        "candidate_id": "cand_550e8400e29b41d4a716446655440040",
        "title": "변경 영향 가시성",
        "claim": claim,
        "summary": "릴리스 전에 변경 영향을 이해하는 desired direction이다.",
        "captured_from": "conversation",
        "requested_kind": "intent",
        "specialized_kinds": ["intent"],
        "fallback_kind": None,
        "scope_hint": "product/release",
        "tags": [],
        "search_terms": ["change", "visibility"],
        "source_refs": [],
        "owner_inputs": {"intent": {"intent": claim, "intent_key": "change-visibility"}},
    }


def document_candidate() -> dict:
    claim = "릴리스 전에 변경 범위, 운영 영향, 복구 절차를 검토한다."
    return {
        "schema": "context-capture-candidate/v1",
        "candidate_id": "cand_550e8400e29b41d4a716446655440041",
        "title": "릴리스 운영 문서",
        "claim": claim,
        "summary": "릴리스 운영 절차의 living document다.",
        "captured_from": "conversation",
        "requested_kind": "document",
        "specialized_kinds": ["document"],
        "fallback_kind": None,
        "scope_hint": "product/release",
        "tags": [],
        "search_terms": ["release", "operations"],
        "source_refs": [],
        "owner_inputs": {"document": {"document_key": "release-operations", "content": claim}},
    }


def decision_candidate(*, key: str, serves: str, affects: str) -> dict:
    claim = "배포 전에 변경 영향 보고서를 승인 절차에 포함한다."
    return {
        "schema": "context-capture-candidate/v1",
        "candidate_id": "cand_550e8400e29b41d4a716446655440042" if key == "impact-review" else "cand_550e8400e29b41d4a716446655440043",
        "title": f"변경 영향 검토 {key}",
        "claim": claim,
        "summary": "변경 영향 검토를 현재 릴리스 선택으로 고정한다.",
        "captured_from": "conversation",
        "requested_kind": "decision",
        "specialized_kinds": ["decision"],
        "fallback_kind": None,
        "scope_hint": "product/release",
        "tags": ["release"],
        "search_terms": ["impact", "approval"],
        "source_refs": ["conversation:test"],
        "evidence": ["릴리스 책임자가 현재 따를 선택으로 확정했다."],
        "owner_inputs": {
            "decision": {
                "decision": claim,
                "rationale": "측정된 변경 영향과 목표를 같은 승인 지점에서 검토한다.",
                "rejected_alternatives": ["검토 생략: 운영 위험이 커서 반려"],
                "decision_key": key,
                "serves_intents": [serves],
                "affects_documents": [affects],
            }
        },
    }


class TypedRelationFlowTests(unittest.TestCase):
    def test_decision_typed_edges_connect_independent_owner_artifacts(self) -> None:
        intent_id = "ctx_550e8400e29b41d4a716446655440040"
        document_id = "ctx_550e8400e29b41d4a716446655440041"
        with tempfile.TemporaryDirectory() as temp:
            repo = Path(temp) / "vault"
            repo.mkdir()
            core_cli.bootstrap_repository(repo, intent_cli.owner_descriptor(), intent_cli.intent_index_seed(), host="codex")
            core_cli.bootstrap_repository(repo, document_cli.owner_descriptor(), document_cli.document_index_seed(), host="codex")
            decision_plan = decision_cli.build_init_plan()
            core_cli.bootstrap_repository(repo, decision_plan["owner_descriptor"], decision_plan["index_seed"], host="codex")

            intent = intent_candidate()
            intent_result = intent_cli.build_claim_result(
                intent,
                claim_attestation(intent_cli, intent, [
                    ("intent_present", ["/owner_inputs/intent/intent"]),
                    ("desired_direction", ["/owner_inputs/intent/intent"]),
                ]),
                identifier=intent_id,
                created_at="2026-09-01T01:00:00+09:00",
            )
            apply_result(repo, intent_cli, intent_result)

            document = document_candidate()
            document_result = document_cli.build_claim_result(
                document,
                claim_attestation(document_cli, document, [
                    ("content_present", ["/owner_inputs/document/content"]),
                    ("living_document", ["/owner_inputs/document/document_key", "/owner_inputs/document/content"]),
                ]),
                identifier=document_id,
                created_at="2026-09-01T01:01:00+09:00",
            )
            apply_result(repo, document_cli, document_result)

            receipt = Path(temp) / "decision-receipt.json"
            before = tree_digest(repo)
            preview = run_workflow(
                repo,
                "preview",
                "--host", "codex",
                "--core-cli", str(CORE_CLI),
                "--inline",
                "--title", "변경 영향 검토 impact-review",
                "--summary", "변경 영향 검토를 현재 릴리스 선택으로 고정한다.",
                "--scope", "product/release",
                "--decision-key", "impact-review",
                "--commitment-evidence", "릴리스 책임자가 현재 따를 선택으로 확정했다.",
                "--sec-decision", "배포 전에 변경 영향 보고서를 승인 절차에 포함한다.",
                "--sec-rationale", "측정된 변경 영향과 목표를 같은 승인 지점에서 검토한다.",
                "--sec-alternatives", "검토 생략: 운영 위험이 커서 반려",
                "--serves-intent", intent_id,
                "--affects-document", document_id,
                "--attest-explicit-choice",
                "--attest-scope-identified",
                "--attest-commitment-present",
                "--receipt-file", str(receipt),
            )
            self.assertEqual(before, tree_digest(repo), "workflow preview must not write vault bytes")
            run_workflow(
                repo,
                "apply",
                "--core-cli", str(CORE_CLI),
                "--receipt-file", str(receipt),
                "--approved-digest", preview["approval_digest"],
            )
            record = next(
                item
                for item in decision_cli.current_state(repo).values()
                if item["frontmatter"]["decision_key"] == "impact-review"
            )
            frontmatter, _ = decision_cli.parse_document((repo / record["path"]).read_text(encoding="utf-8"))
            self.assertEqual(
                {"serves:intent": [intent_id], "affects:document": [document_id]},
                frontmatter["relations"],
            )

            wrong = decision_candidate(key="wrong-kind", serves=document_id, affects=document_id)
            wrong_result = decision_cli.build_claim_result(
                wrong,
                claim_attestation(decision_cli, wrong, [
                    ("explicit_choice", ["/owner_inputs/decision/decision"]),
                    ("scope_identified", ["/scope_hint"]),
                    ("commitment_present", ["/evidence/0"]),
                ]),
                identifier="ctx_550e8400e29b41d4a716446655440043",
                created_at="2026-09-01T01:03:00+09:00",
                repo=repo,
            )
            receipt = decision_cli.validate_batch(repo, wrong_result)
            before = tree_digest(repo)
            with self.assertRaises(core_cli.ContextError) as caught:
                core_cli.finalize_owner_result(repo, wrong_result, receipt)
            self.assertEqual("typed_relation_kind_mismatch", caught.exception.code)
            self.assertEqual(before, tree_digest(repo))


if __name__ == "__main__":
    unittest.main()
