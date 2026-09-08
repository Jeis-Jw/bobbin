import hashlib
import json
import multiprocessing
import sys
import tempfile
import unittest
from pathlib import Path

from phase0_contract import (
    MechanismContractError,
    atomic_replace_exact,
    changed_move,
    collision_key,
    index_row_from_entry,
    index_row_to_entry,
    parallel_index_mutation_worker,
    repository_lock_path,
    repository_root_wikilink,
    slug_filename,
    try_fcntl_lock_worker,
)


FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "filesystem-mechanisms"


def load_fixture(name):
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def sha256_bytes(value):
    return "sha256:" + hashlib.sha256(value).hexdigest()


class IndexAndPathContractTests(unittest.TestCase):
    def test_generated_index_row_round_trip_is_byte_deterministic(self):
        fixture = load_fixture("generated-index-row.json")
        entry = fixture["entry"]

        row = index_row_from_entry(entry)
        self.assertEqual(row, fixture["expected_row"])
        self.assertEqual(index_row_to_entry(row), entry)
        self.assertEqual(index_row_from_entry(index_row_to_entry(row)), row)
        self.assertNotIn("generated_at", row)

    def test_repository_root_obsidian_link_is_exact(self):
        fixture = load_fixture("generated-index-row.json")
        path = fixture["entry"]["path"]

        self.assertEqual(
            repository_root_wikilink(path), fixture["expected_wikilink"]
        )
        self.assertTrue(fixture["expected_wikilink"].startswith("[[context/"))
        self.assertFalse(fixture["expected_wikilink"].endswith(".md]]"))

    def test_unicode_space_and_collision_contract(self):
        fixture = load_fixture("path-cases.json")
        for case in fixture["slug_cases"]:
            with self.subTest(case=case["id"]):
                self.assertEqual(slug_filename(case["title"]), case["expected"])

        for case in fixture["collision_cases"]:
            with self.subTest(case=case["id"]):
                self.assertEqual(
                    collision_key(case["left"]), collision_key(case["right"])
                )

    def test_rename_changes_path_only_in_index_projection(self):
        fixture = load_fixture("rename-case.json")
        before = fixture["before"]
        after = fixture["after"]

        self.assertEqual(before["id"], after["id"])
        self.assertEqual(before["relations"], after["relations"])
        self.assertNotEqual(before["path"], after["path"])
        self.assertIn(before["path"], index_row_from_entry(before["index_entry"]))
        self.assertIn(after["path"], index_row_from_entry(after["index_entry"]))


