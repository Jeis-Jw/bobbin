#!/usr/bin/env python3
from __future__ import annotations

import unittest

import test_decision_schema as helpers


decision_cli = helpers.decision_cli


def draft_pair(result: dict, *, current: bool = True) -> tuple[str, str]:
    drafts = [draft for draft in result["artifact_drafts"] if ("/retired/" not in draft["path"]) == current]
    assert len(drafts) == 1
    return drafts[0]["path"], drafts[0]["content"]


class DecisionLifecycleTests(unittest.TestCase):
    def test_acceptance_28_supersede(self) -> None:
        with helpers.vault_dir() as temp:
            repo = helpers.Path(temp)
            old_result = helpers.claim_result()
            helpers.write_decision_area(repo, current=[draft_pair(old_result)])
            successor_candidate = helpers.candidate(
                decision="인증 세션은 auth service가 소유한다.",
                rationale="BFF와 worker가 같은 session lifecycle을 공유한다.",
                candidate_id="cand_550e8400e29b41d4a716446655440001",
            )
            result = decision_cli.build_supersede_result(
                repo,
                "ctx_550e8400e29b41d4a716446655440000",
                successor_candidate,
                helpers.attestation(successor_candidate),
                identifier="ctx_550e8400e29b41d4a716446655440001",
                retired_at="2026-08-14T09:00:00+09:00",
            )
            decision_cli.validate_owner_result(result)
            old_path, old_content = draft_pair(result, current=False)
            new_path, new_content = draft_pair(result, current=True)
            old_fm, _ = decision_cli.parse_document(old_content)
            new_fm, _ = decision_cli.parse_document(new_content)
            self.assertEqual((old_fm["scope"], old_fm["decision_key"]), (new_fm["scope"], new_fm["decision_key"]))
            self.assertEqual(new_fm["id"], old_fm["superseded_by"])
            self.assertEqual([old_fm["id"]], new_fm["supersedes"])
            self.assertIn(f"--{old_fm['id'][4:16]}.md", old_path)
            self.assertNotIn("/retired/", new_path)
            operations = result["proposed_plan"]["operations"]
            self.assertEqual(["move", "create"], [operation["op"] for operation in operations])
            receipt = decision_cli.validate_batch(repo, result)
            self.assertEqual("valid", receipt["status"])

    def test_supersede_requires_explicit_exact_successor_slot(self) -> None:
        with helpers.vault_dir() as temp:
            repo = helpers.Path(temp)
            old = helpers.claim_result()
            helpers.write_decision_area(repo, current=[draft_pair(old)])
            mismatched = helpers.candidate(
                scope="project/payments",
                decision="인증 세션은 auth service가 소유한다.",
                candidate_id="cand_550e8400e29b41d4a716446655440001",
            )
            with self.assertRaises(decision_cli.DecisionError) as caught:
                decision_cli.build_supersede_result(
                    repo,
                    "ctx_550e8400e29b41d4a716446655440000",
                    mismatched,
                    helpers.attestation(mismatched),
                    identifier="ctx_550e8400e29b41d4a716446655440001",
                    retired_at="2026-08-14T09:00:00+09:00",
                )
            self.assertEqual("successor_slot_mismatch", caught.exception.code)

    def test_repeated_same_title_history_paths_are_deterministic_and_distinct(self) -> None:
        with helpers.vault_dir() as temp:
            repo = helpers.Path(temp)
            first = helpers.claim_result()
            helpers.write_decision_area(repo, current=[draft_pair(first)])
            second_candidate = helpers.candidate(
                decision="인증 세션은 auth service가 소유한다.",
                candidate_id="cand_550e8400e29b41d4a716446655440001",
            )
            second = decision_cli.build_supersede_result(
                repo,
                "ctx_550e8400e29b41d4a716446655440000",
                second_candidate,
                helpers.attestation(second_candidate),
                identifier="ctx_123e4567e89b42d3a456426614174001",
                retired_at="2026-08-14T09:00:00+09:00",
            )
            old_history = draft_pair(second, current=False)
            helpers.write_decision_area(repo, current=[draft_pair(second)], history=[old_history])
            third_candidate = helpers.candidate(
                decision="인증 세션은 identity platform이 소유한다.",
                candidate_id="cand_550e8400e29b41d4a716446655440002",
            )
            third = decision_cli.build_supersede_result(
                repo,
                "ctx_123e4567e89b42d3a456426614174001",
                third_candidate,
                helpers.attestation(third_candidate),
                identifier="ctx_987e6543e21b42d3a456426614174002",
                retired_at="2026-08-15T09:00:00+09:00",
            )
            first_history_path = old_history[0]
            second_history_path = draft_pair(third, current=False)[0]
            self.assertNotEqual(first_history_path, second_history_path)
            self.assertTrue(first_history_path.endswith("--550e8400e29b.md"))
            self.assertTrue(second_history_path.endswith("--123e4567e89b.md"), (first_history_path, second_history_path))

    def test_acceptance_29_withdraw(self) -> None:
        with helpers.vault_dir() as temp:
            repo = helpers.Path(temp)
            current = helpers.claim_result()
            helpers.write_decision_area(repo, current=[draft_pair(current)])
            result = decision_cli.build_withdraw_result(
                repo,
                "ctx_550e8400e29b41d4a716446655440000",
                "외부 인증 정책이 바뀌어 이 결정을 철회한다.",
                retired_at="2026-08-14T09:00:00+09:00",
            )
            decision_cli.validate_owner_result(result)
            path, content = draft_pair(result, current=False)
            frontmatter, _ = decision_cli.parse_document(content)
            self.assertEqual("withdrawn", frontmatter["retired_reason"])
            self.assertNotIn("superseded_by", frontmatter)
            self.assertNotIn("successor", result["effects"][0])
            decision_cli.validate_batch(repo, result)

            helpers.write_decision_area(repo, history=[(path, content)])
            current_search = decision_cli.search_decisions(repo, query="인증", include_history=False)
            history_search = decision_cli.search_decisions(repo, query="인증", include_history=True)
            self.assertEqual([], current_search["items"])
            self.assertEqual(1, history_search["returned"])
            self.assertTrue(history_search["items"][0]["do_not_follow"])

    def test_annotate_preserves_semantic_sections_and_slot(self) -> None:
        with helpers.vault_dir() as temp:
            repo = helpers.Path(temp)
            current = helpers.claim_result()
            path, content = draft_pair(current)
            helpers.write_decision_area(repo, current=[(path, content)])
            before_fm, before_sections = decision_cli.parse_document(content)
            result = decision_cli.build_annotate_result(repo, before_fm["id"], summary="운영 경계를 BFF에 둔다.", tags=["auth", "bff"])
            after_fm, after_sections = decision_cli.parse_document(result["artifact_drafts"][0]["content"])
            self.assertEqual(before_sections, after_sections)
            self.assertEqual(
                (before_fm["scope"], before_fm["decision_key"]),
                (after_fm["scope"], after_fm["decision_key"]),
            )
            self.assertNotIn("claim_fingerprint", after_fm)


if __name__ == "__main__":
    unittest.main()
