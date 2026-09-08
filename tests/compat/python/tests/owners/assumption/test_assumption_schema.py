from __future__ import annotations

import ast
import copy
import json
import tempfile
import unittest
from pathlib import Path

import assumption_test_support as helpers


assumption_cli = helpers.assumption_cli
core_cli = helpers.core_cli


class AssumptionSchemaTests(unittest.TestCase):
    def test_descriptor_v2_and_artifact_body_are_core_valid(self) -> None:
        descriptor = assumption_cli.owner_descriptor()
        self.assertEqual(("context-assumption", "assumption", "context-assumption/v1", "provisional"), core_cli.validate_owner_descriptor(descriptor))
        self.assertLessEqual(len(assumption_cli.canonical_json(descriptor).encode("utf-8")), 8 * 1024)
        value = helpers.candidate()
        result = assumption_cli.build_claim_result(value, helpers.attestation(value), identifier="ctx_550e8400e29b41d4a716446655440000", created_at="2026-08-22T01:00:00+09:00")
        core_cli.validate_owner_result(result, assumption_cli.assumption_capability(), descriptor)
        frontmatter, sections = assumption_cli.parse_document(result["artifact_drafts"][0]["content"])
        self.assertEqual("context-assumption/v1", frontmatter["schema"])
        self.assertEqual("provisional", assumption_cli.assumption_capability()["authority"])
        self.assertEqual(["Assumption", "Basis", "Confirmation conditions", "Refutation conditions"], list(sections))

    def test_claim_requires_exact_candidate_digest_and_rfc6901_pointers(self) -> None:
        value = helpers.candidate()
        before = copy.deepcopy(value)
        valid = helpers.attestation(value)
        result = assumption_cli.build_claim_result(value, valid, route_only=True)
        self.assertEqual([], result["artifact_drafts"])
        self.assertEqual(before, result["semantic_inputs"][0]["value"])

        for mutation in ("digest", "pointer", "unverified"):
            with self.subTest(mutation=mutation):
                candidate = copy.deepcopy(value)
                proof = copy.deepcopy(valid)
                if mutation == "digest":
                    proof["input_digest"] = "sha256:" + "0" * 64
                elif mutation == "pointer":
                    proof["assertions"][0]["evidence_pointers"] = ["/claim"]
                else:
                    candidate["owner_inputs"]["assumption"]["unverified_ok"] = False
                    proof = helpers.attestation(candidate)
                with self.assertRaises(assumption_cli.AssumptionError):
                    assumption_cli.build_claim_result(candidate, proof, route_only=True)

    def test_observation_and_decision_candidates_decline_without_drafts(self) -> None:
        for kind in ("observation", "decision"):
            with self.subTest(kind=kind):
                candidate = helpers.semantic_candidate(kind)
                result = assumption_cli.build_decline_result(candidate, f"{kind} semantic boundary")
                core_cli.validate_owner_result(result, assumption_cli.assumption_capability())
                self.assertEqual("decline", result["decision"])
                self.assertEqual(candidate, result["semantic_inputs"][0]["value"])
                self.assertEqual([], result["artifact_drafts"])
                self.assertIsNone(result["proposed_plan"])

    def test_semantic_owner_cli_has_no_filesystem_write_primitive(self) -> None:
        module = ast.parse(helpers.CLI_PATH.read_text(encoding="utf-8"))
        forbidden_calls = []
        for node in ast.walk(module):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr in {"write_text", "write_bytes", "mkdir", "unlink", "rename"}:
                forbidden_calls.append(node.func.attr)
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "open":
                forbidden_calls.append("open")
        self.assertEqual([], forbidden_calls)

    def test_plugin_surface_is_complete_and_manifests_parse(self) -> None:
        required = [
            helpers.PLUGIN / ".codex-plugin/plugin.json",
            helpers.PLUGIN / ".claude-plugin/plugin.json",
            helpers.PLUGIN / "README.md",
            helpers.PLUGIN / "rules/assumption-policy.md",
            helpers.PLUGIN / "skills/assumption/SKILL.md",
            helpers.PLUGIN / "skills/assumption/references/assumption-protocol.md",
            helpers.PLUGIN / "skills/init/SKILL.md",
            helpers.PLUGIN / "skills/init/scripts/assumption_init.py",
            helpers.PLUGIN / "templates/assumption.md",
        ]
        self.assertTrue(all(path.is_file() for path in required))
        for path in required[:2]:
            manifest = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual("bobbin", manifest["name"])
        template = (helpers.PLUGIN / "templates/assumption.md").read_text(encoding="utf-8")
        for token in ("context-assumption/v1", "## Assumption", "## Basis", "## Confirmation conditions", "## Refutation conditions"):
            self.assertIn(token, template)


if __name__ == "__main__":
    unittest.main()
