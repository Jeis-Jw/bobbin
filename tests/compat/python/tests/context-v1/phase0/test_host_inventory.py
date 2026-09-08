import copy
import hashlib
import json
import unittest
from pathlib import Path

from phase0_contract import (
    InventoryContractError,
    classify_preflight,
    discover_owner,
    render_preflight,
)


FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "host-inventory"


def load_fixture(name):
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def canonical_digest(value):
    payload = json.dumps(
        value, ensure_ascii=False, separators=(",", ":"), sort_keys=True
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


class HostEvidenceTests(unittest.TestCase):
    def test_codex_live_and_claude_static_evidence_are_not_conflated(self):
        codex = load_fixture("codex-live-evidence.json")
        claude = load_fixture("claude-code-static-evidence.json")

        self.assertEqual(codex["evidence_kind"], "live_current_session")
        self.assertEqual(codex["host"], "codex")
        self.assertEqual(codex["collection_surface"], "active_session_inventory")
        self.assertFalse(codex["alternate_runtime_used"])
        self.assertFalse(codex["cache_probe_used"])

        self.assertEqual(claude["evidence_kind"], "fixture_static")
        self.assertEqual(claude["host"], "claude-code")
        self.assertFalse(claude["live_inventory_available"])
        self.assertEqual(claude["release_evidence_gate"], "open")
        self.assertIn("live Claude Code", claude["evidence_gap"])
        self.assertFalse(claude["alternate_runtime_used"])
        self.assertFalse(claude["cache_probe_used"])

    def test_owner_discovery_accepts_inventory_or_caller_descriptor_only(self):
        cases = load_fixture("owner-discovery-cases.json")
        collection = cases["result_collection_contract"]
        self.assertEqual(collection["semantic_result_source"], "host_owner_skill_call")
        self.assertEqual(collection["required_result_schema"], "context-owner-result/v1")
        self.assertEqual(collection["router_owner_process_invocations"], 0)
        self.assertEqual(collection["cache_probe_count"], 0)
        self.assertEqual(collection["alternate_runtime_count"], 0)
        for case in cases["accepted"]:
            with self.subTest(case=case["id"]):
                self.assertEqual(
                    discover_owner(case["input"]), case["expected_descriptor"]
                )

        for case in cases["rejected"]:
            with self.subTest(case=case["id"]):
                with self.assertRaisesRegex(
                    InventoryContractError, case["expected_error"]
                ):
                    discover_owner(case["input"])


class ManualPreflightTests(unittest.TestCase):
    def test_exact_preflight_state_and_renderer_contract(self):
        corpus = load_fixture("preflight-cases.json")
        required = load_fixture("required-plugin.json")

        for case in corpus["cases"]:
            with self.subTest(case=case["id"]):
                result = classify_preflight(case["inventory"], case["doctor"], required)
                self.assertEqual(result["code"], case["expected_code"])
                self.assertEqual(result["observed"], case["expected_observed"])
                rendered = render_preflight(result, case["host"], required)
                self.assertEqual(
                    {key: rendered[key] for key in case["expected_rendered"]},
                    case["expected_rendered"],
                )
                self.assertEqual(rendered["required_plugin"], required)
                self.assertEqual(
                    rendered["write_policy"],
                    {"repository": "none", "host_configuration": "none"},
                )

    def test_preflight_and_error_rendering_are_byte_noop(self):
        corpus = load_fixture("preflight-cases.json")
        required = load_fixture("required-plugin.json")

        for case in corpus["cases"]:
            with self.subTest(case=case["id"]):
                inventory = copy.deepcopy(case["inventory"])
                doctor = copy.deepcopy(case["doctor"])
                before = canonical_digest({"inventory": inventory, "doctor": doctor})

                result = classify_preflight(inventory, doctor, required)
                render_preflight(result, case["host"], required)

                after = canonical_digest({"inventory": inventory, "doctor": doctor})
                self.assertEqual(after, before)

    def test_preflight_order_keeps_repository_state_out_of_plugin_identity(self):
        corpus = load_fixture("preflight-cases.json")
        by_id = {case["id"]: case for case in corpus["cases"]}
        required = load_fixture("required-plugin.json")

        missing = by_id["codex-core-missing"]
        self.assertEqual(
            classify_preflight(missing["inventory"], missing["doctor"], required)[
                "code"
            ],
            "core_missing",
        )

        absent = by_id["codex-core-uninitialized"]
        self.assertEqual(
            classify_preflight(absent["inventory"], absent["doctor"], required)[
                "code"
            ],
            "core_uninitialized",
        )


if __name__ == "__main__":
    unittest.main()
