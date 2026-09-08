#!/usr/bin/env python3
from __future__ import annotations

import importlib.util
import json
import re
import sys
import unittest
from pathlib import Path


ROOT = next(p for p in Path(__file__).resolve().parents if (p / "pytest.ini").is_file())


def load(name: str, relative: str):
    spec = importlib.util.spec_from_file_location(name, ROOT / relative)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


active_language = load(
    "active_language_contract",
    "plugins/bobbin/skills/context/scripts/active_language.py",
)
core_cli = load("context_cli_language", "plugins/bobbin/skills/context/scripts/context_cli.py")
decision_cli = load("decision_cli_language", "plugins/bobbin/skills/decision/scripts/decision_cli.py")
assumption_cli = load("assumption_cli_language", "plugins/bobbin/skills/assumption/scripts/assumption_cli.py")
term_cli = load("term_cli_language", "plugins/bobbin/skills/term/scripts/term_cli.py")


class ActiveLanguageContractTests(unittest.TestCase):
    def test_signal_precedence_and_fallback_are_executable(self) -> None:
        cases = (
            ({"current_request": "en", "explicit_pin": "ko", "host_preference": "ko", "conversation_language": "ko"}, "en"),
            ({"explicit_pin": "ko", "host_preference": "en", "conversation_language": "en"}, "ko"),
            ({"explicit_pin": "auto", "host_preference": "ko", "conversation_language": "en"}, "ko"),
            ({"host_preference": "auto", "conversation_language": "ko"}, "ko"),
            ({}, "en"),
            ({"current_request": "pt_BR"}, "pt-br"),
        )
        for inputs, expected in cases:
            with self.subTest(inputs=inputs):
                self.assertEqual(expected, active_language.resolve_active_language(**inputs))

    def test_incidental_foreign_text_and_os_locale_do_not_switch_language(self) -> None:
        for signal in ("code", "filename", "identifier", "quotation", "isolated_foreign_term", "os_locale", "unknown"):
            with self.subTest(signal=signal):
                self.assertFalse(active_language.signal_may_switch_language(signal))
        for signal in ("current_request", "explicit_pin", "host_preference", "conversation_language"):
            self.assertTrue(active_language.signal_may_switch_language(signal))

    def test_approval_gate_is_semantic_and_language_independent(self) -> None:
        explicit_decisions = (
            "Use Supabase for authentication and storage; this is the decision.",
            "인증과 저장소는 Supabase를 쓰는 것으로 결정해.",
        )
        for decision in explicit_decisions:
            with self.subTest(decision=decision):
                self.assertTrue(active_language.qualifies_as_capture_approval(
                    semantic_payload_confirmed=True,
                    scope_identified=True,
                    lifecycle_effects_identified=True,
                    direct=True,
                    explicit=True,
                    unconditional=True,
                ))

        rejected = (
            ("Okay.", {"generic_acknowledgement": True}),
            ("알겠어요.", {"generic_acknowledgement": True}),
            ("Looks good.", {"praise_only": True}),
            ("좋네요.", {"praise_only": True}),
            ("Yes, if the title changes.", {"unconditional": False}),
            ("네, 제목을 바꿔 주세요.", {"edit_request": True}),
            ("By the way, what is next?", {"topic_change": True}),
        )
        for answer, override in rejected:
            inputs = {
                "semantic_payload_confirmed": True,
                "scope_identified": True,
                "lifecycle_effects_identified": True,
                "direct": True,
                "explicit": True,
                "unconditional": True,
                **override,
            }
            with self.subTest(answer=answer):
                self.assertFalse(active_language.qualifies_as_capture_approval(**inputs))

        for unresolved in (
            {"semantic_payload_confirmed": False},
            {"scope_identified": False},
            {"lifecycle_effects_identified": False},
            {"semantic_delta": True},
        ):
            inputs = {
                "semantic_payload_confirmed": True,
                "scope_identified": True,
                "lifecycle_effects_identified": True,
                "direct": True,
                "explicit": True,
                "unconditional": True,
                **unresolved,
            }
            with self.subTest(unresolved=unresolved):
                self.assertFalse(active_language.qualifies_as_capture_approval(**inputs))

    def test_canonical_runtime_sources_are_english_and_share_one_contract(self) -> None:
        canonical_skills = sorted(ROOT.glob("plugins/*/skills/*/SKILL.md"))
        rules = sorted(ROOT.glob("plugins/*/rules/*.md"))
        self.assertEqual(10, len(canonical_skills))
        for path in [*canonical_skills, *rules]:
            text = path.read_text(encoding="utf-8")
            self.assertIsNone(re.search(r"[가-힣]", text), path)
            self.assertIn("active language", text.casefold(), path)

        contract = (ROOT / "plugins/bobbin/skills/context/references/active-language.md").read_text(encoding="utf-8")
        ordered = (
            "explicit user language choice",
            "host's preferred response language",
            "established language of the current conversation",
            "English when none",
        )
        positions = [contract.index(token) for token in ordered]
        self.assertEqual(sorted(positions), positions)
        for token in ("OS locale", "Code, identifiers, filenames, quotations", "machine-readable surfaces", "semantic and language-independent"):
            self.assertIn(token, contract)

        # Starter prompts are user actions, not duplicated policy instructions.
        self.assertIn("machine-readable surfaces in canonical English", core_cli.POLICY_BODY)

    def test_managed_policy_source_and_embedded_copy_are_identical(self) -> None:
        rule = (ROOT / "plugins/bobbin/rules/context-policy.md").read_text(encoding="utf-8")
        begin = core_cli.POLICY_BEGIN
        end = core_cli.POLICY_END
        managed = rule[rule.index(begin):rule.index(end) + len(end)]
        self.assertEqual(core_cli.POLICY_BODY, managed)
        agents = (ROOT / "AGENTS.md").read_text(encoding="utf-8")
        self.assertEqual(1, agents.count(core_cli.POLICY_BODY))

    def test_public_docs_cannot_restore_korean_only_canonical_headings(self) -> None:
        decision_root = ROOT / "plugins/bobbin"
        protocol = (decision_root / "skills/decision/references/decision-protocol.md").read_text(encoding="utf-8")
        self.assertIn("canonical required body sections are `Decision`, `Rationale`, and `Rejected alternatives`", protocol)
        self.assertIn("canonical `Decision` and `Rationale` fields", protocol)
        self.assertIn("legacy read and round-trip aliases only", protocol)
        self.assertNotIn("required body sections are `결정`, `취지`, and `반려대안`", protocol)
        self.assertNotIn("materializes only `결정` and `취지`", protocol)

        decision_korean_docs = (
            ROOT / "docs/modules/decision.ko.md",
            decision_root / "skills/decision/SKILL.ko.md",
            decision_root / "skills/decision/references/decision-protocol.ko.md",
        )
        for path in decision_korean_docs:
            text = path.read_text(encoding="utf-8")
            self.assertIn("`Decision`", text, path)
            self.assertIn("`Rationale`", text, path)
            self.assertIn("legacy", text, path)

        plugin_docs = {
            "context-assumption": ("`Assumption`, `Basis`", ("`가정`", "`근거`")),
            "context-term": ("`Definition`", ("`정의`",)),
        }
        for plugin, (canonical, legacy_aliases) in plugin_docs.items():
            root = ROOT / "docs/modules"
            kind = plugin.removeprefix("context-")
            english = (root / f"{kind}.md").read_text(encoding="utf-8")
            korean = (root / f"{kind}.ko.md").read_text(encoding="utf-8")
            self.assertIn(f"[한국어](./{kind}.ko.md)", english, plugin)
            self.assertIn(f"[English](./{kind}.md)", korean, plugin)
            self.assertIn(canonical, english, plugin)
            self.assertIn(canonical, korean, plugin)
            self.assertIn("canonical english", english.casefold(), plugin)
            self.assertIn("legacy Korean", english, plugin)
            for alias in legacy_aliases:
                self.assertIn(alias, english, plugin)
                self.assertIn(alias, korean, plugin)

    def test_new_artifacts_use_english_structure_and_legacy_korean_round_trips(self) -> None:
        core_cases = (
            (
                {
                    "schema": "context-snapshot/v1", "id": "ctx_550e8400e29b41d4a716446655440000",
                    "title": "handoff", "summary": "resume later", "created_at": "2026-08-25T00:00:00+09:00",
                    "updated_at": "2026-08-25T00:00:00+09:00", "captured_from": "manual",
                },
                {"Current context": "상태", "Open items": "- 항목", "Next steps": "- 다음"},
                {"Current context": "현재 맥락", "Open items": "열린 항목", "Next steps": "다음 단계"},
            ),
            (
                {
                    "schema": "context-observation/v1", "id": "ctx_550e8400e29b41d4a716446655440001",
                    "title": "evidence", "summary": "reusable evidence", "created_at": "2026-08-25T00:00:00+09:00",
                    "captured_from": "manual",
                },
                {"Observation": "관찰 본문", "Evidence": "- 근거"},
                {"Observation": "관찰", "Evidence": "근거"},
            ),
        )
        for frontmatter, sections, aliases in core_cases:
            english = core_cli.render_document(frontmatter, sections)
            self.assertTrue(all(f"## {name}" in english for name in sections))
            legacy = english
            for canonical, korean in aliases.items():
                legacy = legacy.replace(f"## {canonical}\n", f"## {korean}\n")
            parsed = core_cli.parse_document(legacy)
            self.assertEqual(legacy, core_cli.render_document(parsed.frontmatter, parsed.sections))

        decision_frontmatter = {
            "schema": "context-decision/v1", "id": "ctx_550e8400e29b41d4a716446655440002",
            "title": "policy", "summary": "selected policy", "created_at": "2026-08-25T00:00:00+09:00",
            "captured_from": "manual", "scope": "project", "decision_key": "policy",
        }
        decision_sections = {"Decision": "선택", "Rationale": "취지", "Rejected alternatives": "- 대안"}
        decision_english = decision_cli.render_document(decision_frontmatter, decision_sections)
        decision_legacy = decision_english
        for canonical, korean in {"Decision": "결정", "Rationale": "취지", "Rejected alternatives": "반려대안"}.items():
            decision_legacy = decision_legacy.replace(f"## {canonical}\n", f"## {korean}\n")
        decision_parsed = decision_cli.parse_document(decision_legacy)
        self.assertEqual(decision_legacy, decision_cli.render_document(*decision_parsed))

        assumption_frontmatter = {
            "schema": "context-assumption/v1", "id": "ctx_550e8400e29b41d4a716446655440003",
            "title": "premise", "summary": "unverified premise", "created_at": "2026-08-25T00:00:00+09:00",
            "captured_from": "manual", "scope": "project",
        }
        assumption_english = assumption_cli.render_document(assumption_frontmatter, {"Assumption": "전제", "Basis": "- 근거"})
        assumption_legacy = assumption_english.replace("## Assumption\n", "## 가정\n").replace("## Basis\n", "## 근거\n")
        assumption_parsed = assumption_cli.parse_document(assumption_legacy)
        self.assertEqual(assumption_legacy, assumption_cli.render_document(*assumption_parsed))

        term_frontmatter = {
            "schema": "context-term/v1", "id": "ctx_550e8400e29b41d4a716446655440004",
            "title": "BFF", "summary": "project term", "created_at": "2026-08-25T00:00:00+09:00",
            "captured_from": "manual", "scope": "project", "term": "BFF", "term_key": "bff",
        }
        term_english = term_cli.render_document(term_frontmatter, {"Definition": "프로젝트 정의"})
        term_legacy = term_english.replace("## Definition\n", "## 정의\n")
        term_parsed = term_cli.parse_document(term_legacy)
        self.assertEqual(term_legacy, term_cli.render_document(*term_parsed))


if __name__ == "__main__":
    unittest.main()
