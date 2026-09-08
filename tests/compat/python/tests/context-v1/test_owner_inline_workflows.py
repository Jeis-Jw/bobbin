from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = next(p for p in Path(__file__).resolve().parents if (p / "pytest.ini").is_file())
CORE_CLI = ROOT / "plugins/bobbin/skills/context/scripts/context_cli.py"
CASES = {
    "intent": {
        "preview": [
            "--title", "Release direction", "--summary", "Keep the release direction explicit.",
            "--scope", "product/release", "--intent-key", "release-direction",
            "--sec-intent", "Ship through one explicit preview and apply path.",
            "--sec-success-criterion", "Preview requires one command.",
            "--attest-intent-present", "--attest-desired-direction",
        ],
    },
    "term": {
        "preview": [
            "--title", "Release train term", "--summary", "Define the project-local release train term.",
            "--scope", "product/release", "--term", "release train",
            "--sec-definition", "The synchronized catalog version set for Context Plugins.",
            "--attest-term-identified", "--attest-definition-present",
        ],
    },
    "assumption": {
        "preview": [
            "--title", "Host reload premise", "--summary", "Track an unverified host reload premise.",
            "--scope", "product/release", "--sec-assumption", "The host reloads the newest catalog pin.",
            "--sec-basis", "The host documents reload after plugin changes.",
            "--attest-assumption-present", "--attest-unverified-ok",
        ],
    },
    "document": {
        "preview": [
            "--title", "Release operations", "--summary", "Keep the release operating guide current.",
            "--scope", "product/release", "--document-key", "release-operations",
            "--sec-content", "Preview, approve, and apply through the owner workflow.",
            "--attest-content-present", "--attest-living-document",
        ],
    },
}


def load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


core_cli = load("context_owner_workflow_test_core", CORE_CLI)


def tree_digest(root: Path) -> str:
    digest = hashlib.sha256()
    for path in sorted(item for item in root.rglob("*") if item.is_file()):
        digest.update(path.relative_to(root).as_posix().encode("utf-8"))
        digest.update(path.read_bytes())
    return digest.hexdigest()


class OwnerInlineWorkflowTests(unittest.TestCase):
    def test_all_non_decision_owners_preview_then_apply_with_two_commands(self) -> None:
        for kind, case in CASES.items():
            with self.subTest(kind=kind), tempfile.TemporaryDirectory() as temp:
                vault = Path(temp) / "vault"
                vault.mkdir()
                owner_cli_path = ROOT / f"plugins/bobbin/skills/{kind}/scripts/{kind}_cli.py"
                workflow_path = ROOT / f"plugins/bobbin/skills/{kind}/scripts/{kind}_workflow.py"
                owner_cli = load(f"context_owner_workflow_test_{kind}", owner_cli_path)
                seed = getattr(owner_cli, f"{kind}_index_seed")()
                core_cli.bootstrap_repository(vault, owner_cli.owner_descriptor(), seed, host="codex")
                before = tree_digest(vault)

                preview = subprocess.run(
                    [
                        sys.executable,
                        str(workflow_path),
                        "preview",
                        "--host", "codex",
                        "--core-cli", str(CORE_CLI.resolve()),
                        "--vault", str(vault),
                        "--inline",
                        *case["preview"],
                        "--json",
                    ],
                    cwd=temp,
                    env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
                    text=True,
                    capture_output=True,
                )
                self.assertEqual(0, preview.returncode, preview.stdout + preview.stderr)
                result = json.loads(preview.stdout)["result"]
                self.assertEqual("context-owner-inline-workflow/v1", result["schema"])
                self.assertEqual(f"context-{kind}", result["owner"])
                self.assertFalse(result["applied"])
                self.assertEqual("awaiting_approval", result["state"])
                self.assertEqual(before, tree_digest(vault), "preview must not change vault bytes")
                receipt = Path(result["receipt_file"])
                self.assertTrue(receipt.is_file())

                applied = subprocess.run(
                    [
                        sys.executable,
                        str(workflow_path),
                        "apply",
                        "--core-cli", str(CORE_CLI.resolve()),
                        "--vault", str(vault),
                        "--receipt-file", str(receipt),
                        "--approved-digest", result["approval_digest"],
                        "--json",
                    ],
                    cwd=temp,
                    env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
                    text=True,
                    capture_output=True,
                )
                self.assertEqual(0, applied.returncode, applied.stdout + applied.stderr)
                applied_result = json.loads(applied.stdout)["result"]
                self.assertTrue(applied_result["applied"])
                self.assertEqual("applied", applied_result["state"])
                self.assertFalse(receipt.exists())
                artifacts = [
                    path
                    for path in (vault / "context" / kind).glob("*.md")
                    if path.name != f"{kind}.index.md"
                ]
                self.assertEqual(1, len(artifacts))

    def test_public_workflow_help_needs_no_manual_preflight_files(self) -> None:
        for kind in CASES:
            with self.subTest(kind=kind):
                workflow_path = ROOT / f"plugins/bobbin/skills/{kind}/scripts/{kind}_workflow.py"
                completed = subprocess.run(
                    [sys.executable, str(workflow_path), "preview", "--help"],
                    cwd=ROOT,
                    text=True,
                    capture_output=True,
                )
                self.assertEqual(0, completed.returncode, completed.stdout + completed.stderr)
                self.assertIn("--inline", completed.stdout)
                self.assertIn("--core-cli", completed.stdout)
                self.assertNotIn("--core-inventory", completed.stdout)
                self.assertNotIn("--core-doctor", completed.stdout)


if __name__ == "__main__":
    unittest.main()
