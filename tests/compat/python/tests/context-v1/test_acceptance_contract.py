#!/usr/bin/env python3
from __future__ import annotations

import ast
import hashlib
import importlib.util
import json
import re
import sys
import unittest
from pathlib import Path


HERE = Path(__file__).resolve().parent
REGISTRY = HERE / "acceptance-matrix.json"
FORBIDDEN = {"skip", "skipped", "xfail", "pending", "todo"}
PUBLIC_SURFACE_FIELDS = {
    "kind", "selector", "observable_result", "write_policy", "availability",
}


def public_coverage_status(entries: list[dict]) -> str:
    public = [entry for entry in entries if entry.get("coverage") == "public-surface"]
    if not public or any(
        (entry.get("public_surface") or {}).get("availability") != "available"
        for entry in public
    ):
        return "unknown"
    return "pass"


def public_execution_status(result: unittest.TestResult, expected_count: int) -> str:
    """A public claim is unknown when its runnable selector did not fully execute."""
    if result.testsRun != expected_count:
        return "unknown"
    if result.skipped or result.expectedFailures or result.unexpectedSuccesses:
        return "unknown"
    return "pass" if result.wasSuccessful() else "fail"


def load_selected_test(selector: str) -> unittest.TestCase:
    path_text, class_name, method_name = selector.split("::")
    path = HERE.parents[1] / path_text
    module_name = "acceptance_public_" + hashlib.sha256(selector.encode("utf-8")).hexdigest()
    spec = importlib.util.spec_from_file_location(module_name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"public selector unavailable: {selector}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    sys.path.insert(0, str(path.parent))
    try:
        spec.loader.exec_module(module)
    finally:
        sys.path.pop(0)
    selected = getattr(module, class_name)(method_name)
    if selected.countTestCases() != 1:
        raise RuntimeError(f"public selector did not resolve exactly once: {selector}")
    return selected


def execute_public_selectors(selectors: list[str]) -> str:
    try:
        selected = [load_selected_test(selector) for selector in selectors]
    except (AttributeError, ImportError, OSError, RuntimeError, ValueError):
        return "unknown"
    result = unittest.TestResult()
    unittest.TestSuite(selected).run(result)
    return public_execution_status(result, len(selectors))


class AcceptanceRegistryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.registry = json.loads(REGISTRY.read_text(encoding="utf-8"))
        corpus_path = HERE / cls.registry["fixture_corpus"]
        cls.corpus = json.loads(corpus_path.read_text(encoding="utf-8"))

    def test_registry_contains_each_acceptance_id_exactly_once(self) -> None:
        entries = self.registry["entries"]
        ids = [entry["id"] for entry in entries]
        self.assertEqual(list(range(1, 69)), sorted(ids))
        self.assertEqual(len(ids), len(set(ids)))
        self.assertEqual("context-v1-acceptance-registry/v1", self.registry["schema"])

    def test_registry_and_corpus_are_one_to_one_and_complete(self) -> None:
        entries = self.registry["entries"]
        case_names = [entry["case"] for entry in entries]
        self.assertEqual(len(case_names), len(set(case_names)))
        self.assertEqual(set(case_names), set(self.corpus["cases"]))
        for name, contract in self.corpus["cases"].items():
            self.assertIsInstance(contract.get("input"), str, name)
            self.assertTrue(contract.get("expected"), name)

    def test_every_entry_has_named_downstream_selector_and_no_deferred_state(self) -> None:
        modules = {}
        for entry in self.registry["entries"]:
            self.assertEqual("executable", entry["status"])
            self.assertFalse(FORBIDDEN.intersection(str(v).casefold() for v in entry.values()))
            self.assertRegex(
                entry["selector"],
                re.compile(r"^[^:]+\.py::[A-Za-z_][A-Za-z0-9_]*::test_acceptance_\d{2}_[A-Za-z0-9_]+$"),
            )
            self.assertTrue(entry["owner"].startswith("p"))
            path_text, class_name, method_name = entry["selector"].split("::")
            if path_text not in modules:
                module = ast.parse((HERE.parents[1] / path_text).read_text(encoding="utf-8"))
                modules[path_text] = {
                    node.name: node for node in module.body if isinstance(node, ast.ClassDef)
                }
            self.assertIn(class_name, modules[path_text], entry["selector"])
            selected_class = modules[path_text][class_name]
            self.assertIn("unittest.TestCase", {ast.unparse(base) for base in selected_class.bases})
            methods = {
                node.name for node in selected_class.body
                if isinstance(node, ast.FunctionDef)
            }
            self.assertIn(method_name, methods, entry["selector"])

    def test_public_surface_registry_executes_real_shipped_surfaces(self) -> None:
        public = []
        for entry in self.registry["entries"]:
            self.assertIn(entry["coverage"], {"public-surface", "internal-invariant"})
            if entry["coverage"] == "internal-invariant":
                self.assertNotIn("public_surface", entry)
                continue
            surface = entry.get("public_surface")
            self.assertIsInstance(surface, dict)
            self.assertEqual(PUBLIC_SURFACE_FIELDS, set(surface))
            self.assertIn(surface["kind"], {"cli", "skill", "adapter", "artifact-layout", "agent-skill-composition"})
            self.assertIn(
                surface["write_policy"],
                {"read-only", "approval-gated", "none-before-ready", "explicit-init-fixed-seed", "core-sole-writer", "explicit-host-install"},
            )
            self.assertTrue(surface["observable_result"])
            self.assertIn(surface["availability"], {"available", "unavailable"})
            public.append(surface)

        self.assertEqual("pass", public_coverage_status(self.registry["entries"]))
        self.assertEqual(
            "pass", execute_public_selectors([item["selector"] for item in public]),
        )

    def test_public_surface_unavailable_is_unknown_and_internal_is_excluded(self) -> None:
        entries = [
            {"coverage": "internal-invariant"},
            {"coverage": "public-surface", "public_surface": {"availability": "unavailable"}},
        ]
        self.assertEqual("unknown", public_coverage_status(entries))
        entries[1]["public_surface"]["availability"] = "available"
        self.assertEqual("pass", public_coverage_status(entries))

    def test_public_execution_skip_and_non_execution_are_unknown(self) -> None:
        class Skipped(unittest.TestCase):
            @unittest.skip("surface unavailable")
            def test_surface(self) -> None:
                pass

        class ExpectedFailure(unittest.TestCase):
            @unittest.expectedFailure
            def test_surface(self) -> None:
                self.fail("not shipped")

        class UnexpectedSuccess(unittest.TestCase):
            @unittest.expectedFailure
            def test_surface(self) -> None:
                pass

        for case in (Skipped("test_surface"), ExpectedFailure("test_surface"),
                     UnexpectedSuccess("test_surface")):
            with self.subTest(case=type(case).__name__):
                result = unittest.TestResult()
                case.run(result)
                self.assertEqual("unknown", public_execution_status(result, 1))

        executed = unittest.TestResult()
        unittest.FunctionTestCase(lambda: None).run(executed)
        self.assertEqual("unknown", public_execution_status(executed, 2))
        self.assertEqual(
            "unknown", execute_public_selectors(["missing.py::Missing::test_missing"]),
        )


if __name__ == "__main__":
    unittest.main()