@unittest.skipUnless(
    sys.platform == "darwin" or sys.platform.startswith("linux"),
    "context-common/v2 supports fcntl only on macOS/Linux",
)
class FilesystemMechanismTests(unittest.TestCase):
    def test_root_lock_path_and_parent_mode_are_deterministic(self):
        with tempfile.TemporaryDirectory() as tmp:
            repository = Path(tmp).resolve()
            expected_key = hashlib.sha256(str(repository).encode("utf-8")).hexdigest()
            lock_path = repository_lock_path(repository)

            self.assertEqual(
                lock_path,
                Path(tempfile.gettempdir()) / "context-core-locks" / expected_key,
            )
            self.assertEqual(lock_path.parent.stat().st_mode & 0o777, 0o700)

    def test_fcntl_lock_excludes_parallel_process(self):
        with tempfile.TemporaryDirectory() as tmp:
            lock_path = Path(tmp) / "context.lock"
            ready = multiprocessing.Event()
            release = multiprocessing.Event()
            queue = multiprocessing.Queue()
            holder = multiprocessing.Process(
                target=try_fcntl_lock_worker,
                args=(str(lock_path), True, ready, release, queue),
            )
            holder.start()
            self.assertTrue(ready.wait(5), "lock holder did not start")

            contender = multiprocessing.Process(
                target=try_fcntl_lock_worker,
                args=(str(lock_path), False, None, None, queue),
            )
            contender.start()
            contender.join(5)
            self.assertFalse(contender.is_alive(), "lock contender did not finish")
            self.assertEqual(queue.get(timeout=2), "blocked")

            release.set()
            holder.join(5)
            self.assertFalse(holder.is_alive(), "lock holder did not finish")
            self.assertEqual(queue.get(timeout=2), "acquired")

    def test_exact_byte_precondition_and_same_directory_replace(self):
        fixture = load_fixture("atomic-replace.json")
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "artifact.md"
            target.write_bytes(fixture["before"].encode("utf-8"))
            observed = []

            atomic_replace_exact(
                target,
                fixture["after"].encode("utf-8"),
                sha256_bytes(fixture["before"].encode("utf-8")),
                replace_observer=lambda source, destination: observed.append(
                    (Path(source), Path(destination))
                ),
            )
            self.assertEqual(target.read_text(encoding="utf-8"), fixture["after"])
            self.assertEqual(observed[0][0].parent, target.parent)
            self.assertEqual(observed[0][1], target)

            changed_bytes = b"out-of-band\n"
            target.write_bytes(changed_bytes)
            with self.assertRaisesRegex(
                MechanismContractError, "precondition_changed"
            ):
                atomic_replace_exact(
                    target,
                    b"must-not-write\n",
                    sha256_bytes(fixture["after"].encode("utf-8")),
                )
            self.assertEqual(target.read_bytes(), changed_bytes)

    def test_changed_move_start_prepared_final_resume(self):
        fixture = load_fixture("changed-move-states.json")
        for state in fixture["states"]:
            with self.subTest(state=state["id"]), tempfile.TemporaryDirectory() as tmp:
                source = Path(tmp) / "active.md"
                destination = Path(tmp) / "retired" / "active--abc123.md"
                destination.parent.mkdir()
                before = fixture["before"].encode("utf-8")
                after = fixture["after"].encode("utf-8")

                if state["source"] == "before":
                    source.write_bytes(before)
                if state["destination"] == "after":
                    destination.write_bytes(after)
                destination_mtime = (
                    destination.stat().st_mtime_ns if destination.exists() else None
                )

                result = changed_move(
                    source,
                    destination,
                    before_digest=sha256_bytes(before),
                    after_bytes=after,
                )

                self.assertEqual(result, state["expected_result"])
                self.assertFalse(source.exists())
                self.assertEqual(destination.read_bytes(), after)
                if state["id"] in {"prepared", "final"}:
                    self.assertEqual(destination.stat().st_mtime_ns, destination_mtime)

    def test_changed_move_forced_crash_resumes_only_same_bundle(self):
        fixture = load_fixture("changed-move-states.json")
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / "active.md"
            destination = Path(tmp) / "retired" / "active--abc123.md"
            destination.parent.mkdir()
            before = fixture["before"].encode("utf-8")
            after = fixture["after"].encode("utf-8")
            source.write_bytes(before)

            with self.assertRaisesRegex(RuntimeError, "forced_crash_after_prepare"):
                changed_move(
                    source,
                    destination,
                    before_digest=sha256_bytes(before),
                    after_bytes=after,
                    crash_after_prepare=True,
                )
            self.assertEqual(source.read_bytes(), before)
            self.assertEqual(destination.read_bytes(), after)

            with self.assertRaisesRegex(
                MechanismContractError, "precondition_changed"
            ):
                changed_move(
                    source,
                    destination,
                    before_digest=sha256_bytes(before),
                    after_bytes=b"different-approved-bundle\n",
                )

            self.assertEqual(
                changed_move(
                    source,
                    destination,
                    before_digest=sha256_bytes(before),
                    after_bytes=after,
                ),
                "resumed_prepared",
            )

    def test_parallel_mutations_have_no_lost_rows(self):
        fixture = load_fixture("parallel-mutation.json")
        with tempfile.TemporaryDirectory() as tmp:
            repository = Path(tmp).resolve()
            index_path = repository / "rows.json"
            index_path.write_text("[]\n", encoding="utf-8")
            start = multiprocessing.Event()
            processes = [
                multiprocessing.Process(
                    target=parallel_index_mutation_worker,
                    args=(str(repository), str(index_path), row, start),
                )
                for row in fixture["rows"]
            ]
            for process in processes:
                process.start()
            start.set()
            for process in processes:
                process.join(10)
                self.assertEqual(process.exitcode, 0)

            actual = json.loads(index_path.read_text(encoding="utf-8"))
            self.assertEqual(actual, sorted(fixture["rows"], key=lambda row: row["id"]))
            self.assertEqual(len({row["id"] for row in actual}), len(fixture["rows"]))


if __name__ == "__main__":
    unittest.main()
