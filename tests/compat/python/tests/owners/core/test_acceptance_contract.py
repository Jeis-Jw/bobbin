#!/usr/bin/env python3
from __future__ import annotations

import ast
import json
import unittest
from pathlib import Path


REPO = Path(__file__).resolve().parents[3]
PLUGIN = REPO / "plugins/bobbin"
REGISTRY = REPO / "tests/context-v1/acceptance-matrix.json"
OWNED = {1, 3, 4, 5, 6, 7, 8, 9, 10, 35, 39, 47, 49, 50, 51, 53, 54, 55, 56}


class AcceptanceKernelContractTests(unittest.TestCase):
    def test_owned_acceptance_selectors_are_real_named_tests(self) -> None:
        entries = json.loads(REGISTRY.read_text(encoding="utf-8"))["entries"]
        owned = [entry for entry in entries if entry["owner"] == "p1-acceptance-storage-kernel"]
        self.assertEqual(OWNED, {entry["id"] for entry in owned})
        for entry in owned:
            path_text, class_name, method_name = entry["selector"].split("::")
            module = ast.parse((REPO / path_text).read_text(encoding="utf-8"))
            classes = {node.name: node for node in module.body if isinstance(node, ast.ClassDef)}
            self.assertIn(class_name, classes)
            methods = {node.name for node in classes[class_name].body if isinstance(node, ast.FunctionDef)}
            self.assertIn(method_name, methods)

    def test_kernel_is_stdlib_only(self) -> None:
        source = (PLUGIN / "skills/context/scripts/context_cli.py").read_text(encoding="utf-8")
        module = ast.parse(source)
        allowed = {
            "__future__", "argparse", "contextlib", "dataclasses", "datetime", "fcntl", "hashlib", "json", "os", "pathlib",
            "re", "shutil", "stat", "subprocess", "sys", "tempfile", "typing", "unicodedata", "uuid", "importlib"
        }
        imported = set()
        for node in ast.walk(module):
            if isinstance(node, ast.Import):
                imported.update(alias.name.split(".")[0] for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported.add(node.module.split(".")[0])
        self.assertFalse(imported - allowed, imported - allowed)


if __name__ == "__main__":
    unittest.main()
