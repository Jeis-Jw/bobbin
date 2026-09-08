"""W3: generated indexes are projections of the artifacts.

Branches that each record a decision merge without an index conflict (init installs a
union merge attribute for Git vaults), ``refresh --check`` fails CI on drift, index-only
drift no longer blocks an approved write (core re-derives the index under the root lock),
while target-artifact drift, concurrent same/overlapping-slot Currents and merge-created
duplicate slots still hold, and an interrupted apply leaves no partial write.
"""
from __future__ import annotations

import importlib.util
import json
import pathlib
import subprocess
import sys
import tempfile
import time
import unittest
from unittest import mock

ROOT = pathlib.Path(__file__).resolve().parents[2]
CORE = ROOT / "plugins/bobbin/skills/context/scripts/context_cli.py"
DECISION_INIT = ROOT / "plugins/bobbin/skills/init/scripts/decision_init.py"
WORKFLOW = ROOT / "plugins/bobbin/skills/decision/scripts/decision_workflow.py"
CHECK = ROOT / "plugins/bobbin/skills/decision/scripts/decision_cli.py"
INDEX = "context/decision/decision.index.md"
GIT_IDENT = ["-c", "user.name=w3", "-c", "user.email=w3@example.invalid"]
BEGIN = "<!-- BEGIN CONTEXT GENERATED:current -->"
END = "<!-- END CONTEXT GENERATED:current -->"


def run(argv, cwd):
    completed = subprocess.run([sys.executable, *argv, "--json"], cwd=cwd, capture_output=True, text=True)
    try:
        return completed.returncode, json.loads(completed.stdout)
    except json.JSONDecodeError:  # pragma: no cover - diagnostic path
        raise AssertionError(f"non-JSON output rc={completed.returncode}: {completed.stdout[-800:]} {completed.stderr[-800:]}")


def git(repo, *args, check=True):
    completed = subprocess.run(["git", *GIT_IDENT, *args], cwd=repo, capture_output=True, text=True)
    if check and completed.returncode != 0:
        raise AssertionError(f"git {' '.join(args)} failed: {completed.stderr}")
    return completed


def semantic(scope: str, key: str, title: str, *extra: str) -> list[str]:
    return [
        str(WORKFLOW), "record", "--host", "codex", "--core-cli", str(CORE), "--inline",
        "--title", title, "--summary", f"{title}.", "--scope", scope, "--decision-key", key,
        "--commitment-evidence", "user: remember this",
        "--sec-decision", f"{title}.", "--sec-rationale", "It keeps the service simple and offline-capable.",
        "--sec-alternatives", "A hosted alternative, rejected.", "--sec-revisit", "Revisit if the requirement changes.",
        "--attest-explicit-choice", "--attest-scope-identified", "--attest-commitment-present", "--approved", *extra,
    ]


def preview_args(scope: str, key: str, title: str, receipt: pathlib.Path, *extra: str) -> list[str]:
    arguments = semantic(scope, key, title, *extra)
    arguments[1] = "preview"
    arguments.remove("--approved")
    return [*arguments, "--receipt-file", str(receipt)]


def current_rows(repo: pathlib.Path) -> list[str]:
    lines = (repo / INDEX).read_text(encoding="utf-8").splitlines()
    return lines[lines.index(BEGIN) + 1:lines.index(END)]


def swap_current_rows(repo: pathlib.Path) -> None:
    """Simulate a union merge outcome: same rows, non-canonical order."""
    path = repo / INDEX
    lines = path.read_text(encoding="utf-8").splitlines()
    start, end = lines.index(BEGIN) + 1, lines.index(END)
    assert end - start >= 2, "need two rows to reorder"
    lines[start:end] = list(reversed(lines[start:end]))
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def artifact_paths(repo: pathlib.Path) -> dict[str, bytes]:
    return {
        p.relative_to(repo).as_posix(): p.read_bytes()
        for p in (repo / "context/decision").glob("*.md")
        if not p.name.endswith(".index.md")
    }


class IndexProjectionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.repo = pathlib.Path(self.temp.name).resolve()

    def tearDown(self) -> None:
        self.temp.cleanup()

    def init(self, *, use_git: bool = True) -> dict:
        (self.repo / "PROJECT.md").write_text("demo\n", encoding="utf-8")
        if use_git:
            git(self.repo, "init", "-q", "-b", "main")
        rc, payload = run([str(CORE), "init", "--host", "codex"], self.repo)
        self.assertEqual(0, rc, payload)
        self.assertEqual(0, run([str(DECISION_INIT), "--host", "codex", "--core-cli", str(CORE)], self.repo)[0])
        if use_git:
            git(self.repo, "add", "-A")
            git(self.repo, "commit", "-q", "-m", "base")
        return payload["result"]

    def record(self, scope: str, key: str, title: str, *extra: str) -> dict:
        rc, payload = run(semantic(scope, key, title, *extra), self.repo)
        self.assertEqual(0, rc, payload)
        self.assertTrue(payload["result"]["applied"])
        return payload["result"]

    def search_ids(self) -> dict[str, str]:
        rc, payload = run([str(CHECK), "search"], self.repo)
        self.assertEqual(0, rc, payload)
        return {item["title"]: item["id"] for item in payload["result"]["items"]}

    def refresh_check(self) -> tuple[int, dict]:
        return run([str(CORE), "refresh", "--check"], self.repo)

    # ------------------------------------------------------------------ init / doctor

    def test_init_installs_union_merge_attributes_only_for_git_vaults(self) -> None:
        (self.repo / ".gitattributes").write_text("*.png binary\n", encoding="utf-8")
        first = self.init()
        attributes = (self.repo / ".gitattributes").read_text(encoding="utf-8")
        self.assertIn("*.png binary", attributes)
        self.assertIn("context/**/*.index.md merge=union", attributes)
        self.assertEqual(1, attributes.count("BEGIN context-core-merge"))
        self.assertTrue(first["merge_attributes"]["applied"])
        self.assertIn(("merge_attributes_install", "applied"), [(p["phase"], p["status"]) for p in first["phases"]])
        rc, second = run([str(CORE), "init", "--host", "codex"], self.repo)
        self.assertEqual(0, rc, second)
        self.assertTrue(second["result"]["merge_attributes"]["noop"])
        self.assertEqual(attributes, (self.repo / ".gitattributes").read_text(encoding="utf-8"))
        rc, doctor = run([str(CORE), "doctor"], self.repo)
        self.assertEqual("ready", doctor["result"]["repository_state"])
        self.assertNotIn("merge_attributes_missing", {w.get("code") for w in doctor["result"]["warnings"]})
        (self.repo / ".gitattributes").unlink()
        rc, doctor = run([str(CORE), "doctor"], self.repo)
        self.assertEqual("ready", doctor["result"]["repository_state"])
        self.assertIn("merge_attributes_missing", {w.get("code") for w in doctor["result"]["warnings"]})

    def test_non_git_vault_is_untouched_by_the_merge_attribute(self) -> None:
        result = self.init(use_git=False)
        self.assertFalse((self.repo / ".gitattributes").exists())
        self.assertFalse(result["merge_attributes"]["requested"])
        self.assertNotIn("merge_attributes_install", [p["phase"] for p in result["phases"]])
        rc, doctor = run([str(CORE), "doctor"], self.repo)
        self.assertEqual("ready", doctor["result"]["repository_state"])
        self.assertNotIn("merge_attributes_missing", {w.get("code") for w in doctor["result"]["warnings"]})
        self.record("repository", "persistence", "Use SQLite")
        self.assertEqual(0, self.refresh_check()[0])

    # ------------------------------------------------------------------ branches

    def test_two_branches_recording_different_decisions_merge_without_index_conflict(self) -> None:
        self.init()
        git(self.repo, "checkout", "-q", "-b", "feature-a")
        self.record("service/api", "persistence", "Use SQLite for the API")
        git(self.repo, "add", "-A")
        git(self.repo, "commit", "-q", "-m", "decision a")
        artifacts_a = artifact_paths(self.repo)
        git(self.repo, "checkout", "-q", "main")
        git(self.repo, "checkout", "-q", "-b", "feature-b")
        time.sleep(1.1)  # distinct created_at so the canonical order is well defined
        self.record("service/worker", "queue", "Use Redis streams for the worker")
        git(self.repo, "add", "-A")
        git(self.repo, "commit", "-q", "-m", "decision b")
        artifacts_b = artifact_paths(self.repo)
        merged = git(self.repo, "merge", "--no-edit", "feature-a", check=False)
        self.assertEqual(0, merged.returncode, merged.stdout + merged.stderr)
        self.assertEqual("", git(self.repo, "diff", "--name-only", "--diff-filter=U").stdout.strip())
        after = artifact_paths(self.repo)
        self.assertEqual({**artifacts_a, **artifacts_b}, after)
        self.assertEqual(2, len(current_rows(self.repo)))
        rc, payload = self.refresh_check()
        if rc != 0:
            self.assertEqual(6, rc, payload)
            self.assertEqual("projection_drift", payload["error"]["code"])
        rc, fixed = run([str(CORE), "refresh", "--fix", "index"], self.repo)
        self.assertEqual(0, rc, fixed)
        self.assertEqual([], fixed["result"]["issues"])
        rc, again = run([str(CORE), "refresh", "--fix", "index"], self.repo)
        self.assertTrue(again["result"]["noop"], again)
        rc, payload = self.refresh_check()
        self.assertEqual(0, rc, payload)
        self.assertEqual("ok", payload["result"]["check"])
        rc, doctor = run([str(CORE), "doctor"], self.repo)
        self.assertEqual("ready", doctor["result"]["repository_state"], doctor)
        self.assertEqual({"Use SQLite for the API", "Use Redis streams for the worker"}, set(self.search_ids()))
        self.assertEqual(after, artifact_paths(self.repo))

    def test_same_slot_from_two_branches_is_reported_and_holds_only_that_slot(self) -> None:
        self.init()
        git(self.repo, "checkout", "-q", "-b", "feature-a")
        self.record("repository", "persistence", "Use SQLite")
        git(self.repo, "add", "-A")
        git(self.repo, "commit", "-q", "-m", "a")
        git(self.repo, "checkout", "-q", "main")
        git(self.repo, "checkout", "-q", "-b", "feature-b")
        time.sleep(1.1)
        self.record("repository", "persistence", "Use PostgreSQL")
        git(self.repo, "add", "-A")
        git(self.repo, "commit", "-q", "-m", "b")
        merged = git(self.repo, "merge", "--no-edit", "feature-a", check=False)
        self.assertEqual(0, merged.returncode, merged.stdout + merged.stderr)
        run([str(CORE), "refresh", "--fix", "index"], self.repo)
        rc, payload = self.refresh_check()
        self.assertEqual(6, rc, payload)
        self.assertEqual("integrity_issues", payload["error"]["code"])
        self.assertIn("duplicate_current_slot", {issue["code"] for issue in payload["error"]["details"]["issues"]})
        rc, doctor = run([str(CORE), "doctor"], self.repo)
        self.assertEqual("invalid", doctor["result"]["repository_state"])
        before = artifact_paths(self.repo)
        rc, blocked = run(semantic("repository", "persistence", "Use DuckDB"), self.repo)
        self.assertEqual(5, rc, blocked)
        self.assertEqual("decision_slot_conflict", blocked["error"]["code"])
        self.assertEqual(2, len(blocked["error"]["details"]["current_candidates"]))
        self.assertEqual(before, artifact_paths(self.repo))
        # An unrelated slot keeps working while the duplicate is reported, not auto-resolved.
        self.record("repository", "cache", "Use an in-process LRU cache")
        self.assertEqual(3, len(current_rows(self.repo)))
        ids = self.search_ids()
        rc, withdrawn = run([str(WORKFLOW), "record", "--host", "codex", "--core-cli", str(CORE), "--withdraw", ids["Use PostgreSQL"],
                             "--reason", "Keep the SQLite decision after the merge.", "--approved"], self.repo)
        self.assertEqual(0, rc, withdrawn)
        rc, payload = self.refresh_check()
        self.assertEqual(0, rc, payload)

    # ------------------------------------------------------------------ CI check

    def test_refresh_check_fails_on_row_drift_and_fix_index_restores_canonical_bytes(self) -> None:
        self.init(use_git=False)
        self.record("repository", "persistence", "Use SQLite")
        time.sleep(1.1)
        self.record("repository", "cache", "Use an in-process LRU cache")
        canonical = (self.repo / INDEX).read_bytes()
        self.assertEqual(0, self.refresh_check()[0])
        swap_current_rows(self.repo)
        rc, payload = self.refresh_check()
        self.assertEqual(6, rc, payload)
        self.assertEqual("projection_drift", payload["error"]["code"])
        self.assertTrue(any(w["code"].startswith("index_") for w in payload["error"]["details"]["drift"]))
        self.assertEqual(2, len(self.search_ids()), "recall keeps working on a non-canonical projection")
        rc, fixed = run([str(CORE), "refresh", "--fix", "index"], self.repo)
        self.assertEqual(0, rc, fixed)
        self.assertEqual([INDEX], fixed["result"]["changed_paths"])
        self.assertEqual(canonical, (self.repo / INDEX).read_bytes())
        self.assertEqual(0, self.refresh_check()[0])

    # ------------------------------------------------------------------ CAS separation

    def test_index_only_drift_does_not_block_an_approved_write(self) -> None:
        self.init(use_git=False)
        self.record("repository", "persistence", "Use SQLite")
        for variant in ("unrelated_addition", "row_reorder"):
            with self.subTest(variant=variant):
                receipt = self.repo.parent / f"w3-{variant}-{self.repo.name}.json"
                receipt.unlink(missing_ok=True)
                title = f"Use structured logging ({variant})"
                rc, previewed = run(preview_args("repository", f"logging-{variant}", title, receipt), self.repo)
                self.assertEqual(0, rc, previewed)
                digest = previewed["result"]["approval_digest"]
                if variant == "unrelated_addition":
                    time.sleep(1.1)
                    self.record("payments", f"gateway-{variant}", f"Use the hosted payment gateway ({variant})")
                else:
                    swap_current_rows(self.repo)
                rc, applied = run([str(WORKFLOW), "apply", "--core-cli", str(CORE), "--receipt-file", str(receipt),
                                   "--approved-digest", digest], self.repo)
                self.assertEqual(0, rc, applied)
                self.assertTrue(applied["result"]["applied"])
                self.assertIn(title, self.search_ids())
                rc, payload = self.refresh_check()
                self.assertEqual(0, rc, payload)

    def test_concurrent_same_or_overlapping_slot_still_blocks_the_write(self) -> None:
        self.init(use_git=False)
        for variant, concurrent_scope in (("same_slot", "service/api"), ("overlapping_scope", "service")):
            with self.subTest(variant=variant):
                receipt = self.repo.parent / f"w3-slot-{variant}-{self.repo.name}.json"
                receipt.unlink(missing_ok=True)
                key = f"persistence-{variant}"
                rc, previewed = run(preview_args("service/api", key, f"Use SQLite ({variant})", receipt), self.repo)
                self.assertEqual(0, rc, previewed)
                self.record(concurrent_scope, key, f"Use PostgreSQL ({variant})")
                before = artifact_paths(self.repo)
                rc, blocked = run([str(WORKFLOW), "apply", "--core-cli", str(CORE), "--receipt-file", str(receipt),
                                   "--approved-digest", previewed["result"]["approval_digest"]], self.repo)
                self.assertEqual(5, rc, blocked)
                self.assertEqual("precondition_changed", blocked["error"]["code"])
                self.assertEqual(before, artifact_paths(self.repo))
                self.assertNotIn(f"Use SQLite ({variant})", self.search_ids())
                self.assertEqual(0, self.refresh_check()[0])

    def test_target_artifact_drift_still_blocks_a_supersede(self) -> None:
        self.init(use_git=False)
        self.record("repository", "persistence", "Use SQLite")
        predecessor_id = self.search_ids()["Use SQLite"]
        receipt = self.repo.parent / f"w3-supersede-{self.repo.name}.json"
        receipt.unlink(missing_ok=True)
        rc, previewed = run(preview_args("repository", "persistence", "Use PostgreSQL", receipt, "--supersede", predecessor_id), self.repo)
        self.assertEqual(0, rc, previewed)
        predecessor_path = next(p for p in (self.repo / "context/decision").glob("*.md") if not p.name.endswith(".index.md"))
        predecessor_path.write_text(predecessor_path.read_text(encoding="utf-8") + "\nEdited after preview.\n", encoding="utf-8")
        before = artifact_paths(self.repo)
        rc, blocked = run([str(WORKFLOW), "apply", "--core-cli", str(CORE), "--receipt-file", str(receipt),
                           "--approved-digest", previewed["result"]["approval_digest"]], self.repo)
        # The modified move source fails closed on the existing artifact-identity check.
        self.assertIn(rc, (5, 6), blocked)
        self.assertIn(blocked["error"]["code"], {"precondition_changed", "duplicate_id"})
        self.assertEqual(before, artifact_paths(self.repo))
        self.assertEqual({"Use SQLite"}, set(self.search_ids()))

    def test_record_works_on_a_non_canonical_projection_and_leaves_it_canonical(self) -> None:
        self.init(use_git=False)
        self.record("repository", "persistence", "Use SQLite")
        time.sleep(1.1)
        self.record("repository", "cache", "Use an in-process LRU cache")
        swap_current_rows(self.repo)
        self.assertEqual(6, self.refresh_check()[0])
        self.record("repository", "logging", "Use structured logging")
        self.assertEqual(3, len(current_rows(self.repo)))
        rc, payload = self.refresh_check()
        self.assertEqual(0, rc, payload)
        self.assertEqual(3, len(self.search_ids()))

    # ------------------------------------------------------------------ rollback

    def test_interrupted_apply_leaves_no_partial_write(self) -> None:
        self.init(use_git=False)
        receipt = self.repo.parent / f"w3-rollback-{self.repo.name}.json"
        receipt.unlink(missing_ok=True)
        rc, previewed = run(preview_args("repository", "persistence", "Use SQLite", receipt), self.repo)
        self.assertEqual(0, rc, previewed)
        frozen = json.loads(receipt.read_text(encoding="utf-8"))
        bundle = frozen["approval_material"]["core_bundle"]
        before_tree = {p.relative_to(self.repo).as_posix(): p.read_bytes() for p in (self.repo / "context").rglob("*") if p.is_file()}
        spec = importlib.util.spec_from_file_location("context_cli_w3_rollback", CORE)
        context_cli = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = context_cli  # dataclasses resolve the module through sys.modules
        spec.loader.exec_module(context_cli)
        real_write = context_cli._atomic_write

        def failing_index_write(path, content):
            if path.name.endswith(".index.md"):
                raise OSError("simulated index write failure")
            real_write(path, content)

        with mock.patch.object(context_cli, "_atomic_write", failing_index_write):
            with self.assertRaises(OSError):
                context_cli.apply_bundle(self.repo, bundle, bundle["approval_digest"])
        after_tree = {p.relative_to(self.repo).as_posix(): p.read_bytes() for p in (self.repo / "context").rglob("*") if p.is_file()}
        self.assertEqual(before_tree, after_tree, "artifact write was rolled back after the index step failed")
        rc, applied = run([str(WORKFLOW), "apply", "--core-cli", str(CORE), "--receipt-file", str(receipt),
                           "--approved-digest", previewed["result"]["approval_digest"]], self.repo)
        self.assertEqual(0, rc, applied)
        self.assertEqual({"Use SQLite"}, set(self.search_ids()))
        self.assertEqual(0, self.refresh_check()[0])


if __name__ == "__main__":
    unittest.main()
