#!/usr/bin/env python3
"""Guard centralized Bobbin guidance without duplicating policy in UI starter prompts."""
from __future__ import annotations

import unittest
from pathlib import Path

ROOT = next(p for p in Path(__file__).resolve().parents if (p / "pytest.ini").is_file())
PACKAGE = ROOT / "plugins/bobbin"


class BehaviorContractParityTests(unittest.TestCase):
    def test_every_skill_resolves_the_shared_recording_policy(self) -> None:
        skills = sorted((PACKAGE / "skills").glob("*/SKILL.md"))
        self.assertEqual(10, len(skills))
        for path in skills:
            with self.subTest(skill=path.parent.name):
                text = path.read_text()
                self.assertIn("recording-policy.md", text)
                relative = "references/recording-policy.md" if path.parent.name == "context" else "../context/references/recording-policy.md"
                self.assertTrue((path.parent / relative).is_file())
        policy = (PACKAGE / "skills/context/references/recording-policy.md").read_text()
        for marker in ("explicit", "auto", "adaptive", "Semantic attestation", "approval-source policy", "policy-decision", "policy-reason"):
            self.assertIn(marker, policy)

    def test_mechanical_edit_zero_path_and_ephemeral_audit_remain(self) -> None:
        policy = (PACKAGE / "rules/context-policy.md").read_text()
        for marker in ("Audit each user turn's new meaning once", "zero context tool calls",
                       "skip AGENTS/guidance discovery", "exclude `context/`", "one task subtree",
                       "Never use `.`", "`--hidden`", "repository-wide globs", "session-only ledger"):
            self.assertIn(marker, policy)

    def test_language_and_semantic_authority_do_not_depend_on_recording_mode(self) -> None:
        policy = (PACKAGE / "rules/context-policy.md").read_text()
        for marker in ("explicit user language choice", "OS locale is not authoritative",
                       "machine-readable surfaces in canonical English",
                       "actual bodies, scope, and rationale", "not semantic evidence",
                       "reassessment, not implementation", "user commitment",
                       "Recording policy grants no unrelated execution"):
            self.assertIn(marker, policy)

    def test_decision_actual_sections_and_revisit_classification_remain(self) -> None:
        for relative in ("skills/decision/SKILL.md", "skills/decision/SKILL.ko.md", "rules/decision-policy.md"):
            with self.subTest(surface=relative):
                text = (PACKAGE / relative).read_text()
                for marker in ("Decision", "Rationale", "Rejected alternatives", "Revisit conditions",
                               "satisfied", "no evidence", "ambiguous", "conflict", "supersede"):
                    self.assertIn(marker, text)
        english = (PACKAGE / "skills/decision/SKILL.md").read_text()
        for marker in ("one exact-slot `decision_cli.py check`",
                       "Reuse sections returned by `check` in the same turn",
                       "do not call `read`, `spec-view`, or another context read",
                       "requested conflicting action is not evidence",
                       "explicit binary question", "verbatim",
                       "Keep means the action is not performed"):
            self.assertIn(marker, english)
        korean = (PACKAGE / "skills/decision/SKILL.ko.md").read_text()
        self.assertIn("user response에 그대로", korean)
        self.assertIn("keep이면 수행하지 않고 supersede면 그 명시적 선택 뒤에만", korean)

    def test_authorization_and_transport_integrity_remain_separate(self) -> None:
        policy = (PACKAGE / "rules/context-policy.md").read_text()
        for marker in ("sole writer", "internal preview and unchanged apply", "same response",
                       "project/vault identity", "CAS", "atomic-write", "Never regenerate after approval",
                       "generic acknowledgement", "rendered storage body"):
            self.assertIn(marker, policy)
        self.assertNotIn("approval_digest", policy)
        self.assertIn("approval_digest", (PACKAGE / "skills/context/references/recording-policy.md").read_text())

    def test_feature_disabling_is_not_data_deletion_or_policy_inheritance(self) -> None:
        policy = (PACKAGE / "rules/context-policy.md").read_text()
        for marker in ("project-local", "Shared vaults do not share settings",
                       "Only a direct user request", "Only enabled owners",
                       "Disabled features preserve records and explicit historical reads"):
            self.assertIn(marker, policy)


if __name__ == "__main__":
    unittest.main()
