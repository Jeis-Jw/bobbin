"""One-call ``record`` and deterministic embedded-core resolution.

record = preview + unchanged apply in one process, gated by ``--approved``.
Without ``--core-cli`` Bobbin's own embedded core is resolved
from the checkout layout and SHA-bound like an explicit path.
"""
from __future__ import annotations

import json
import pathlib
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[3]
CORE = ROOT / "plugins/bobbin/skills/context/scripts/context_cli.py"
DECISION_INIT = ROOT / "plugins/bobbin/skills/init/scripts/decision_init.py"
WORKFLOW = ROOT / "plugins/bobbin/skills/decision/scripts/decision_workflow.py"
CHECK = ROOT / "plugins/bobbin/skills/decision/scripts/decision_cli.py"


def run(argv, cwd):
    completed = subprocess.run([sys.executable, *argv, "--json"], cwd=cwd, capture_output=True, text=True)
    return completed.returncode, json.loads(completed.stdout)


def semantic(*extra):
    return [
        "--host", "codex", "--inline", "--title", "Use SQLite", "--summary", "Use embedded SQLite.",
        "--scope", "repository", "--decision-key", "persistence", "--commitment-evidence", "user: remember this",
        "--sec-decision", "Use embedded SQLite.", "--sec-rationale", "Must work offline.",
        "--sec-alternatives", "Mandatory PostgreSQL, rejected.", "--sec-revisit", "Revisit if offline support is removed.",
        "--attest-explicit-choice", "--attest-scope-identified", "--attest-commitment-present", *extra,
    ]


class RecordCommandTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.repo = pathlib.Path(self.temp.name)
        (self.repo / "PROJECT.md").write_text("demo\n", encoding="utf-8")
        self.assertEqual(0, run([str(CORE), "init", "--host", "codex"], self.repo)[0])
        self.assertEqual(0, run([str(DECISION_INIT), "--host", "codex", "--core-cli", str(CORE)], self.repo)[0])

    def tearDown(self) -> None:
        self.temp.cleanup()

    def test_record_requires_caller_attested_approval_and_writes_nothing_without_it(self) -> None:
        before = {p.relative_to(self.repo): p.read_bytes() for p in (self.repo / "context").rglob("*") if p.is_file()}
        rc, payload = run([str(WORKFLOW), "record", *semantic()], self.repo)
        self.assertEqual(2, rc)
        self.assertEqual("approval_attestation_required", payload["error"]["code"])
        self.assertEqual(before, {p.relative_to(self.repo): p.read_bytes() for p in (self.repo / "context").rglob("*") if p.is_file()})

    def test_record_refuses_missing_or_ambiguous_embedded_core_before_writing(self) -> None:
        packages = self.repo / "packages"
        copied_owner = packages / "bobbin"
        shutil.copytree(ROOT / "plugins/bobbin", copied_owner, ignore=shutil.ignore_patterns("__pycache__"))
        copied_workflow = copied_owner / "skills/decision/scripts/decision_workflow.py"
        # A valid unrelated installation must not replace a missing embedded writer.
        for version in ("1.0.0", "1.1.0"):
            cached_core = packages / "other-bobbin" / version
            shutil.copytree(ROOT / "plugins/bobbin", cached_core, ignore=shutil.ignore_patterns("__pycache__"))
        (copied_owner / "skills/context/scripts/context_cli.py").unlink()
        before = {p.relative_to(self.repo): p.read_bytes() for p in (self.repo / "context").rglob("*") if p.is_file()}
        rc, payload = run([str(copied_workflow), "record", *semantic("--approved")], self.repo)
        self.assertEqual(2, rc, payload)
        self.assertEqual("core_cli_required", payload["error"]["code"])
        self.assertEqual([], payload["error"]["details"]["candidates"])
        self.assertEqual(before, {p.relative_to(self.repo): p.read_bytes() for p in (self.repo / "context").rglob("*") if p.is_file()})

    def test_record_previews_and_applies_in_one_call_with_embedded_core_resolution(self) -> None:
        rc, payload = run([str(WORKFLOW), "record", *semantic("--approved")], self.repo)
        self.assertEqual(0, rc, payload)
        result = payload["result"]
        self.assertEqual("context-decision-workflow-record/v1", result["schema"])
        self.assertTrue(result["applied"])
        self.assertTrue(result["receipt_removed"])
        self.assertIn("context/decision/Use-SQLite.md", result["changed_paths"])
        self.assertTrue(result["preflight"]["observed"]["entrypoint"].endswith("skills/context/scripts/context_cli.py"))
        self.assertTrue((self.repo / "context/decision/Use-SQLite.md").is_file())
        rc, check = run([str(CHECK), "check", "--statement", "Move to PostgreSQL", "--scope", "repository", "--decision-key", "persistence"], self.repo)
        self.assertEqual(0, rc)
        current = check["result"]["comparison_input"]["current"]
        self.assertEqual(1, len(current))
        self.assertEqual("Use SQLite", current[0]["title"])
        self.assertEqual("Must work offline.", current[0]["sections"]["Rationale"])

    def test_record_on_an_occupied_slot_fails_closed_with_supersede_guidance(self) -> None:
        self.assertEqual(0, run([str(WORKFLOW), "record", *semantic("--approved")], self.repo)[0])
        rc, payload = run([str(WORKFLOW), "record", *semantic("--approved")], self.repo)
        self.assertNotEqual(0, rc)
        self.assertEqual("decision_slot_conflict", payload["error"]["code"])
        self.assertEqual("supersede", payload["error"]["details"]["suggested_action"])

    def test_record_supersede_preserves_history_and_links_the_successor(self) -> None:
        self.assertEqual(0, run([str(WORKFLOW), "record", *semantic("--approved")], self.repo)[0])
        rc, payload = run([str(CHECK), "search"], self.repo)
        self.assertEqual(0, rc, payload)
        predecessor = payload["result"]["items"][0]
        arguments = semantic("--approved", "--supersede", predecessor["id"])
        for flag, value in {
            "--title": "Use PostgreSQL", "--summary": "Use a managed PostgreSQL service.",
            "--sec-decision": "Use managed PostgreSQL for persistence.",
            "--sec-rationale": "Offline support has been removed.",
            "--sec-alternatives": "Embedded SQLite, rejected after removing offline support.",
        }.items():
            arguments[arguments.index(flag) + 1] = value
        rc, payload = run([str(WORKFLOW), "record", *arguments], self.repo)
        self.assertEqual(0, rc, payload)
        self.assertTrue(payload["result"]["applied"])
        self.assertTrue(payload["result"]["receipt_removed"])
        rc, payload = run([str(CHECK), "search"], self.repo)
        self.assertEqual(0, rc, payload)
        self.assertEqual(1, len(payload["result"]["items"]))
        successor = payload["result"]["items"][0]
        self.assertNotEqual(predecessor["id"], successor["id"])
        self.assertEqual("Use PostgreSQL", successor["title"])
        rc, payload = run([str(CHECK), "read", "--id", predecessor["id"]], self.repo)
        self.assertEqual(0, rc, payload)
        self.assertTrue(payload["result"]["do_not_follow"])
        self.assertEqual("superseded", payload["result"]["lifecycle_reason"])
        rc, payload = run([str(CHECK), "brief", "--id", predecessor["id"], "--include-history"], self.repo)
        self.assertEqual(0, rc, payload)
        self.assertEqual(successor["id"], payload["result"]["items"][0]["successor"])

    def test_record_withdraw_requires_approval_and_retires_the_current_decision(self) -> None:
        self.assertEqual(0, run([str(WORKFLOW), "record", *semantic("--approved")], self.repo)[0])
        rc, payload = run([str(CHECK), "search"], self.repo)
        self.assertEqual(0, rc, payload)
        identifier = payload["result"]["items"][0]["id"]
        before = {p.relative_to(self.repo): p.read_bytes() for p in (self.repo / "context").rglob("*") if p.is_file()}
        arguments = [str(WORKFLOW), "record", "--host", "codex", "--withdraw", identifier,
                     "--reason", "The project no longer needs a persistence decision."]
        rc, payload = run(arguments, self.repo)
        self.assertEqual(2, rc, payload)
        self.assertEqual("approval_attestation_required", payload["error"]["code"])
        self.assertEqual(before, {p.relative_to(self.repo): p.read_bytes() for p in (self.repo / "context").rglob("*") if p.is_file()})
        rc, payload = run([*arguments, "--approved"], self.repo)
        self.assertEqual(0, rc, payload)
        self.assertTrue(payload["result"]["applied"])
        self.assertTrue(payload["result"]["receipt_removed"])
        rc, payload = run([str(CHECK), "search"], self.repo)
        self.assertEqual(0, rc, payload)
        self.assertEqual([], payload["result"]["items"])
        rc, payload = run([str(CHECK), "read", "--id", identifier], self.repo)
        self.assertEqual(0, rc, payload)
        self.assertTrue(payload["result"]["do_not_follow"])
        self.assertEqual("withdrawn", payload["result"]["lifecycle_reason"])

    def test_explicit_core_cli_still_works_and_schema_lists_record(self) -> None:
        rc, payload = run([str(WORKFLOW), "record", "--core-cli", str(CORE), *semantic("--approved")], self.repo)
        self.assertEqual(0, rc, payload)
        rc, schema = run([str(CHECK), "schema"], self.repo)
        self.assertEqual(0, rc)
        self.assertIn("record", schema["result"]["workflow_surface"]["commands"])


