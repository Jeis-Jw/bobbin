#!/usr/bin/env python3
from __future__ import annotations

import copy
import json
import subprocess
import unittest

import test_decision_schema as helpers


decision_cli = helpers.decision_cli


def pair(result: dict) -> tuple[str, str]:
    draft = next(draft for draft in result["artifact_drafts"] if "/retired/" not in draft["path"])
    return draft["path"], draft["content"]


class MutationPlanTests(unittest.TestCase):
    def test_acceptance_33_addon_init(self) -> None:
        with helpers.vault_dir() as temp:
            repo = helpers.Path(temp)
            before = helpers.tree_digest(repo)
            result = decision_cli.build_init_plan()
            self.assertEqual(before, helpers.tree_digest(repo))
            self.assertFalse(result["applied"])
            self.assertEqual("context-decision", result["owner_descriptor"]["owner"])
            self.assertEqual("decision", result["owner_descriptor"]["kind"])
            self.assertEqual(decision_cli.canonical_digest(result["owner_descriptor"]), result["descriptor_digest"])
            self.assertEqual(decision_cli.file_digest(result["index_seed"]), result["index_seed_sha256"])
            self.assertEqual({"owner": "context-core", "operation": "bootstrap", "index_path": "context/decision/decision.index.md"}, result["registration"])
            self.assertEqual(
                [("core_init", "ready"), ("area_register", "pending"), ("policy_install", "pending")],
                [(phase["phase"], phase["status"]) for phase in result["phases"]],
            )
            self.assertEqual("apply_if_needed", result["bootstrap"]["core_init"])
            self.assertEqual("active_host", result["bootstrap"]["host"])
            self.assertEqual("active_host", result["bootstrap"]["policy_install"])
            self.assertNotIn("context.index.md", result["index_seed"])
            decision_cli.parse_decision_index(result["index_seed"])

    def test_required_plugin_identity_matches_manual_dependency_contract(self) -> None:
        expected = json.loads(
            (helpers.PLUGIN.parents[1] / "tests/context-v1/fixtures/host-inventory/required-plugin.json").read_text(encoding="utf-8")
        )
        self.assertEqual(
            expected,
            decision_cli.REQUIRED_PLUGIN,
        )
        self.assertFalse(decision_cli.schema_result()["physical_write"])

    def test_validation_receipt_and_plan_validation_bind_owner_result(self) -> None:
        with helpers.vault_dir() as temp:
            repo = helpers.Path(temp)
            helpers.write_decision_area(repo)
            result = helpers.claim_result()
            receipt = decision_cli.validate_batch(repo, result)
            base = (repo / decision_cli.DECISION_INDEX).read_bytes()
            self.assertEqual(decision_cli.bytes_digest(base), receipt["base_area_index_sha256"])
            self.assertEqual(decision_cli.canonical_digest(result), receipt["owner_result_digest"])
            self.assertEqual(
                {
                    "scope": "project/auth",
                    "decision_key": "session-owner",
                    "primary_claim": "인증 세션은 BFF가 소유한다.",
                    "rationale": "브라우저별 cookie 차이를 서버 경계 안으로 모은다.",
                    "acknowledged_conflicts": [],
                },
                receipt["validated_facts"],
            )
            bundle = helpers.bundle(result, validation=receipt)
            validated = decision_cli.validate_plan_bundle(bundle)
            self.assertEqual("valid", validated["status"])
            self.assertFalse(validated["physical_write"])

            tampered = copy.deepcopy(bundle)
            tampered["approval_material"]["plan"]["owner_validation"]["validated_facts"]["scope"] = "project/other"
            tampered["approval_digest"] = decision_cli.canonical_digest(tampered["approval_material"])
            with self.assertRaises(decision_cli.DecisionError) as caught:
                decision_cli.validate_plan_bundle(tampered)
            self.assertEqual("owner_validation_invalid", caught.exception.code)

    def test_all_owner_operations_are_filesystem_noops(self) -> None:
        with helpers.vault_dir() as temp:
            repo = helpers.Path(temp)
            active = helpers.claim_result()
            helpers.write_decision_area(repo, current=[pair(active)])
            before = helpers.tree_digest(repo)
            decision_cli.schema_result()
            decision_cli.decision_capability()
            decision_cli.build_init_plan()
            decision_cli.search_decisions(repo, query="인증")
            decision_cli.read_decision(repo, "ctx_550e8400e29b41d4a716446655440000")
            decision_cli.brief_decisions(repo, identifiers=["ctx_550e8400e29b41d4a716446655440000"])
            decision_cli.conflict_candidates(repo, "project/auth", "session-owner")
            decision_cli.revisit_decisions(repo, as_of="2026-08-14")
            decision_cli.validate_batch(repo, decision_cli.build_withdraw_result(repo, "ctx_550e8400e29b41d4a716446655440000", "정책 변경", retired_at="2026-08-14T09:00:00+09:00"))
            decision_cli.build_annotate_result(repo, "ctx_550e8400e29b41d4a716446655440000", summary="변경된 설명")
            successor = helpers.candidate(
                decision="인증 세션은 auth service가 소유한다.",
                candidate_id="cand_550e8400e29b41d4a716446655440001",
            )
            supersede = decision_cli.build_supersede_result(
                repo,
                "ctx_550e8400e29b41d4a716446655440000",
                successor,
                helpers.attestation(successor),
                identifier="ctx_123e4567e89b42d3a456426614174001",
                retired_at="2026-08-14T09:00:00+09:00",
            )
            decision_cli.validate_batch(repo, supersede)
            self.assertEqual(before, helpers.tree_digest(repo))

    def test_supersede_rejects_successor_draft_detached_from_candidate(self) -> None:
        with helpers.vault_dir() as temp:
            repo = helpers.Path(temp)
            active = helpers.claim_result()
            helpers.write_decision_area(repo, current=[pair(active)])
            before = helpers.tree_digest(repo)
            successor = helpers.candidate(
                decision="인증 세션은 auth service가 소유한다.",
                candidate_id="cand_550e8400e29b41d4a716446655440001",
            )
            result = decision_cli.build_supersede_result(
                repo,
                "ctx_550e8400e29b41d4a716446655440000",
                successor,
                helpers.attestation(successor),
                identifier="ctx_123e4567e89b42d3a456426614174001",
                retired_at="2026-08-14T09:00:00+09:00",
            )
            altered = copy.deepcopy(result)
            draft = next(item for item in altered["artifact_drafts"] if "/retired/" not in item["path"])
            draft["content"] = draft["content"].replace(
                "인증 세션은 auth service가 소유한다.",
                "인증 세션은 완전히 다른 경계가 소유한다.",
            )
            draft["semantic_projection"]["primary_claim"] = "인증 세션은 완전히 다른 경계가 소유한다."
            with self.assertRaises(decision_cli.DecisionError) as caught:
                decision_cli.validate_batch(repo, altered)
            self.assertEqual("claim_result_mismatch", caught.exception.code)
            self.assertEqual(before, helpers.tree_digest(repo))

    def test_cli_core_free_static_surfaces_do_not_modify_repository(self) -> None:
        with helpers.vault_dir() as temp:
            repo = helpers.Path(temp)
            before = helpers.tree_digest(repo)
            for command in (("schema", "--json"), ("capabilities", "--json")):
                completed = subprocess.run(["python3", str(helpers.CLI_PATH), *command], cwd=repo, text=True, capture_output=True)
                self.assertEqual(0, completed.returncode, completed.stdout + completed.stderr)
                self.assertTrue(json.loads(completed.stdout)["ok"])
                self.assertEqual(before, helpers.tree_digest(repo))


if __name__ == "__main__":
    unittest.main()
