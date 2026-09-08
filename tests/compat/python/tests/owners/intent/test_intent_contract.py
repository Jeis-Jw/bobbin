from __future__ import annotations

import ast
import json
import tempfile
import unittest
from pathlib import Path

import intent_test_support as helpers


intent_cli = helpers.intent_cli
core_cli = helpers.core_cli


class IntentContractTests(unittest.TestCase):
    def test_descriptor_claim_and_optional_sections_are_core_valid(self) -> None:
        descriptor = intent_cli.owner_descriptor()
        self.assertEqual(
            ("context-intent", "intent", "context-intent/v1", "authoritative"),
            core_cli.validate_owner_descriptor(descriptor),
        )
        value = helpers.candidate()
        result = intent_cli.build_claim_result(
            value,
            helpers.attestation(value),
            identifier="ctx_550e8400e29b41d4a716446655440020",
            created_at="2026-09-01T01:00:00+09:00",
        )
        core_cli.validate_owner_result(result, intent_cli.intent_capability(), descriptor)
        frontmatter, sections = intent_cli.parse_document(result["artifact_drafts"][0]["content"])
        self.assertEqual("change-visibility", frontmatter["intent_key"])
        self.assertEqual(
            ["Intent", "Success criteria", "Constraints", "Revisit conditions"],
            list(sections),
        )

    def test_capture_read_search_and_supersede_work_in_filesystem_vault(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            repo = Path(temp) / "vault"
            helpers.init_repo(repo)
            helpers.capture(repo)
            found = intent_cli.search_intents(repo, query="visibility")
            self.assertEqual(1, found["returned"])
            read = intent_cli.read_intent(repo, identifier="ctx_550e8400e29b41d4a716446655440020")
            self.assertEqual("current", read["state"])

            successor = helpers.candidate(
                candidate_id="cand_550e8400e29b41d4a716446655440021",
                intent="고객과 운영자가 배포 전 변경 영향과 복구 경로를 이해할 수 있게 한다.",
                title="변경 영향 가시성 개정",
            )
            same = intent_cli.prepare_same_claim_input(
                repo,
                "ctx_550e8400e29b41d4a716446655440020",
                successor,
            )
            result = intent_cli.build_supersede_result(
                repo,
                "ctx_550e8400e29b41d4a716446655440020",
                successor,
                helpers.attestation(successor),
                same,
                helpers.same_claim_attestation(same),
                successor_id="ctx_550e8400e29b41d4a716446655440021",
                retired_at="2026-09-01T02:00:00+09:00",
            )
            self.assertEqual("supersede_current", intent_cli.validate_batch(repo, result)["transition_topology"])
            helpers.apply_result(repo, result)
            current = intent_cli.read_intent(repo, identifier="ctx_550e8400e29b41d4a716446655440021")
            history = intent_cli.read_intent(repo, identifier="ctx_550e8400e29b41d4a716446655440020")
            self.assertEqual("change-visibility", current["frontmatter"]["intent_key"])
            self.assertEqual("history", history["state"])

    def test_exact_slot_conflict_and_semantic_owner_write_boundary_fail_closed(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            repo = Path(temp) / "vault"
            helpers.init_repo(repo)
            helpers.capture(repo)
            duplicate = helpers.candidate(
                candidate_id="cand_550e8400e29b41d4a716446655440022",
                title="Duplicate direction",
            )
            result = intent_cli.build_claim_result(
                duplicate,
                helpers.attestation(duplicate),
                identifier="ctx_550e8400e29b41d4a716446655440022",
                created_at="2026-09-01T02:00:00+09:00",
            )
            with self.assertRaises(intent_cli.IntentError) as caught:
                intent_cli.validate_batch(repo, result)
            self.assertEqual("intent_slot_conflict", caught.exception.code)

        module = ast.parse(helpers.CLI_PATH.read_text(encoding="utf-8"))
        writes = []
        for node in ast.walk(module):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr in {
                "write_text", "write_bytes", "mkdir", "unlink", "rename",
            }:
                writes.append(node.func.attr)
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "open":
                writes.append("open")
        self.assertEqual([], writes)

    def test_plugin_surface_is_complete(self) -> None:
        required = (
            helpers.PLUGIN / ".codex-plugin/plugin.json",
            helpers.PLUGIN / ".claude-plugin/plugin.json",
            helpers.PLUGIN / "README.md",
            helpers.PLUGIN / "README.ko.md",
            helpers.PLUGIN / "rules/intent-policy.md",
            helpers.PLUGIN / "skills/intent/SKILL.md",
            helpers.PLUGIN / "skills/intent/references/intent-protocol.md",
            helpers.PLUGIN / "skills/init/SKILL.md",
            helpers.PLUGIN / "skills/init/scripts/intent_init.py",
            helpers.PLUGIN / "templates/intent.md",
        )
        self.assertTrue(all(path.is_file() for path in required))
        for path in required[:2]:
            self.assertEqual("bobbin", json.loads(path.read_text(encoding="utf-8"))["name"])


if __name__ == "__main__":
    unittest.main()
