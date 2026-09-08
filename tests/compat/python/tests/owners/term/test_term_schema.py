from __future__ import annotations

import ast
import copy
import json
import unicodedata
import unittest

import term_test_support as helpers


term_cli = helpers.term_cli
core_cli = helpers.core_cli


class TermSchemaTests(unittest.TestCase):
    def test_descriptor_v2_and_artifact_body_are_core_valid(self) -> None:
        descriptor = term_cli.owner_descriptor()
        self.assertEqual(
            ("context-term", "term", "context-term/v1", "authoritative"),
            core_cli.validate_owner_descriptor(descriptor),
        )
        self.assertLessEqual(len(term_cli.canonical_json(descriptor).encode("utf-8")), 8 * 1024)
        value = helpers.candidate()
        result = term_cli.build_claim_result(
            value,
            helpers.attestation(value),
            identifier="ctx_550e8400e29b41d4a716446655440000",
            created_at="2026-08-22T01:00:00+09:00",
        )
        core_cli.validate_owner_result(result, term_cli.term_capability(), descriptor)
        frontmatter, sections = term_cli.parse_document(result["artifact_drafts"][0]["content"])
        self.assertEqual("context-term/v1", frontmatter["schema"])
        self.assertEqual("bff", frontmatter["term_key"])
        self.assertEqual("authoritative", term_cli.term_capability()["authority"])
        self.assertEqual(["Definition"], list(sections))
        self.assertEqual(value["claim"], sections["Definition"])

    def test_term_key_is_unicode_case_space_and_punctuation_deterministic(self) -> None:
        nfc = "Café API"
        nfd = unicodedata.normalize("NFD", nfc)
        keys = {
            term_cli.canonical_term_key(nfc),
            term_cli.canonical_term_key(nfd),
            term_cli.canonical_term_key("  CAFÉ---api  "),
            term_cli.canonical_term_key("Café_api"),
        }
        self.assertEqual({"café-api"}, keys)
        self.assertEqual("project/auth-flow", term_cli.canonical_scope(" PROJECT / Auth_flow "))

    def test_alias_and_deprecated_term_keys_are_disjoint(self) -> None:
        value = helpers.candidate()
        for mutation in ("alias-primary", "deprecated-alias", "canonical-duplicate"):
            with self.subTest(mutation=mutation):
                attacked = copy.deepcopy(value)
                owner_input = attacked["owner_inputs"]["term"]
                if mutation == "alias-primary":
                    owner_input["aliases"] = ["BFF!"]
                elif mutation == "deprecated-alias":
                    owner_input["deprecated_terms"] = ["backend—for—frontend"]
                else:
                    owner_input["aliases"] = ["Café API", unicodedata.normalize("NFD", "CAFÉ_api")]
                with self.assertRaises(term_cli.TermError) as caught:
                    term_cli.build_claim_result(attacked, helpers.attestation(attacked), route_only=True)
                self.assertEqual("term_overlap", caught.exception.code)

    def test_claim_requires_exact_candidate_digest_and_rfc6901_pointers(self) -> None:
        value = helpers.candidate()
        before = copy.deepcopy(value)
        valid = helpers.attestation(value)
        result = term_cli.build_claim_result(value, valid, route_only=True)
        self.assertEqual("claim", result["decision"])
        self.assertEqual([], result["artifact_drafts"])
        self.assertEqual(before, result["semantic_inputs"][0]["value"])

        for mutation in ("digest", "pointer", "definition"):
            with self.subTest(mutation=mutation):
                candidate = copy.deepcopy(value)
                proof = copy.deepcopy(valid)
                if mutation == "digest":
                    proof["input_digest"] = "sha256:" + "0" * 64
                elif mutation == "pointer":
                    proof["assertions"][0]["evidence_pointers"] = ["/claim"]
                else:
                    candidate["owner_inputs"]["term"]["definition"] += " changed"
                    proof = helpers.attestation(candidate)
                with self.assertRaises(term_cli.TermError):
                    term_cli.build_claim_result(candidate, proof, route_only=True)

        generic = helpers.candidate(project_signal="generic-dictionary")
        declined = term_cli.build_claim_result(generic, helpers.attestation(generic), route_only=True)
        self.assertEqual("decline", declined["decision"])

    def test_observation_decision_assumption_and_mixed_candidates_decline(self) -> None:
        for kind in ("observation", "decision", "assumption"):
            with self.subTest(kind=kind):
                candidate = helpers.semantic_candidate(kind)
                result = term_cli.build_claim_result(candidate, helpers.attestation(helpers.candidate()), route_only=True)
                self.assertEqual("decline", result["decision"])
                self.assertEqual(candidate, result["semantic_inputs"][0]["value"])
                self.assertEqual([], result["artifact_drafts"])

        mixed = helpers.candidate()
        mixed["requested_kind"] = None
        mixed["specialized_kinds"] = ["term", "decision"]
        mixed["owner_inputs"]["decision"] = {"decision": "현재 이 선택을 따른다."}
        result = term_cli.build_claim_result(mixed, helpers.attestation(mixed), route_only=True)
        self.assertEqual("decline", result["decision"])

    def test_semantic_owner_cli_has_no_filesystem_write_primitive(self) -> None:
        module = ast.parse(helpers.CLI_PATH.read_text(encoding="utf-8"))
        forbidden = []
        for node in ast.walk(module):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr in {
                "write_text", "write_bytes", "mkdir", "unlink", "rename",
            }:
                forbidden.append(node.func.attr)
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "open":
                forbidden.append("open")
        self.assertEqual([], forbidden)

    def test_plugin_surface_is_complete_and_manifests_parse(self) -> None:
        required = [
            helpers.PLUGIN / ".codex-plugin/plugin.json",
            helpers.PLUGIN / ".claude-plugin/plugin.json",
            helpers.PLUGIN / "README.md",
            helpers.PLUGIN / "rules/term-policy.md",
            helpers.PLUGIN / "skills/term/SKILL.md",
            helpers.PLUGIN / "skills/term/references/term-protocol.md",
            helpers.PLUGIN / "skills/init/SKILL.md",
            helpers.PLUGIN / "skills/init/scripts/term_init.py",
            helpers.PLUGIN / "templates/term.md",
        ]
        self.assertTrue(all(path.is_file() for path in required))
        for path in required[:2]:
            self.assertEqual("bobbin", json.loads(path.read_text(encoding="utf-8"))["name"])
        template = (helpers.PLUGIN / "templates/term.md").read_text(encoding="utf-8")
        for token in ("context-term/v1", "term_key", "aliases", "deprecated_terms", "related", "## Definition"):
            self.assertIn(token, template)


if __name__ == "__main__":
    unittest.main()
