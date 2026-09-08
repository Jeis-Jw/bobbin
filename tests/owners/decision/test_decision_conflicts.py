#!/usr/bin/env python3
from __future__ import annotations

import unittest
from unittest import mock

import test_decision_schema as helpers


decision_cli = helpers.decision_cli


def draft_pair(result: dict) -> tuple[str, str]:
    draft = result["artifact_drafts"][0]
    return draft["path"], draft["content"]


class DecisionConflictTests(unittest.TestCase):
    def test_check_actions_follow_recording_policy_and_reuse_same_silently(self) -> None:
        with helpers.vault_dir() as temp:
            repo = helpers.Path(temp)
            helpers.write_decision_area(repo, current=[draft_pair(helpers.claim_result())])
            before = helpers.tree_digest(repo)

            for slot in ((), ("--scope", "project/auth", "--decision-key", "session-owner")):
                with self.subTest(slot=slot):
                    completed = helpers.subprocess.run(
                        [
                            helpers.sys.executable, str(helpers.CLI_PATH), "check",
                            "--statement", "인증 세션은 BFF가 소유한다.", *slot, "--json",
                        ],
                        cwd=repo, text=True, capture_output=True,
                    )
                    self.assertEqual(0, completed.returncode, completed.stdout + completed.stderr)
                    actions = helpers.json.loads(completed.stdout)["result"]["assessment_contract"]["actions"]
                    for directive in (
                        "shared recording policy", "explicit/auto/adaptive",
                        "Explicit choice only", "no second storage ask",
                    ):
                        self.assertIn(directive, actions["new"])
                    self.assertIn("silently", actions["same"])
                    self.assertIn("no duplicate DEC", actions["same"])
                    self.assertIn("cite if needed", actions["same"])
                    self.assertNotIn("Ask about capture", actions["new"])
                    self.assertFalse(actions["same"].startswith("Cite"))
                    self.assertEqual(before, helpers.tree_digest(repo))

    def test_acceptance_26_duplicate_slot(self) -> None:
        with helpers.vault_dir() as temp:
            repo = helpers.Path(temp)
            existing = helpers.claim_result()
            helpers.write_decision_area(repo, current=[draft_pair(existing)])
            second_value = helpers.candidate(
                decision="인증 세션은 API gateway가 소유한다.",
                candidate_id="cand_550e8400e29b41d4a716446655440001",
            )
            second = helpers.claim_result(second_value, identifier="ctx_550e8400e29b41d4a716446655440001")
            with self.assertRaises(decision_cli.DecisionError) as physical:
                decision_cli.validate_batch(repo, second)
            self.assertEqual("decision_slot_conflict", physical.exception.code)
            self.assertEqual("supersede", physical.exception.details["suggested_action"])
            self.assertEqual(
                {
                    "id": "ctx_550e8400e29b41d4a716446655440000",
                    "title": "인증 세션 소유권",
                    "created_at": "2026-08-13T18:20:00+09:00",
                },
                physical.exception.details["current"],
            )

        with helpers.vault_dir() as temp:
            repo = helpers.Path(temp)
            helpers.write_decision_area(repo)
            first = helpers.claim_result()
            first_bundle = helpers.bundle(first)
            second_value = helpers.candidate(
                decision="인증 세션은 API gateway가 소유한다.",
                candidate_id="cand_550e8400e29b41d4a716446655440001",
            )
            second = helpers.claim_result(second_value, identifier="ctx_550e8400e29b41d4a716446655440001")
            with self.assertRaises(decision_cli.DecisionError) as virtual:
                decision_cli.validate_batch(repo, second, [first_bundle])
            self.assertEqual("decision_slot_conflict", virtual.exception.code)

    def test_acceptance_46_actual_body_semantic_check(self) -> None:
        with helpers.vault_dir() as temp:
            repo = helpers.Path(temp)
            existing = helpers.claim_result()
            helpers.write_decision_area(repo, current=[draft_pair(existing)])
            related_value = helpers.candidate(
                key="auth-owner-alias",
                decision="로그인 세션의 책임 경계는 backend-for-frontend에 둔다.",
                candidate_id="cand_550e8400e29b41d4a716446655440001",
            )
            related = helpers.claim_result(related_value, identifier="ctx_550e8400e29b41d4a716446655440001")
            receipt = decision_cli.validate_batch(repo, related)
            self.assertEqual(related_value["claim"], receipt["validated_facts"]["primary_claim"])

            check = decision_cli.prepare_decision_check(
                repo,
                statement=related_value["claim"],
                rationale=related_value["owner_inputs"]["decision"]["rationale"],
                scope=related_value["scope_hint"],
                decision_key=related_value["owner_inputs"]["decision"]["decision_key"],
            )
            self.assertEqual("exact_slot", check["coverage"])
            self.assertEqual(["ctx_550e8400e29b41d4a716446655440000"], [item["id"] for item in check["comparison_input"]["current"]])
            self.assertEqual("인증 세션은 BFF가 소유한다.", check["comparison_input"]["current"][0]["sections"]["Decision"])
            self.assertEqual(list(decision_cli.SEMANTIC_RELATIONS), check["assessment_contract"]["relations"])
            self.assertEqual(1, check["retrieval"]["body_reads"])
            self.assertGreater(check["retrieval"]["selected_semantic_bytes"], 0)
            self.assertEqual(
                decision_cli.file_digest((repo / decision_cli.DECISION_INDEX).read_text(encoding="utf-8")),
                check["retrieval"]["index_sha256"],
            )
            self.assertFalse(check["physical_write"])

            completed = helpers.subprocess.run(
                [
                    helpers.sys.executable,
                    str(helpers.CLI_PATH),
                    "check",
                    "--statement",
                    related_value["claim"],
                    "--scope",
                    related_value["scope_hint"],
                    "--decision-key",
                    related_value["owner_inputs"]["decision"]["decision_key"],
                    "--json",
                ],
                cwd=repo,
                text=True,
                capture_output=True,
            )
            self.assertEqual(0, completed.returncode, completed.stdout + completed.stderr)
            cli_result = helpers.json.loads(completed.stdout)["result"]
            self.assertEqual("context-decision-check/v1", cli_result["schema"])
            self.assertEqual(check["comparison_input"]["current"][0]["id"], cli_result["comparison_input"]["current"][0]["id"])

    def test_check_conditionally_includes_actual_revisit_body_and_follows_changes(self) -> None:
        with helpers.vault_dir() as temp:
            repo = helpers.Path(temp)
            initial_value = helpers.candidate()
            initial_value["owner_inputs"]["decision"]["revisit_when"] = [
                "오프라인 요구가 사라지면 재평가한다."
            ]
            initial = helpers.claim_result(initial_value)
            helpers.write_decision_area(repo, current=[draft_pair(initial)])
            before = helpers.tree_digest(repo)

            first = decision_cli.prepare_decision_check(
                repo,
                statement="인증 세션 소유권을 API gateway로 바꾼다.",
                scope="project/auth",
                decision_key="session-owner",
            )

            first_item = first["comparison_input"]["current"][0]
            self.assertEqual(
                "- 오프라인 요구가 사라지면 재평가한다.",
                first_item["sections"]["Revisit conditions"],
            )
            self.assertNotIn("revisit_when", first_item)
            revisit_contract = first["assessment_contract"]["conflict_revisit"]
            self.assertEqual(
                ["satisfied", "no evidence", "ambiguous"],
                revisit_contract["classifications"],
            )
            self.assertEqual(
                ["revisit_conditions", "revisit_assessment"],
                revisit_contract["required_fields"],
            )
            self.assertIn("Revisit conditions", first["assessment_contract"]["actions"]["conflict"])
            self.assertIn("keep = not performed", first["assessment_contract"]["actions"]["conflict"])
            self.assertIn("not implementation", first["assessment_contract"]["actions"]["conflict"])
            self.assertIn("Do not invent evidence", revisit_contract["rule"])
            self.assertIn("user-supplied present facts", revisit_contract["rule"])
            self.assertIn("requested conflicting action itself is not evidence", revisit_contract["rule"])
            self.assertIn("facts are absent or concern something other than the stored condition", revisit_contract["rule"])
            self.assertNotIn("offline-status", revisit_contract["rule"])
            self.assertNotIn("growth priority", revisit_contract["rule"])
            self.assertIn("Use ambiguous only when", revisit_contract["rule"])
            self.assertIn("state the selected classification token verbatim", revisit_contract["rule"])
            self.assertIn("one explicit binary question", first["assessment_contract"]["rule"])
            self.assertEqual(before, helpers.tree_digest(repo))

            changed_value = helpers.candidate()
            changed_value["owner_inputs"]["decision"]["revisit_when"] = [
                "규제기관이 중앙 세션 저장을 요구하면 재평가한다."
            ]
            changed = helpers.claim_result(changed_value)
            helpers.write_decision_area(repo, current=[draft_pair(changed)])
            before_changed_check = helpers.tree_digest(repo)

            second = decision_cli.prepare_decision_check(
                repo,
                statement="인증 세션 소유권을 API gateway로 바꾼다.",
                scope="project/auth",
                decision_key="session-owner",
            )

            second_item = second["comparison_input"]["current"][0]
            self.assertEqual(
                "- 규제기관이 중앙 세션 저장을 요구하면 재평가한다.",
                second_item["sections"]["Revisit conditions"],
            )
            self.assertNotEqual(first["input_digest"], second["input_digest"])
            self.assertNotEqual(first_item["sha256"], second_item["sha256"])
            self.assertEqual(before_changed_check, helpers.tree_digest(repo))
            self.assertLessEqual(
                len(decision_cli.canonical_json(second).encode("utf-8")),
                decision_cli.MAX_CHECK_RESULT_BYTES,
            )

    def test_check_does_not_invent_revisit_section_when_absent(self) -> None:
        with helpers.vault_dir() as temp:
            repo = helpers.Path(temp)
            existing = helpers.claim_result()
            helpers.write_decision_area(repo, current=[draft_pair(existing)])

            check = decision_cli.prepare_decision_check(
                repo,
                statement="인증 세션 소유권을 API gateway로 바꾼다.",
                scope="project/auth",
                decision_key="session-owner",
            )

            item = check["comparison_input"]["current"][0]
            self.assertEqual(list(decision_cli.CORE_SECTIONS), list(item["sections"]))
            self.assertNotIn("revisit_when", item)
            self.assertNotIn("Revisit conditions", item["sections"])
            self.assertNotIn("conflict_revisit", check["assessment_contract"])
            self.assertEqual(
                ("Decision", "Rationale", "Rejected alternatives"),
                decision_cli.CORE_SECTIONS,
            )

    def test_discovery_only_check_is_lexical_and_cannot_conclude_no_conflict(self) -> None:
        with helpers.vault_dir() as temp:
            repo = helpers.Path(temp)
            existing = helpers.claim_result()
            helpers.write_decision_area(repo, current=[draft_pair(existing)])
            before = helpers.tree_digest(repo)

            result = decision_cli.prepare_decision_check(
                repo,
                statement="BFF 인증 세션 소유권을 바꾼다.",
            )

            self.assertEqual("discovery_only", result["coverage"])
            self.assertEqual(
                "no-conflict cannot be concluded; re-run with exact scope/decision_key before preview",
                result["caveat"],
            )
            self.assertIsNone(result["comparison_input"]["proposal"]["scope"])
            self.assertIsNone(result["comparison_input"]["proposal"]["decision_key"])
            self.assertEqual([], result["deterministic"]["exact_slot"])
            self.assertEqual([], result["deterministic"]["scope_overlap"])
            self.assertEqual(before, helpers.tree_digest(repo))

            completed = helpers.subprocess.run(
                [
                    helpers.sys.executable,
                    str(helpers.CLI_PATH),
                    "check",
                    "--statement",
                    "BFF 인증 세션 소유권을 바꾼다.",
                    "--json",
                ],
                cwd=repo,
                text=True,
                capture_output=True,
            )
            self.assertEqual(0, completed.returncode, completed.stdout + completed.stderr)
            self.assertEqual("discovery_only", helpers.json.loads(completed.stdout)["result"]["coverage"])
            self.assertEqual(before, helpers.tree_digest(repo))

            for partial_argument in (("--scope", "project/auth"), ("--decision-key", "session-owner")):
                with self.subTest(partial=partial_argument[0]):
                    partial = helpers.subprocess.run(
                        [
                            helpers.sys.executable,
                            str(helpers.CLI_PATH),
                            "check",
                            "--statement",
                            "BFF 인증 세션 소유권을 바꾼다.",
                            *partial_argument,
                            "--json",
                        ],
                        cwd=repo,
                        text=True,
                        capture_output=True,
                    )
                    self.assertEqual(2, partial.returncode, partial.stdout + partial.stderr)
                    self.assertEqual("usage_invalid", helpers.json.loads(partial.stdout)["error"]["code"])

    def test_check_skips_unrelated_bodies_for_large_current_index(self) -> None:
        rows = [
            {
                "id": f"ctx_{number:032x}",
                "path": f"context/decision/record-{number:05d}.md",
                "title": f"decision {number}",
                "summary": "unrelated current decision",
                "scope": f"project/area-{number}",
                "decision_key": f"slot-{number}",
                "terms": [],
            }
            for number in range(5000)
        ]

        def record(_repo, row):
            return {
                "id": row["id"],
                "path": row["path"],
                "sha256": "sha256:" + "a" * 64,
                "frontmatter": {
                    "title": row["title"],
                    "summary": row["summary"],
                    "scope": row["scope"],
                    "decision_key": row["decision_key"],
                },
                "sections": {
                    "결정": "bounded decision body",
                    "취지": "bounded rationale",
                    "반려대안": "bounded alternative",
                },
            }

        with (
            mock.patch.object(decision_cli, "_index", return_value=("index", rows, [])),
            mock.patch.object(decision_cli, "_record", side_effect=record) as opened,
        ):
            result = decision_cli.prepare_decision_check(
                helpers.Path("."),
                statement="new unrelated decision",
                scope="project/new-area",
                decision_key="new-slot",
            )

        retrieval = result["retrieval"]
        opened.assert_not_called()
        self.assertEqual(5000, retrieval["total_current"])
        self.assertEqual(0, retrieval["metadata_matches"])
        self.assertEqual(0, retrieval["body_reads"])
        self.assertEqual(0, retrieval["returned"])
        self.assertEqual(5000, retrieval["omitted"])
        self.assertEqual(2, retrieval["selected_semantic_bytes"])
        self.assertEqual(decision_cli.file_digest("index"), retrieval["index_sha256"])
        self.assertEqual(decision_cli.MAX_OMITTED_ID_SAMPLE, len(retrieval["omitted_id_sample"]))
        self.assertTrue(retrieval["omitted_id_sample_truncated"])
        self.assertLessEqual(
            len(decision_cli.canonical_json(result).encode("utf-8")),
            2500,
        )

    def test_check_still_reads_a_distinctive_cross_scope_match(self) -> None:
        rows = [
            {
                "id": "ctx_550e8400e29b41d4a716446655440000",
                "path": "context/decision/unrelated.md",
                "title": "generic delivery policy",
                "summary": "common repository decision",
                "scope": "other/product",
                "decision_key": "delivery",
                "terms": [],
            },
            {
                "id": "ctx_123e4567e89b42d3a456426614174001",
                "path": "context/decision/proactive-loop.md",
                "title": "proactive conversation conflict loop",
                "summary": "incremental decision detection",
                "scope": "shared/agent",
                "decision_key": "conversation-loop",
                "terms": ["proactive", "conflict"],
            },
        ]

        def record(_repo, row):
            return {
                "id": row["id"],
                "path": row["path"],
                "sha256": "sha256:" + "a" * 64,
                "frontmatter": {
                    "title": row["title"],
                    "summary": row["summary"],
                    "scope": row["scope"],
                    "decision_key": row["decision_key"],
                },
                "sections": {
                    "결정": "대화 중 결정 신호를 증분 감지한다.",
                    "취지": "충돌을 먼저 발견한다.",
                    "반려대안": "매 turn 전체 recall은 반려한다.",
                },
            }

        with (
            mock.patch.object(decision_cli, "_index", return_value=("index", rows, [])),
            mock.patch.object(decision_cli, "_record", side_effect=record) as opened,
        ):
            result = decision_cli.prepare_decision_check(
                helpers.Path("."),
                statement="proactive conversation conflict detection",
                scope="project/new-area",
                decision_key="new-slot",
            )

        self.assertEqual(
            ["ctx_123e4567e89b42d3a456426614174001"],
            [item["id"] for item in result["comparison_input"]["current"]],
        )
        self.assertEqual(1, opened.call_count)

    def test_acceptance_27_scope_overlap(self) -> None:
        with helpers.vault_dir() as temp:
            repo = helpers.Path(temp)
            existing = helpers.claim_result()
            existing_path, existing_content = draft_pair(existing)
            helpers.write_decision_area(repo, current=[(existing_path, existing_content)])
            ancestor_value = helpers.candidate(
                scope="project",
                decision="프로젝트 인증 세션은 서버 경계가 소유한다.",
                candidate_id="cand_550e8400e29b41d4a716446655440001",
            )
            unacknowledged = helpers.claim_result(ancestor_value, identifier="ctx_550e8400e29b41d4a716446655440001")
            conflicts = decision_cli.conflict_candidates(repo, "project", "session-owner")
            self.assertEqual(["ctx_550e8400e29b41d4a716446655440000"], [item["id"] for item in conflicts["overlap"]])
            with self.assertRaises(decision_cli.DecisionError) as caught:
                decision_cli.validate_batch(repo, unacknowledged)
            self.assertEqual("conflict_ack_required", caught.exception.code)

            acknowledged = helpers.claim_result(
                ancestor_value,
                identifier="ctx_550e8400e29b41d4a716446655440001",
                repo=repo,
                acknowledgements=("ctx_550e8400e29b41d4a716446655440000",),
            )
            receipt = decision_cli.validate_batch(repo, acknowledged)
            self.assertEqual(["ctx_550e8400e29b41d4a716446655440000"], receipt["validated_facts"]["acknowledged_conflicts"])
            self.assertEqual(decision_cli.canonical_digest({key: value for key, value in receipt.items() if key != "receipt_digest"}), receipt["receipt_digest"])

    def test_same_batch_overlap_requires_virtual_read_precondition(self) -> None:
        with helpers.vault_dir() as temp:
            repo = helpers.Path(temp)
            helpers.write_decision_area(repo)
            child = helpers.claim_result()
            child_bundle = helpers.bundle(child)
            parent_value = helpers.candidate(
                scope="project",
                decision="프로젝트 인증 세션은 서버가 소유한다.",
                candidate_id="cand_550e8400e29b41d4a716446655440001",
            )
            parent = helpers.claim_result(parent_value, identifier="ctx_550e8400e29b41d4a716446655440001")
            virtual_conflict = child["artifact_drafts"][0]
            parent["effects"][0]["acknowledged_conflicts"] = ["ctx_550e8400e29b41d4a716446655440000"]
            parent["proposed_plan"]["read_preconditions"] = [{
                "id": "ctx_550e8400e29b41d4a716446655440000",
                "path": virtual_conflict["path"],
                "sha256": decision_cli.file_digest(virtual_conflict["content"]),
            }]
            receipt = decision_cli.validate_batch(repo, parent, [child_bundle])
            self.assertEqual([child_bundle["approval_digest"]], receipt["prior_same_area_bundle_digests"])

            parent["proposed_plan"]["read_preconditions"][0]["sha256"] = "sha256:" + "0" * 64
            with self.assertRaises(decision_cli.DecisionError) as caught:
                decision_cli.validate_batch(repo, parent, [child_bundle])
            self.assertEqual("conflict_read_precondition_required", caught.exception.code)

    def test_receipt_binds_exact_ordered_prior_chain(self) -> None:
        with helpers.vault_dir() as temp:
            repo = helpers.Path(temp)
            helpers.write_decision_area(repo)
            first = helpers.claim_result()
            first_bundle = helpers.bundle(first)
            second_value = helpers.candidate(
                scope="project/payments",
                key="settlement-owner",
                decision="정산은 ledger service가 소유한다.",
                title="정산 소유권",
                candidate_id="cand_550e8400e29b41d4a716446655440001",
            )
            second = helpers.claim_result(second_value, identifier="ctx_550e8400e29b41d4a716446655440001")
            second_bundle = helpers.bundle(second, priors=[first_bundle["approval_digest"]])
            third_value = helpers.candidate(
                scope="project/notifications",
                key="delivery-owner",
                decision="알림 전송은 notification service가 소유한다.",
                title="알림 전송 소유권",
                candidate_id="cand_550e8400e29b41d4a716446655440002",
            )
            third = helpers.claim_result(third_value, identifier="ctx_550e8400e29b41d4a716446655440002")
            receipt = decision_cli.validate_batch(repo, third, [first_bundle, second_bundle])
            self.assertEqual([first_bundle["approval_digest"], second_bundle["approval_digest"]], receipt["prior_same_area_bundle_digests"])
            with self.assertRaises(decision_cli.DecisionError) as caught:
                decision_cli.validate_batch(repo, third, [second_bundle, first_bundle])
            self.assertEqual("prior_bundle_order_invalid", caught.exception.code)


if __name__ == "__main__":
    unittest.main()
