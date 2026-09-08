#!/usr/bin/env python3
from __future__ import annotations

import json
import subprocess
import sys
import unittest
import unicodedata
import uuid
from unittest import mock

import test_decision_schema as helpers


decision_cli = helpers.decision_cli


def pair(result: dict, *, current: bool = True) -> tuple[str, str]:
    drafts = [draft for draft in result["artifact_drafts"] if ("/retired/" not in draft["path"]) == current]
    assert len(drafts) == 1
    return drafts[0]["path"], drafts[0]["content"]


def run_spec_view_cli(
    repo: helpers.Path,
    *,
    scope: str,
    max_bytes: int | None = None,
) -> subprocess.CompletedProcess[str]:
    command = [
        sys.executable,
        str(helpers.CLI_PATH),
        "spec-view",
        "--scope",
        scope,
        "--json",
    ]
    if max_bytes is not None:
        command[5:5] = ["--max-bytes", str(max_bytes)]
    return subprocess.run(command, cwd=repo, text=True, capture_output=True)


class DecisionRecallTests(unittest.TestCase):
    def test_read_only_cli_surfaces_need_no_host_inventory_and_write_no_repository_bytes(self) -> None:
        with helpers.vault_dir() as temp:
            repo = helpers.Path(temp)
            current = helpers.claim_result()
            helpers.write_decision_area(repo, current=[pair(current)])
            identifier = "ctx_550e8400e29b41d4a716446655440000"
            commands = (
                ("check", "--statement", "인증 세션은 BFF가 소유한다.", "--scope", "project/auth", "--decision-key", "session-owner"),
                ("search", "--query", "인증"),
                ("read", "--id", identifier),
                ("brief", "--id", identifier),
                ("spec-view", "--scope", "project/auth"),
                ("conflicts", "--scope", "project/auth", "--decision-key", "session-owner"),
                ("revisit", "--id", identifier),
            )
            before = helpers.tree_digest(repo)
            for command in commands:
                with self.subTest(command=command[0]):
                    completed = subprocess.run(
                        [sys.executable, str(helpers.CLI_PATH), *command, "--json"],
                        cwd=repo,
                        text=True,
                        capture_output=True,
                    )
                    self.assertEqual(0, completed.returncode, completed.stdout + completed.stderr)
                    self.assertTrue(json.loads(completed.stdout)["ok"])
                    self.assertEqual(before, helpers.tree_digest(repo))

    def test_acceptance_30_revisit(self) -> None:
        with helpers.vault_dir() as temp:
            repo = helpers.Path(temp)
            due_value = helpers.candidate(revisit_on="2026-08-13")
            due = helpers.claim_result(due_value)
            future_value = helpers.candidate(
                scope="project/payments",
                key="settlement-owner",
                decision="정산은 ledger service가 소유한다.",
                title="정산 소유권",
                candidate_id="cand_550e8400e29b41d4a716446655440001",
                revisit_on="2027-01-01",
            )
            future = helpers.claim_result(future_value, identifier="ctx_123e4567e89b42d3a456426614174001")
            helpers.write_decision_area(repo, current=[pair(due), pair(future)])
            before = helpers.tree_digest(repo)
            result = decision_cli.revisit_decisions(repo, due=True, as_of="2026-08-14")
            self.assertEqual("2026-08-14", result["as_of"])
            self.assertEqual(["ctx_550e8400e29b41d4a716446655440000"], [item["id"] for item in result["items"]])
            self.assertTrue(result["items"][0]["due"])
            self.assertEqual("review_only", result["items"][0]["proposal"])
            self.assertFalse(result["state_changed"])
            self.assertEqual(before, helpers.tree_digest(repo))

    def test_stage1_search_is_index_only_and_brief_reads_selected_decisions(self) -> None:
        with helpers.vault_dir() as temp:
            repo = helpers.Path(temp)
            first = helpers.claim_result()
            second_value = helpers.candidate(
                scope="project/payments",
                key="settlement-owner",
                decision="정산은 ledger service가 소유한다.",
                title="정산 결정",
                candidate_id="cand_550e8400e29b41d4a716446655440001",
            )
            second = helpers.claim_result(second_value, identifier="ctx_123e4567e89b42d3a456426614174001")
            helpers.write_decision_area(repo, current=[pair(first), pair(second)])
            with mock.patch.object(decision_cli, "_record", side_effect=AssertionError("Stage 1 opened an artifact")):
                search = decision_cli.search_decisions(repo, query="정산")
            self.assertEqual(["ctx_123e4567e89b42d3a456426614174001"], [item["id"] for item in search["items"]])

            opened: list[str] = []
            original = decision_cli._record

            def recording(repo_path, row):
                opened.append(row["id"])
                return original(repo_path, row)

            with mock.patch.object(decision_cli, "_record", side_effect=recording):
                brief = decision_cli.brief_decisions(repo, identifiers=["ctx_550e8400e29b41d4a716446655440000"])
            self.assertEqual(["ctx_550e8400e29b41d4a716446655440000"], opened)
            self.assertEqual(set(decision_cli.CORE_SECTIONS), set(brief["items"][0]["sections"]))

    def test_brief_is_bounded_to_8_kib_and_keeps_only_complete_items(self) -> None:
        with helpers.vault_dir() as temp:
            repo = helpers.Path(temp)
            current: list[tuple[str, str]] = []
            identifiers = (
                "ctx_550e8400e29b41d4a716446655440000",
                "ctx_123e4567e89b42d3a456426614174001",
                "ctx_987e6543e21b42d3a456426614174002",
            )
            for index, identifier in enumerate(identifiers):
                value = helpers.candidate(
                    scope=f"project/area{index}",
                    key=f"owner-{index}",
                    decision=f"결정 {index}: " + "가" * 180,
                    rationale="나" * 180,
                    alternatives=["다" * 180],
                    title=f"결정 {index}",
                    candidate_id=f"cand_550e8400e29b41d4a7164466554400{index:02d}",
                )
                current.append(pair(helpers.claim_result(value, identifier=identifier)))
            helpers.write_decision_area(repo, current=current)
            result = decision_cli.brief_decisions(repo, query="결정", max_bytes=1400)
            self.assertTrue(result["truncated"])
            self.assertGreater(result["omitted"], 0)
            self.assertLessEqual(len(decision_cli.canonical_json(result["items"]).encode("utf-8")), 1400)
            for item in result["items"]:
                self.assertEqual(set(decision_cli.CORE_SECTIONS), set(item["sections"]))
            with self.assertRaises(decision_cli.DecisionError):
                decision_cli.brief_decisions(repo, query="결정", max_bytes=8193)

    def test_history_brief_marks_do_not_follow_and_reason(self) -> None:
        with helpers.vault_dir() as temp:
            repo = helpers.Path(temp)
            active = helpers.claim_result()
            helpers.write_decision_area(repo, current=[pair(active)])
            withdrawn = decision_cli.build_withdraw_result(
                repo,
                "ctx_550e8400e29b41d4a716446655440000",
                "정책 변경",
                retired_at="2026-08-14T09:00:00+09:00",
            )
            history_path, history_content = pair(withdrawn, current=False)
            helpers.write_decision_area(repo, history=[(history_path, history_content)])
            result = decision_cli.brief_decisions(
                repo,
                identifiers=["ctx_550e8400e29b41d4a716446655440000"],
                include_history=True,
            )
            self.assertTrue(result["items"][0]["do_not_follow"])
            self.assertEqual("withdrawn", result["items"][0]["lifecycle_reason"])
            read = decision_cli.read_decision(repo, "ctx_550e8400e29b41d4a716446655440000")
            self.assertTrue(read["do_not_follow"])

    def test_acceptance_48_spec_view_projection(self) -> None:
        with helpers.vault_dir() as temp:
            repo = helpers.Path(temp)

            def make(
                *,
                identifier: str,
                candidate_id: str,
                scope: str,
                key: str,
                created_at: str,
                title: str,
            ) -> tuple[str, str]:
                value = helpers.candidate(
                    scope=scope,
                    key=key,
                    decision=f"{title} 결정",
                    rationale=f"{title} 취지",
                    title=title,
                    candidate_id=candidate_id,
                )
                return pair(helpers.claim_result(value, identifier=identifier, created_at=created_at))

            ancestor = make(
                identifier="ctx_123e4567e89b42d3a456426614174001",
                candidate_id="cand_00000000000000000000000000000001",
                scope="project",
                key="project-policy",
                created_at="2026-08-13T17:00:00+09:00",
                title="프로젝트 정책",
            )
            exact = make(
                identifier="ctx_550e8400e29b41d4a716446655440000",
                candidate_id="cand_00000000000000000000000000000002",
                scope="project/auth",
                key="auth-policy",
                created_at="2026-08-13T18:00:00+09:00",
                title="인증 정책",
            )
            descendant = make(
                identifier="ctx_987e6543e21b42d3a456426614174002",
                candidate_id="cand_00000000000000000000000000000003",
                scope="project/auth/mobile",
                key="mobile-policy",
                created_at="2026-08-13T19:00:00+09:00",
                title="모바일 정책",
            )
            sibling = make(
                identifier="ctx_aaaaaaaaaaaa4aaa8aaaaaaaaaaaaaaa",
                candidate_id="cand_00000000000000000000000000000004",
                scope="project/payments",
                key="payments-policy",
                created_at="2026-08-13T16:00:00+09:00",
                title="결제 정책",
            )
            lexical_prefix = make(
                identifier="ctx_bbbbbbbbbbbb4bbb9bbbbbbbbbbbbbbb",
                candidate_id="cand_00000000000000000000000000000005",
                scope="project/authentication",
                key="authentication-policy",
                created_at="2026-08-13T15:00:00+09:00",
                title="문자열 prefix 정책",
            )
            history_current = make(
                identifier="ctx_cccccccccccc4cccaccccccccccccccc",
                candidate_id="cand_00000000000000000000000000000006",
                scope="project/auth",
                key="retired-policy",
                created_at="2026-08-13T14:00:00+09:00",
                title="폐기된 정책",
            )
            history_fm, history_sections = decision_cli.parse_document(history_current[1])
            history_fm.update(
                retired_at="2026-08-14T09:00:00+09:00",
                retired_reason="withdrawn",
                retirement_note="더는 따르지 않는다.",
            )
            history = (
                decision_cli._history_path(history_current[0], history_fm["id"]),
                decision_cli.render_document(history_fm, history_sections),
            )
            helpers.write_decision_area(
                repo,
                current=[descendant, sibling, exact, lexical_prefix, ancestor],
                history=[history],
            )

            before = helpers.tree_digest(repo)
            opened: list[str] = []
            original_record = decision_cli._record

            def recording(repo_path, row):
                opened.append(row["id"])
                return original_record(repo_path, row)

            with mock.patch.object(decision_cli, "_record", side_effect=recording):
                first = decision_cli.spec_view(repo, scope=" Project/Auth/ ")
                second = decision_cli.spec_view(repo, scope="project/auth")
            self.assertEqual(decision_cli.canonical_json(first), decision_cli.canonical_json(second))
            self.assertEqual(before, helpers.tree_digest(repo))
            self.assertEqual(
                [
                    "ctx_123e4567e89b42d3a456426614174001",
                    "ctx_550e8400e29b41d4a716446655440000",
                    "ctx_987e6543e21b42d3a456426614174002",
                ],
                [item["id"] for item in first["items"]],
            )
            self.assertNotIn("ctx_cccccccccccc4cccaccccccccccccccc", opened)
            self.assertTrue(all(set(item["sections"]) == {"Decision", "Rationale"} for item in first["items"]))
            self.assertEqual(0, first["retrieval"]["history_body_reads"])
            self.assertFalse(first["physical_write"])
            self.assertEqual("ephemeral", first["projection"])
            self.assertLessEqual(
                len(decision_cli._serialize_success(first, json_mode=True).encode("utf-8")),
                32 * 1024,
            )

            zero = decision_cli.spec_view(repo, scope="unrelated")
            self.assertEqual([], zero["items"])
            self.assertEqual(0, zero["omitted_count"])
            self.assertEqual(0, zero["retrieval"]["body_reads"])

            completed = run_spec_view_cli(repo, scope="project/auth")
            cli_before = helpers.tree_digest(repo)
            repeated = run_spec_view_cli(repo, scope="project/auth")
            self.assertEqual(0, completed.returncode, completed.stdout + completed.stderr)
            self.assertEqual(completed.stdout, repeated.stdout)
            cli_result = json.loads(completed.stdout)["result"]
            self.assertEqual(first, cli_result)
            self.assertEqual(decision_cli._serialize_success(cli_result, json_mode=True), completed.stdout)
            self.assertLessEqual(len(completed.stdout.encode("utf-8")), 32 * 1024)
            self.assertEqual(cli_before, helpers.tree_digest(repo))

    def test_spec_view_cap_omits_complete_low_rank_items(self) -> None:
        with helpers.vault_dir() as temp:
            repo = helpers.Path(temp)
            current: list[tuple[str, str]] = []
            for number in range(6):
                value = helpers.candidate(
                    scope=f"project/auth/area-{number}",
                    key=f"policy-{number}",
                    decision=f"결정 {number}: " + "가" * 200,
                    rationale=f"취지 {number}: " + "나" * 250,
                    title=f"정책 {number}",
                    candidate_id=f"cand_{number:032x}",
                )
                identifier = "ctx_" + uuid.UUID(f"00000000-0000-4000-8000-{number:012x}").hex
                current.append(
                    pair(
                        helpers.claim_result(
                            value,
                            identifier=identifier,
                            created_at=f"2026-08-13T{number + 10:02d}:00:00+09:00",
                        )
                    )
                )
            helpers.write_decision_area(repo, current=list(reversed(current)))
            before = helpers.tree_digest(repo)
            result = decision_cli.spec_view(repo, scope="project/auth", max_bytes=3800)
            self.assertGreater(result["returned"], 0)
            self.assertLess(result["returned"], len(current))
            self.assertEqual(len(current) - result["returned"], result["omitted_count"])
            self.assertTrue(result["truncated"])
            self.assertLessEqual(
                len(decision_cli._serialize_success(result, json_mode=True).encode("utf-8")),
                3800,
            )
            self.assertEqual(
                sorted((item["created_at"], item["id"]) for item in result["items"]),
                [(item["created_at"], item["id"]) for item in result["items"]],
            )
            for item in result["items"]:
                self.assertTrue(item["sections"]["Decision"].endswith("가" * 200))
                self.assertTrue(item["sections"]["Rationale"].endswith("나" * 250))
            self.assertEqual(result["returned"] + 1, result["retrieval"]["body_reads"])
            self.assertEqual(before, helpers.tree_digest(repo))

    def test_spec_view_default_cap_bounds_raw_cli_envelope(self) -> None:
        with helpers.vault_dir() as temp:
            repo = helpers.Path(temp)
            current: list[tuple[str, str]] = []
            for number in range(22):
                rationale_chars = 624 if number == 21 else 1000
                value = helpers.candidate(
                    scope="p",
                    key=f"k{number}",
                    decision="d" * 300,
                    rationale="r" * rationale_chars,
                    alternatives=["x"],
                    title=f"t{number}",
                    candidate_id=f"cand_{number:032x}",
                )
                identifier = "ctx_" + uuid.UUID(f"00000000-0000-4000-8000-{number:012x}").hex
                current.append(
                    pair(
                        helpers.claim_result(
                            value,
                            identifier=identifier,
                            created_at=f"2026-08-{13 + number // 14:02d}T{10 + number % 14:02d}:00:00+09:00",
                            filename=f"a{number}.md",
                        )
                    )
                )
            helpers.write_decision_area(repo, current=current)

            completed = run_spec_view_cli(repo, scope="p")
            self.assertEqual(0, completed.returncode, completed.stdout + completed.stderr)
            result = json.loads(completed.stdout)["result"]
            self.assertEqual(decision_cli._serialize_success(result, json_mode=True), completed.stdout)
            self.assertLessEqual(len(completed.stdout.encode("utf-8")), 32 * 1024)
            self.assertEqual(21, result["returned"])
            self.assertEqual(1, result["omitted_count"])
            self.assertEqual(22, result["retrieval"]["body_reads"])

    def test_spec_view_cap_counts_raw_nfd_utf8_bytes(self) -> None:
        with helpers.vault_dir() as temp:
            repo = helpers.Path(temp)
            nfd = unicodedata.normalize("NFD", "가")
            current: list[tuple[str, str]] = []
            for number in range(16):
                value = helpers.candidate(
                    scope="p",
                    key=f"k{number}",
                    decision=nfd * 150,
                    rationale=nfd * 450,
                    alternatives=["x"],
                    title=f"t{number}",
                    candidate_id=f"cand_{number:032x}",
                )
                identifier = "ctx_" + uuid.UUID(f"00000000-0000-4000-8000-{number:012x}").hex
                current.append(
                    pair(
                        helpers.claim_result(
                            value,
                            identifier=identifier,
                            created_at=f"2026-08-{13 + number // 14:02d}T{10 + number % 14:02d}:00:00+09:00",
                            filename=f"a{number}.md",
                        )
                    )
                )
            helpers.write_decision_area(repo, current=current)

            completed = run_spec_view_cli(repo, scope="p")
            self.assertEqual(0, completed.returncode, completed.stdout + completed.stderr)
            result = json.loads(completed.stdout)["result"]
            raw_bytes = len(completed.stdout.encode("utf-8"))
            canonical_bytes = len(
                (decision_cli.canonical_json({"ok": True, "result": result}) + "\n").encode("utf-8")
            )
            self.assertEqual(decision_cli._serialize_success(result, json_mode=True), completed.stdout)
            self.assertIn(nfd, completed.stdout)
            self.assertGreater(raw_bytes, canonical_bytes)
            self.assertLessEqual(raw_bytes, 32 * 1024)
            self.assertEqual(8, result["returned"])
            self.assertEqual(8, result["omitted_count"])

    def test_spec_view_rechecks_envelope_at_body_read_digit_boundary(self) -> None:
        with helpers.vault_dir() as temp:
            repo = helpers.Path(temp)
            current: list[tuple[str, str]] = []
            for number in range(10):
                value = helpers.candidate(
                    scope="p",
                    key=f"k{number}",
                    decision="d" + ("z" * 115 if number == 0 else ""),
                    rationale="r",
                    alternatives=["x"],
                    title=f"t{number}",
                    candidate_id=f"cand_{number:032x}",
                )
                identifier = "ctx_" + uuid.UUID(f"00000000-0000-4000-8000-{number:012x}").hex
                current.append(
                    pair(
                        helpers.claim_result(
                            value,
                            identifier=identifier,
                            created_at=f"2026-08-13T{10 + number:02d}:00:00+09:00",
                            filename=f"a{number}.md",
                        )
                    )
                )
            helpers.write_decision_area(repo, current=current)

            full = decision_cli.spec_view(repo, scope="p")
            nine_items = {
                **full,
                "items": full["items"][:9],
                "returned": 9,
                "omitted_count": 1,
                "truncated": True,
                "max_bytes": 2251,
                "retrieval": {**full["retrieval"], "body_reads": 9},
            }
            self.assertEqual(2251, len(decision_cli._serialize_success(nine_items, json_mode=True).encode("utf-8")))
            nine_items["retrieval"]["body_reads"] = 10
            self.assertEqual(2252, len(decision_cli._serialize_success(nine_items, json_mode=True).encode("utf-8")))

            completed = run_spec_view_cli(repo, scope="p", max_bytes=2251)
            self.assertEqual(0, completed.returncode, completed.stdout + completed.stderr)
            result = json.loads(completed.stdout)["result"]
            self.assertEqual(decision_cli._serialize_success(result, json_mode=True), completed.stdout)
            self.assertLessEqual(len(completed.stdout.encode("utf-8")), 2251)
            self.assertEqual(8, result["returned"])
            self.assertEqual(2, result["omitted_count"])
            self.assertEqual(10, result["retrieval"]["body_reads"])


if __name__ == "__main__":
    unittest.main()
