from __future__ import annotations

import ast
import json
import tempfile
import unittest
from pathlib import Path

import document_test_support as helpers


document_cli = helpers.document_cli
core_cli = helpers.core_cli


class DocumentContractTests(unittest.TestCase):
    def test_descriptor_and_content_claim_are_core_valid(self) -> None:
        descriptor = document_cli.owner_descriptor()
        self.assertEqual(
            ("context-document", "document", "context-document/v1", "authoritative"),
            core_cli.validate_owner_descriptor(descriptor),
        )
        value = helpers.candidate()
        result = document_cli.build_claim_result(
            value,
            helpers.attestation(value),
            identifier="ctx_550e8400e29b41d4a716446655440030",
            created_at="2026-09-01T01:00:00+09:00",
        )
        core_cli.validate_owner_result(result, document_cli.document_capability(), descriptor)
        frontmatter, sections = document_cli.parse_document(result["artifact_drafts"][0]["content"])
        self.assertEqual("release-operations", frontmatter["document_key"])
        self.assertEqual(["Content"], list(sections))

    def test_capture_read_search_and_stable_id_update_work_in_filesystem_vault(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            repo = Path(temp) / "vault"
            helpers.init_repo(repo)
            helpers.capture(repo)
            before = document_cli.read_document(repo, identifier="ctx_550e8400e29b41d4a716446655440030")
            self.assertEqual(1, document_cli.search_documents(repo, query="operations")["returned"])
            replacement = "릴리스 전에 변경 범위, 운영 영향, 복구 절차와 책임자를 함께 검토한다."
            result = document_cli.build_update_result(
                repo,
                before["id"],
                replacement,
                updated_at="2026-09-01T02:00:00+09:00",
            )
            self.assertEqual("replace_same_state", document_cli.validate_batch(repo, result)["transition_topology"])
            draft = result["artifact_drafts"][0]
            updated_frontmatter, _ = document_cli.parse_document(draft["content"])
            self.assertEqual(before["id"], updated_frontmatter["id"])
            self.assertEqual(before["path"], draft["path"])
            helpers.apply_result(repo, result)
            after = document_cli.read_document(repo, identifier=before["id"])
            self.assertEqual(before["path"], after["path"])
            self.assertEqual(replacement, after["sections"]["Content"])

    def test_exact_slot_conflict_no_change_and_write_boundary_fail_closed(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            repo = Path(temp) / "vault"
            helpers.init_repo(repo)
            helpers.capture(repo)
            with self.assertRaises(document_cli.DocumentError) as caught:
                document_cli.build_update_result(
                    repo,
                    "ctx_550e8400e29b41d4a716446655440030",
                    helpers.candidate()["claim"],
                )
            self.assertEqual("no_change", caught.exception.code)

            duplicate = helpers.candidate(
                candidate_id="cand_550e8400e29b41d4a716446655440031",
                title="Duplicate document",
            )
            result = document_cli.build_claim_result(
                duplicate,
                helpers.attestation(duplicate),
                identifier="ctx_550e8400e29b41d4a716446655440031",
                created_at="2026-09-01T02:00:00+09:00",
            )
            with self.assertRaises(document_cli.DocumentError) as caught:
                document_cli.validate_batch(repo, result)
            self.assertEqual("document_slot_conflict", caught.exception.code)

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
            helpers.PLUGIN / "rules/document-policy.md",
            helpers.PLUGIN / "skills/document/SKILL.md",
            helpers.PLUGIN / "skills/document/references/document-protocol.md",
            helpers.PLUGIN / "skills/init/SKILL.md",
            helpers.PLUGIN / "skills/init/scripts/document_init.py",
            helpers.PLUGIN / "templates/document.md",
        )
        self.assertTrue(all(path.is_file() for path in required))
        for path in required[:2]:
            self.assertEqual("bobbin", json.loads(path.read_text(encoding="utf-8"))["name"])


if __name__ == "__main__":
    unittest.main()
