#!/usr/bin/env python3
from __future__ import annotations

import json
import pathlib
import subprocess
import sys
import tempfile
import unittest

import test_decision_schema as helpers


decision_cli = helpers.decision_cli
ROOT = pathlib.Path(__file__).resolve().parents[3]
CORE = ROOT / "plugins/bobbin/skills/context/scripts/context_cli.py"
DECISION_INIT = ROOT / "plugins/bobbin/skills/init/scripts/decision_init.py"
WORKFLOW = ROOT / "plugins/bobbin/skills/decision/scripts/decision_workflow.py"
CHECK = ROOT / "plugins/bobbin/skills/decision/scripts/decision_cli.py"


def run(argv: list[str], cwd: pathlib.Path) -> tuple[int, dict]:
    completed = subprocess.run(
        [sys.executable, *argv, "--json"],
        cwd=cwd,
        capture_output=True,
        text=True,
    )
    return completed.returncode, json.loads(completed.stdout)


class KoreanDiscoveryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.repo = pathlib.Path(self.temp.name)
        (self.repo / "PROJECT.md").write_text("demo\n", encoding="utf-8")
        self.assertEqual(0, run([str(CORE), "init", "--host", "codex"], self.repo)[0])
        self.assertEqual(
            0,
            run(
                [str(DECISION_INIT), "--host", "codex", "--core-cli", str(CORE)],
                self.repo,
            )[0],
        )

    def tearDown(self) -> None:
        self.temp.cleanup()

    def record(
        self,
        *,
        title: str,
        summary: str,
        key: str,
        decision: str,
        rationale: str = "현재 제품 제약과 운영 조건을 반영한다.",
        alternative: str = "다른 접근은 현재 범위에서 반려한다.",
    ) -> dict:
        rc, payload = run(
            [
                str(WORKFLOW),
                "record",
                "--host",
                "codex",
                "--core-cli",
                str(CORE),
                "--inline",
                "--approved",
                "--title",
                title,
                "--summary",
                summary,
                "--scope",
                "repository",
                "--decision-key",
                key,
                "--commitment-evidence",
                "user: 현재 따를 결정으로 확정",
                "--sec-decision",
                decision,
                "--sec-rationale",
                rationale,
                "--sec-alternatives",
                alternative,
                "--attest-explicit-choice",
                "--attest-scope-identified",
                "--attest-commitment-present",
            ],
            self.repo,
        )
        self.assertEqual(0, rc, payload)
        return payload["result"]

    def check(self, statement: str) -> dict:
        rc, payload = run([str(CHECK), "check", "--statement", statement], self.repo)
        self.assertEqual(0, rc, payload)
        return payload["result"]

    def assert_lexical_hit(self, statement: str, title: str) -> dict:
        result = self.check(statement)
        current = result["comparison_input"]["current"]
        self.assertTrue(current, result)
        self.assertEqual(title, current[0]["title"], [item["title"] for item in current])
        self.assertTrue(
            any(reason.startswith("lexical:") for reason in current[0]["retrieval_reasons"]),
            current[0]["retrieval_reasons"],
        )
        return result

    def assert_healthy_miss(self, statement: str) -> None:
        result = self.check(statement)
        self.assertEqual([], result["comparison_input"]["current"])
        self.assertEqual(0, result["retrieval"]["body_reads"])

    def test_readme_login_and_signup_queries_hit_a_record_created_decision(self) -> None:
        title = "첫 버전 인증 정책"
        self.record(
            title=title,
            summary="첫 버전은 계정 없이 바로 사용한다.",
            key="first-version-auth",
            decision="첫 버전은 회원가입과 로그인 없이 동작한다.",
            alternative="회원가입과 로그인을 첫 버전에 추가하는 안은 반려한다.",
        )

        for statement in (
            "로그인 붙이자",
            "로그인 기능을 추가하기 전에 확인",
            "회원가입을 넣자",
            "회원가입 추가",
        ):
            with self.subTest(statement=statement):
                self.assert_lexical_hit(statement, title)

    def test_particle_variants_share_one_canonical_term_and_stopwords_drop_after_stripping(self) -> None:
        for suffix in ("을", "이", "은", "으로", "과", "에서"):
            with self.subTest(suffix=suffix):
                self.assertEqual(["로그인"], decision_cli._canonical_terms("로그인" + suffix))
        self.assertEqual([], decision_cli._canonical_terms("것은"))

    def test_two_syllable_and_mixed_language_queries_hit(self) -> None:
        payment_title = "결제 수단"
        self.record(
            title=payment_title,
            summary="결제 수단은 카드로 제한한다.",
            key="payment-method",
            decision="결제 수단은 카드다.",
        )
        self.assert_lexical_hit("결제를 바꾸자", payment_title)

        login_title = "로그인 경계"
        self.record(
            title=login_title,
            summary="로그인 책임 경계를 유지한다.",
            key="login-boundary",
            decision="로그인 처리는 서버 경계가 소유한다.",
        )
        self.assert_lexical_hit("add 로그인", login_title)

    def test_unrelated_query_does_not_open_record_bodies(self) -> None:
        for number, noun in enumerate(("검색", "알림", "프로필")):
            self.record(
                title=f"{noun} 정책",
                summary=f"{noun} 기능의 현재 동작을 유지한다.",
                key=f"policy-{number}",
                decision=f"{noun} 기능은 기존 경계에서 동작한다.",
            )
        self.assert_healthy_miss("배포 파이프라인 수정")

    def test_common_korean_terms_do_not_crowd_out_the_distinctive_target(self) -> None:
        for number in range(50):
            self.record(
                title=f"기능 설정 {number:02d}",
                summary=f"사용 기능 추가와 버전 설정에 관한 공통 정책 {number:02d}",
                key=f"common-policy-{number:02d}",
                decision=f"기능 추가와 사용 설정은 공통 정책 {number:02d}를 따른다.",
            )
        target_title = "로그인 인증 경계"
        self.record(
            title=target_title,
            summary="로그인 인증 책임을 서버에 둔다.",
            key="login-owner",
            decision="로그인 인증 세션은 서버가 소유한다.",
        )

        self.assert_healthy_miss("배포 파이프라인 수정")
        result = self.assert_lexical_hit("로그인 기능 추가", target_title)
        titles = [item["title"] for item in result["comparison_input"]["current"]]
        self.assertLess(titles.index(target_title), 8)

    def test_particle_like_noun_endings_do_not_create_false_lexical_hits(self) -> None:
        corona_title = "감염병 대응"
        self.record(
            title=corona_title,
            summary="코로나 대응 원칙을 유지한다.",
            key="pandemic-response",
            decision="코로나 상황에서는 원격 운영을 우선한다.",
        )
        self.assert_lexical_hit("코로나 대응 바꾸자", corona_title)
        self.assert_healthy_miss("코로 배포")

        self.record(
            title="처리 속도",
            summary="처리 속도 기준을 유지한다.",
            key="processing-speed",
            decision="처리 속도는 현재 기준을 따른다.",
        )
        self.record(
            title="판매 정책",
            summary="판매 정책을 유지한다.",
            key="sales-policy",
            decision="판매 채널은 공식 경로로 제한한다.",
        )
        self.assert_healthy_miss("속 비우기")
        self.assert_healthy_miss("판 교체")

    def test_english_canonicalization_remains_byte_identical_to_the_base_fixture(self) -> None:
        values = {
            "decision": "Keep billing inside the modular monolith.",
            "rejected_alternatives": [
                "An independent billing microservice, until an operations owner exists.",
                "Using shared queues and services.",
            ],
            "rationale": "The two-person team cannot operate distributed deployments and failure modes.",
            "revisit_when": [
                "Revisit when an operations owner and independent scaling evidence exist."
            ],
        }
        self.assertEqual(
            [
                "independent",
                "microservic",
                "owner",
                "exist",
                "shar",
                "queu",
                "servic",
                "person",
                "team",
                "cannot",
                "operat",
                "distribut",
            ],
            decision_cli.derive_search_terms(
                "Billing architecture",
                "Keep billing inside the modular monolith until operational ownership and scaling evidence justify extraction.",
                values,
            ),
        )
        self.assertEqual(
            ["extract", "bill", "independent", "deploy", "microservic", "team", "work", "autonomou"],
            decision_cli._canonical_terms(
                "Extract billing into an independently deployed microservice today so the team can work more autonomously."
            ),
        )


    def test_english_stopword_stems_keep_the_base_prefilter_only(self) -> None:
        # Base applied the English stopword list before stemming only; a stem that
        # happens to equal a stopword (makes -> make) must still be indexed and queried.
        for word, expected in (("makes", ["make"]), ("takes", ["take"]), ("keeping", ["keep"]), ("used", []), ("using", [])):
            with self.subTest(word=word):
                self.assertEqual(expected, decision_cli._canonical_terms(word))


if __name__ == "__main__":
    unittest.main()
