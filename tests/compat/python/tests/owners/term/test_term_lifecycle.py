from __future__ import annotations

import copy
import tempfile
import unittest
from pathlib import Path

import term_test_support as helpers


term_cli = helpers.term_cli


def same_claim_attestation(value: dict) -> dict:
    return {
        "schema": "context-semantic-attestation/v1",
        "operation": "same_claim",
        "input_schema": value["schema"],
        "input_digest": term_cli.canonical_digest(value),
        "assertions": [{
            "name": "same_semantic_claim",
            "value": True,
            "evidence_pointers": ["/predecessor/primary_claim", "/successor/primary_claim"],
        }],
    }


class TermLifecycleTests(unittest.TestCase):
    def _repo(self, temp: str) -> Path:
        repo = Path(temp) / "repo"
        repo.mkdir()
        helpers.init_repo(repo)
        helpers.capture(repo)
        return repo

    def test_deprecate_requires_reason_and_optional_distinct_replacement(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            repo = self._repo(temp)
            result = term_cli.build_deprecate_result(
                repo,
                "ctx_550e8400e29b41d4a716446655440000",
                "새 gateway 구조에서는 더 이상 이 명칭을 쓰지 않는다.",
                "Session Gateway",
                retired_at="2026-08-22T02:00:00+09:00",
            )
            receipt = term_cli.validate_batch(repo, result)
            self.assertEqual("retire_current", receipt["transition_topology"])
            helpers.apply_result(repo, result)
            _, current, history = term_cli._index(repo)
            self.assertEqual([], current)
            record = term_cli._record(repo, history[0])
            self.assertEqual("deprecated", record["frontmatter"]["retired_reason"])
            self.assertEqual("Session Gateway", record["frontmatter"]["replacement_term"])
            self.assertIn("더 이상", record["frontmatter"]["deprecation_reason"])

            with self.assertRaises(term_cli.TermError):
                term_cli.build_deprecate_result(repo, record["frontmatter"]["id"], "again")

    def test_supersede_quotes_actual_term_and_definition_and_keeps_slot(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            repo = self._repo(temp)
            successor = helpers.candidate(
                candidate_id="cand_550e8400e29b41d4a716446655440001",
                term="bff!",
                definition="이 프로젝트에서 browser session, callback, backend API 인증 경계를 함께 소유하는 서비스다.",
                title="BFF 정의 개정",
            )
            same = term_cli.prepare_same_claim_input(
                repo, "ctx_550e8400e29b41d4a716446655440000", successor
            )
            self.assertEqual(
                {"term": "BFF", "definition": helpers.candidate()["claim"]},
                same["predecessor"]["primary_claim"],
            )
            self.assertEqual("bff!", same["successor"]["primary_claim"]["term"])
            proof = same_claim_attestation(same)
            result = term_cli.build_supersede_result(
                repo,
                "ctx_550e8400e29b41d4a716446655440000",
                successor,
                helpers.attestation(successor),
                same,
                proof,
                successor_id="ctx_550e8400e29b41d4a716446655440001",
                retired_at="2026-08-22T02:00:00+09:00",
            )
            self.assertEqual("supersede_current", term_cli.validate_batch(repo, result)["transition_topology"])

            altered = copy.deepcopy(same)
            altered["predecessor"]["primary_claim"]["definition"] = "hash만 같은 다른 정의"
            with self.assertRaises(term_cli.TermError) as caught:
                term_cli.build_supersede_result(
                    repo,
                    "ctx_550e8400e29b41d4a716446655440000",
                    successor,
                    helpers.attestation(successor),
                    altered,
                    same_claim_attestation(altered),
                )
            self.assertEqual("same_claim_input_invalid", caught.exception.code)

            helpers.apply_result(repo, result)
            _, current, history = term_cli._index(repo)
            current_record = term_cli._record(repo, current[0])
            history_record = term_cli._record(repo, history[0])
            self.assertEqual("bff", current_record["frontmatter"]["term_key"])
            self.assertEqual(current_record["frontmatter"]["id"], history_record["frontmatter"]["superseded_by"])
            self.assertIn(history_record["frontmatter"]["id"], current_record["frontmatter"]["supersedes"])

    def test_annotate_preserves_term_definition_and_slot(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            repo = self._repo(temp)
            before = term_cli._current_record(repo, "ctx_550e8400e29b41d4a716446655440000")
            result = term_cli.build_annotate_result(
                repo,
                before["frontmatter"]["id"],
                summary="인증 경계에서 쓰는 용어임을 더 명시한다.",
                tags=["auth", "terminology"],
                updated_at="2026-08-22T02:00:00+09:00",
            )
            after_fm, after_sections = term_cli.parse_document(result["artifact_drafts"][0]["content"])
            self.assertEqual(before["sections"], after_sections)
            for field in ("scope", "term", "term_key", "aliases", "deprecated_terms", "related"):
                self.assertEqual(before["frontmatter"].get(field), after_fm.get(field))
            self.assertEqual("replace_same_state", term_cli.validate_batch(repo, result)["transition_topology"])

    def test_exact_and_scope_overlap_slots_fail_closed(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            repo = self._repo(temp)
            for index, scope in enumerate(("project/auth", "project", "project/auth/api"), start=1):
                with self.subTest(scope=scope):
                    value = helpers.candidate(
                        candidate_id="cand_" + f"{index + 10:032x}",
                        term="BFF!",
                        scope=scope,
                        title=f"BFF collision {index}",
                    )
                    result = term_cli.build_claim_result(
                        value,
                        helpers.attestation(value),
                        identifier=f"ctx_550e8400e29b41d4a71644665544001{index}",
                        created_at="2026-08-22T03:00:00+09:00",
                    )
                    with self.assertRaises(term_cli.TermError) as caught:
                        term_cli.validate_batch(repo, result)
                    self.assertEqual("term_slot_conflict", caught.exception.code)

            unrelated = helpers.candidate(
                candidate_id="cand_550e8400e29b41d4a716446655440010",
                term="BFF!",
                scope="project/payments",
                title="Payments BFF",
            )
            result = term_cli.build_claim_result(
                unrelated,
                helpers.attestation(unrelated),
                identifier="ctx_550e8400e29b41d4a716446655440010",
                created_at="2026-08-22T03:00:00+09:00",
            )
            self.assertEqual("valid", term_cli.validate_batch(repo, result)["status"])


if __name__ == "__main__":
    unittest.main()