class ScaleRecallTests(unittest.TestCase):
    """The conflicting decision must surface among many near-topic decisions."""

    def test_discovery_check_ranks_the_conflicting_decision_first_among_distractors(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            repo = pathlib.Path(temp)
            (repo / "PROJECT.md").write_text("demo\n", encoding="utf-8")
            self.assertEqual(0, run([str(CORE), "init", "--host", "codex"], repo)[0])
            self.assertEqual(0, run([str(DECISION_INIT), "--host", "codex", "--core-cli", str(CORE)], repo)[0])
            def record(title, summary, key, decision, rationale, rejected, revisit):
                rc, payload = run([str(WORKFLOW), "record", "--host", "codex", "--inline", "--approved", "--title", title, "--summary", summary,
                                   "--scope", "repository", "--decision-key", key, "--commitment-evidence", "seed", "--sec-decision", decision,
                                   "--sec-rationale", rationale, "--sec-alternatives", rejected, "--sec-revisit", revisit,
                                   "--attest-explicit-choice", "--attest-scope-identified", "--attest-commitment-present"], repo)
                self.assertEqual(0, rc, payload)
            for comp in ("billing worker", "ingest service", "admin console", "mobile client", "reporting job", "partner gateway"):
                record(f"EU support hours for the {comp}", "Staff EU support during EU business hours from the EU team.", f"eu-support-{comp.replace(' ', '-')}",
                       f"Staff EU support during EU business hours from the EU team in the {comp}.", "Cross-region handoffs delayed contract customers.",
                       "Follow-the-sun support from any region.", "Revisit when EU contract volume drops below ten percent.")
                record(f"Module ownership for the {comp}", "Assign one owning team per top-level module with a CODEOWNERS entry.", f"module-ownership-{comp.replace(' ', '-')}",
                       f"Assign one owning team per top-level module with a CODEOWNERS entry in the {comp}.", "Unowned modules accumulated unreviewed changes.",
                       "Shared ownership of every module.", "Revisit when the team count changes.")
            record("Billing architecture", "Keep billing inside the modular monolith until operational ownership and scaling evidence justify extraction.",
                   "billing-architecture", "Keep billing inside the modular monolith.", "The two-person team cannot operate distributed deployment and failure modes.",
                   "An independent billing microservice, until an operations owner exists.", "Revisit when an operations owner and independent scaling evidence exist.")
            rc, payload = run([str(CHECK), "check", "--statement", "Extract billing into an independently deployed microservice today so the team can work more autonomously."], repo)
            self.assertEqual(0, rc, payload)
            current = payload["result"]["comparison_input"]["current"]
            self.assertTrue(current, "discovery returned nothing")
            self.assertEqual("Billing architecture", current[0]["title"], [c["title"] for c in current])
            self.assertIn("Rejected alternatives", current[0]["sections"])
            record_path = next(repo.glob("context/decision/Billing-architecture.md"))
            self.assertIn("microservic", record_path.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
