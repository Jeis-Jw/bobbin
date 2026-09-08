from __future__ import annotations

import copy
import tempfile
import unittest
from pathlib import Path

import assumption_test_support as helpers


assumption_cli = helpers.assumption_cli


class AssumptionLifecycleTests(unittest.TestCase):
    def _repo(self, temp: str) -> Path:
        repo = Path(temp) / "repo"
        repo.mkdir()
        helpers.init_repo(repo)
        helpers.capture(repo)
        return repo

    def test_confirm_retires_with_evidence(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            repo = self._repo(temp)
            result = assumption_cli.build_confirm_result(repo, "ctx_550e8400e29b41d4a716446655440000", ["metrics:callback-p95"], retired_at="2026-08-22T02:00:00+09:00")
            receipt = assumption_cli.validate_batch(repo, result)
            self.assertEqual("retire_current", receipt["transition_topology"])
            helpers.apply_result(repo, result)
            _, current, history = assumption_cli._index(repo)
            self.assertEqual([], current)
            record = assumption_cli._record(repo, history[0])
            self.assertEqual("confirmed", record["frontmatter"]["retired_reason"])
            self.assertEqual(["metrics:callback-p95"], record["frontmatter"]["evidence_refs"])

    def test_refute_reports_impacted_decisions_without_dec_mutation(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            repo = self._repo(temp)
            before_decision = list((repo / "context/decision").rglob("*")) if (repo / "context/decision").exists() else []
            result = assumption_cli.build_refute_result(repo, "ctx_550e8400e29b41d4a716446655440000", "production p95가 8초였다.", ["metrics:callback-p95"], [], retired_at="2026-08-22T02:00:00+09:00")
            request = next(item["value"] for item in result["semantic_inputs"] if item["operation"] == "mutation_request")
            self.assertFalse(request["requested_changes"]["decision_mutation"])
            self.assertEqual([], request["requested_changes"]["impacted_decisions"])
            self.assertTrue(all(effect["area"] == "assumption" for effect in result["effects"]))
            helpers.apply_result(repo, result)
            after_decision = list((repo / "context/decision").rglob("*")) if (repo / "context/decision").exists() else []
            self.assertEqual(before_decision, after_decision)

    def test_supersede_requires_both_actual_primary_claims_and_reciprocal_edges(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            repo = self._repo(temp)
            successor = helpers.candidate(
                candidate_id="cand_550e8400e29b41d4a716446655440001",
                assumption="외부 IdP callback은 정상 네트워크에서 5초 안에 반환할 것이다.",
                title="정상 네트워크 IdP latency 전제",
            )
            same = assumption_cli.prepare_same_claim_input(repo, "ctx_550e8400e29b41d4a716446655440000", successor)
            same_attestation = {
                "schema": "context-semantic-attestation/v1",
                "operation": "same_claim",
                "input_schema": same["schema"],
                "input_digest": assumption_cli.canonical_digest(same),
                "assertions": [{"name": "same_semantic_claim", "value": True, "evidence_pointers": ["/predecessor/primary_claim", "/successor/primary_claim"]}],
            }
            result = assumption_cli.build_supersede_result(
                repo,
                "ctx_550e8400e29b41d4a716446655440000",
                successor,
                helpers.attestation(successor),
                same,
                same_attestation,
                successor_id="ctx_550e8400e29b41d4a716446655440001",
                retired_at="2026-08-22T02:00:00+09:00",
            )
            receipt = assumption_cli.validate_batch(repo, result)
            self.assertEqual("supersede_current", receipt["transition_topology"])
            helpers.apply_result(repo, result)
            _, current, history = assumption_cli._index(repo)
            current_record = assumption_cli._record(repo, current[0])
            history_record = assumption_cli._record(repo, history[0])
            self.assertEqual(current_record["frontmatter"]["id"], history_record["frontmatter"]["superseded_by"])
            self.assertIn(history_record["frontmatter"]["id"], current_record["frontmatter"]["supersedes"])

            altered = copy.deepcopy(same)
            altered["predecessor"]["primary_claim"] = "hash만 같은 다른 claim"
            altered_attestation = copy.deepcopy(same_attestation)
            altered_attestation["input_digest"] = assumption_cli.canonical_digest(altered)
            with self.assertRaises(assumption_cli.AssumptionError) as caught:
                assumption_cli.build_supersede_result(repo, current_record["frontmatter"]["id"], successor, helpers.attestation(successor), altered, altered_attestation)
            self.assertEqual("same_claim_input_invalid", caught.exception.code)

    def test_annotate_preserves_primary_claim_and_conditions(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            repo = self._repo(temp)
            before = assumption_cli._current_record(repo, "ctx_550e8400e29b41d4a716446655440000")
            result = assumption_cli.build_annotate_result(repo, before["frontmatter"]["id"], summary="latency 전제의 운영 영향 범위를 명시한다.", tags=["auth", "latency"], updated_at="2026-08-22T02:00:00+09:00")
            after_fm, after_sections = assumption_cli.parse_document(result["artifact_drafts"][0]["content"])
            self.assertEqual(before["sections"], after_sections)
            self.assertEqual(before["frontmatter"]["scope"], after_fm["scope"])
            self.assertEqual(["auth", "latency"], after_fm["tags"])
            self.assertEqual("replace_same_state", assumption_cli.validate_batch(repo, result)["transition_topology"])


if __name__ == "__main__":
    unittest.main()
