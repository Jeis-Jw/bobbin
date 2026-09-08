#!/usr/bin/env python3
from __future__ import annotations

import importlib.util
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = next(p for p in Path(__file__).resolve().parents if (p / "pytest.ini").is_file())


def load(name: str, relative: str):
    path = ROOT / relative
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


core_cli = load("context_core_document_freshness", "plugins/bobbin/skills/context/scripts/context_cli.py")
document_cli = load("context_document_freshness", "plugins/bobbin/skills/document/scripts/document_cli.py")
decision_cli = load("context_decision_document_freshness", "plugins/bobbin/skills/decision/scripts/decision_cli.py")


def claim_attestation(owner, candidate: dict, assertions: list[tuple[str, list[str]]]) -> dict:
    return {
        "schema": "context-semantic-attestation/v1",
        "operation": "claim",
        "input_schema": candidate["schema"],
        "input_digest": owner.canonical_digest(candidate),
        "assertions": [
            {"name": name, "value": True, "evidence_pointers": pointers}
            for name, pointers in assertions
        ],
    }


def apply_result(repo: Path, owner, result: dict) -> dict:
    validation = owner.validate_batch(repo, result)
    preview = core_cli.finalize_owner_result(repo, result, validation)
    return core_cli.apply_bundle(repo, preview["bundle"], preview["approval_digest"])


class DocumentFreshnessTests(unittest.TestCase):
    def test_refresh_warns_for_newer_affecting_decision_until_document_update(self) -> None:
        document_id = "ctx_550e8400e29b41d4a716446655440080"
        decision_id = "ctx_550e8400e29b41d4a716446655440081"
        with tempfile.TemporaryDirectory() as temp:
            repo = Path(temp) / "vault"
            repo.mkdir()
            core_cli.bootstrap_repository(
                repo,
                document_cli.owner_descriptor(),
                document_cli.document_index_seed(),
                host="codex",
            )
            decision_init = decision_cli.build_init_plan()
            core_cli.bootstrap_repository(
                repo,
                decision_init["owner_descriptor"],
                decision_init["index_seed"],
                host="codex",
            )

            document = {
                "schema": "context-capture-candidate/v1",
                "candidate_id": "cand_550e8400e29b41d4a716446655440080",
                "title": "설계 골격",
                "claim": "현재 시스템의 설계 골격을 설명한다.",
                "summary": "판단자가 recall로 소비하는 현행 상태 진술이다.",
                "captured_from": "conversation",
                "requested_kind": "document",
                "specialized_kinds": ["document"],
                "fallback_kind": None,
                "scope_hint": "product/architecture",
                "tags": [],
                "search_terms": ["architecture"],
                "source_refs": [],
                "owner_inputs": {
                    "document": {
                        "document_key": "design-skeleton",
                        "content": "현재 시스템의 설계 골격을 설명한다.",
                    }
                },
            }
            document_result = document_cli.build_claim_result(
                document,
                claim_attestation(document_cli, document, [
                    ("content_present", ["/owner_inputs/document/content"]),
                    ("living_document", ["/owner_inputs/document/document_key", "/owner_inputs/document/content"]),
                ]),
                identifier=document_id,
                created_at="2026-09-01T10:00:00+09:00",
            )
            apply_result(repo, document_cli, document_result)

            decision = {
                "schema": "context-capture-candidate/v1",
                "candidate_id": "cand_550e8400e29b41d4a716446655440081",
                "title": "설계 kickoff 결정",
                "claim": "새 설계 envelope를 현행 기준으로 채택한다.",
                "summary": "설계 골격 document에 반영해야 하는 현재 결정이다.",
                "captured_from": "conversation",
                "requested_kind": "decision",
                "specialized_kinds": ["decision"],
                "fallback_kind": None,
                "scope_hint": "product/architecture",
                "tags": [],
                "search_terms": ["architecture"],
                "source_refs": [],
                "evidence": ["책임자가 현재 따를 선택으로 확정했다."],
                "owner_inputs": {
                    "decision": {
                        "decision": "새 설계 envelope를 현행 기준으로 채택한다.",
                        "rationale": "구현 경계와 운영 책임을 같은 기준으로 정렬한다.",
                        "rejected_alternatives": ["기존 골격 유지: 새 경계를 반영하지 못한다."],
                        "decision_key": "design-envelope",
                        "affects_documents": [document_id],
                    }
                },
            }
            decision_result = decision_cli.build_claim_result(
                decision,
                claim_attestation(decision_cli, decision, [
                    ("explicit_choice", ["/owner_inputs/decision/decision"]),
                    ("scope_identified", ["/scope_hint"]),
                    ("commitment_present", ["/evidence/0"]),
                ]),
                identifier=decision_id,
                created_at="2026-09-01T10:01:00+09:00",
                repo=repo,
            )
            apply_result(repo, decision_cli, decision_result)

            stale = core_cli.refresh_repository(repo)
            self.assertTrue(stale["ok"])
            self.assertIn(
                {
                    "check": "document-stale-vs-decision",
                    "tier": "hygiene-warn",
                    "document": document_id,
                    "newer_decisions": [decision_id],
                },
                stale["warnings"],
            )

            update = document_cli.build_update_result(
                repo,
                document_id,
                "현재 시스템의 설계 골격과 새 envelope 경계를 함께 설명한다.",
                updated_at="2026-09-01T10:02:00+09:00",
            )
            apply_result(repo, document_cli, update)
            refreshed = core_cli.refresh_repository(repo)
            self.assertTrue(refreshed["ok"])
            self.assertNotIn(
                "document-stale-vs-decision",
                {warning.get("check") for warning in refreshed["warnings"]},
            )


if __name__ == "__main__":
    unittest.main()
