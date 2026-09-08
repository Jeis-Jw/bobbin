from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

import assumption_test_support as helpers


class AssumptionPluginContractTests(unittest.TestCase):
    def test_public_init_feature_handshake_bootstraps_through_core(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            repo = Path(temp) / "repo"
            repo.mkdir()
            before = helpers.tree_digest(repo)
            completed = subprocess.run(
                [sys.executable, str(helpers.INIT_PATH), "--host", "codex", "--core-cli", str(helpers.CORE_CLI_PATH), "--json"],
                cwd=repo,
                env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
                text=True,
                capture_output=True,
            )
            self.assertEqual(0, completed.returncode, completed.stdout + completed.stderr)
            payload = json.loads(completed.stdout)["result"]
            self.assertEqual("context-owner-descriptor/v2", payload["required_feature"])
            self.assertNotEqual(before, helpers.tree_digest(repo))
            self.assertTrue((repo / "context/assumption/assumption.index.md").is_file())
            doctor_result = helpers.core_cli.doctor_repository(repo)
            self.assertEqual("ready", doctor_result["repository_state"])

    def test_public_claim_decline_search_read_are_byte_noop(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            repo = root / "repo"
            repo.mkdir()
            helpers.init_repo(repo)
            helpers.capture(repo)
            inventory, doctor = helpers.write_preflight(root)
            preflight = helpers.preflight_args(inventory, doctor)
            candidate_path = root / "candidate.json"
            attestation_path = root / "attestation.json"
            value = helpers.candidate()
            candidate_path.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")
            attestation_path.write_text(json.dumps(helpers.attestation(value), ensure_ascii=False), encoding="utf-8")
            before = helpers.tree_digest(repo)
            claim = helpers.run_cli(repo, "claim", "--candidate", f"@{candidate_path}", "--attestation", f"@{attestation_path}", "--route-only", *preflight, "--json")
            self.assertEqual(0, claim.returncode, claim.stdout + claim.stderr)
            self.assertEqual("claim", json.loads(claim.stdout)["result"]["decision"])
            search = helpers.run_cli(repo, "search", "--signal", "assumption-relevant", "--query", "IdP", *preflight, "--json")
            self.assertEqual(0, search.returncode, search.stdout + search.stderr)
            self.assertEqual(1, json.loads(search.stdout)["result"]["returned"])
            read = helpers.run_cli(repo, "read", "--signal", "assumption-relevant", "--id", "ctx_550e8400e29b41d4a716446655440000", *preflight, "--json")
            self.assertEqual(0, read.returncode, read.stdout + read.stderr)
            self.assertIn("Assumption", json.loads(read.stdout)["result"]["sections"])
            blocked = helpers.run_cli(repo, "search", "--signal", "always", "--query", "IdP", *preflight, "--json")
            self.assertEqual(5, blocked.returncode)
            self.assertEqual("signal_required", json.loads(blocked.stdout)["error"]["code"])
            self.assertEqual(before, helpers.tree_digest(repo))

            for kind in ("observation", "decision"):
                other = root / f"{kind}.json"
                other.write_text(json.dumps(helpers.semantic_candidate(kind), ensure_ascii=False), encoding="utf-8")
                declined = helpers.run_cli(repo, "decline", "--candidate", f"@{other}", "--reason", f"{kind} boundary", *preflight, "--json")
                self.assertEqual(0, declined.returncode, declined.stdout + declined.stderr)
                self.assertEqual("decline", json.loads(declined.stdout)["result"]["decision"])
                self.assertEqual(before, helpers.tree_digest(repo))

    def test_nonstatic_cli_requires_core_preflight(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            repo = Path(temp)
            before = helpers.tree_digest(repo)
            result = helpers.run_cli(repo, "init", "--json")
            self.assertEqual(5, result.returncode)
            self.assertEqual("core_preflight_required", json.loads(result.stdout)["error"]["code"])
            self.assertEqual(before, helpers.tree_digest(repo))
            for command in ("schema", "capabilities"):
                static = helpers.run_cli(repo, command, "--json")
                self.assertEqual(0, static.returncode, static.stdout + static.stderr)


if __name__ == "__main__":
    unittest.main()
