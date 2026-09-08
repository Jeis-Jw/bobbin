#!/usr/bin/env python3
from __future__ import annotations

import copy
import hashlib
import importlib.util
import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


PLUGIN = next(p for p in Path(__file__).resolve().parents if (p / "pytest.ini").is_file()) / "plugins/bobbin"
CLI_PATH = PLUGIN / "skills/context/scripts/context_cli.py"
SPEC = importlib.util.spec_from_file_location("context_cli_transactions", CLI_PATH)
assert SPEC and SPEC.loader
context_cli = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = context_cli
SPEC.loader.exec_module(context_cli)

DECISION_CLI_PATH = PLUGIN / "skills/decision/scripts/decision_cli.py"
DECISION_SPEC = importlib.util.spec_from_file_location("decision_cli_transactions", DECISION_CLI_PATH)
assert DECISION_SPEC and DECISION_SPEC.loader
decision_cli = importlib.util.module_from_spec(DECISION_SPEC)
sys.modules[DECISION_SPEC.name] = decision_cli
DECISION_SPEC.loader.exec_module(decision_cli)


def vault_dir() -> tempfile.TemporaryDirectory[str]:
    temp = tempfile.TemporaryDirectory()
    return temp


def tree_digest(root: Path) -> str:
    digest = hashlib.sha256()
    for path in sorted(p for p in root.rglob("*") if p.is_file()):
        digest.update(path.relative_to(root).as_posix().encode())
        digest.update(path.read_bytes())
    return digest.hexdigest()


def initialize(repo: Path) -> None:
    result = context_cli.build_init_bundle(repo)
    context_cli.apply_bundle(repo, result["bundle"], result["approval_digest"])


def observation_owner_result(identifier: str = "ctx_550e8400e29b41d4a716446655440000") -> dict:
    capability = context_cli.builtin_capability("observation")
    candidate = {
        "schema": "context-capture-candidate/v1",
        "candidate_id": "cand_550e8400e29b41d4a716446655440000",
        "title": "Cookie 전달 관찰",
        "claim": "Safari에서 cookie 전달이 차단된다.",
        "summary": "Safari cookie 전달 실패를 재현했다.",
        "captured_from": "workspace",
        "requested_kind": "observation",
        "specialized_kinds": ["observation"],
        "fallback_kind": None,
        "owner_inputs": {"observation": {"observation": "Safari에서 cookie 전달이 차단된다.", "evidence": ["integration fixture"]}},
    }
    input_digest = context_cli.canonical_digest(candidate)
    content = context_cli.render_document(
        {
            "schema": "context-observation/v1",
            "id": identifier,
            "title": candidate["title"],
            "summary": candidate["summary"],
            "created_at": "2026-08-13T18:20:00+09:00",
            "captured_from": "workspace",
        },
        {"관찰": candidate["claim"], "근거": "integration fixture"},
    )
    return {
        "schema": "context-owner-result/v1",
        "result_type": "claim",
        "transition": "capture",
        "owner": "context-core",
        "target_kind": "observation",
        "candidate_id": candidate["candidate_id"],
        "decision": "claim",
        "reason": "reusable evidence",
        "capability_digest": context_cli.canonical_digest(capability),
        "semantic_inputs": [{"operation": "claim", "input_schema": candidate["schema"], "input_digest": input_digest, "value": candidate}],
        "semantic_attestations": [{
            "schema": "context-semantic-attestation/v1",
            "operation": "claim",
            "input_schema": candidate["schema"],
            "input_digest": input_digest,
            "assertions": [
                {"name": "reusable_observation", "value": True, "evidence_pointers": ["/owner_inputs/observation/observation"]},
                {"name": "evidence_present", "value": True, "evidence_pointers": ["/owner_inputs/observation/evidence/0"]},
            ],
        }],
        "artifact_drafts": [{
            "effect_id": "effect_create_observation",
            "path": "context/observation/Cookie-전달-관찰.md",
            "content": content,
            "semantic_projection": {"kind": "observation", "primary_claim": candidate["claim"], "supporting_context": ["integration fixture"]},
        }],
        "effects": [{"effect_id": "effect_create_observation", "action": "create", "area": "observation", "id": identifier, "state": "current"}],
        "proposed_plan": {"schema": "context-owner-plan/v1", "transition": "capture", "operations": [{"op": "create", "effect_id": "effect_create_observation", "area": "observation", "path": "context/observation/Cookie-전달-관찰.md"}]},
    }


def decision_candidate(candidate_id: str, decision: str) -> dict:
    return {
        "schema": "context-capture-candidate/v1",
        "candidate_id": candidate_id,
        "title": "인증 세션 소유권",
        "claim": decision,
        "summary": "OAuth callback과 cookie boundary를 한 경계로 통합한다.",
        "captured_from": "conversation",
        "requested_kind": "decision",
        "specialized_kinds": ["decision"],
        "fallback_kind": None,
        "scope_hint": "project/auth",
        "source_refs": ["conversation:test"],
        "evidence": ["결정 권한자가 현재 따를 선택으로 확정했다."],
        "tags": ["auth"],
        "owner_inputs": {
            "decision": {
                "decision": decision,
                "rationale": "브라우저별 cookie 차이를 서버 경계 안으로 모은다.",
                "rejected_alternatives": ["SPA token 소유: XSS 노출이 커져 반려"],
                "decision_key": "session-owner",
            }
        },
    }


def decision_attestation(candidate: dict) -> dict:
    return {
        "schema": "context-semantic-attestation/v1",
        "operation": "claim",
        "input_schema": candidate["schema"],
        "input_digest": decision_cli.canonical_digest(candidate),
        "assertions": [
            {"name": "explicit_choice", "value": True, "evidence_pointers": ["/owner_inputs/decision/decision"]},
            {"name": "scope_identified", "value": True, "evidence_pointers": ["/scope_hint"]},
            {"name": "commitment_present", "value": True, "evidence_pointers": ["/evidence/0"]},
        ],
    }


class TransactionCoordinatorTests(unittest.TestCase):
    def test_acceptance_01_init_is_idempotent(self) -> None:
        with vault_dir() as temp:
            repo = Path(temp)
            keep = repo / "keep.txt"
            keep.write_text("preserve existing repository content\n", encoding="utf-8")
            before = tree_digest(repo)
            completed = subprocess.run(
                [sys.executable, str(CLI_PATH), "init", "--host", "codex", "--json"],
                cwd=repo,
                text=True,
                capture_output=True,
            )
            self.assertEqual(0, completed.returncode, completed.stdout + completed.stderr)
            first = json.loads(completed.stdout)["result"]
            self.assertEqual("applied", first["phases"][0]["status"])
            self.assertEqual("ready", first["doctor"]["repository_state"])
            self.assertEqual(
                {"requested": True, "target": "AGENTS.md", "applied": True, "noop": False},
                first["policy"],
            )
            self.assertEqual("preserve existing repository content\n", keep.read_text(encoding="utf-8"))
            self.assertNotEqual(before, tree_digest(repo))

            capture = context_cli.finalize_owner_result(repo, observation_owner_result())
            context_cli.apply_bundle(repo, capture["bundle"], capture["approval_digest"])
            after_user_content = tree_digest(repo)
            repeated = subprocess.run(
                [sys.executable, str(CLI_PATH), "init", "--host", "codex", "--json"],
                cwd=repo,
                text=True,
                capture_output=True,
            )
            self.assertEqual(0, repeated.returncode, repeated.stdout + repeated.stderr)
            second = json.loads(repeated.stdout)["result"]
            self.assertTrue(second["noop"])
            self.assertEqual(
                [("core_init", "noop"), ("policy_install", "noop")],
                [(phase["phase"], phase["status"]) for phase in second["phases"]],
            )
            self.assertEqual(after_user_content, tree_digest(repo))
            self.assertIn(context_cli.POLICY_BODY, (repo / "AGENTS.md").read_text(encoding="utf-8"))
            self.assertFalse((repo / "CLAUDE.md").exists())

        corruptions = {
            "evil-owner": lambda repo: (
                (repo / context_cli.ROOT_INDEX).write_text(
                    (repo / context_cli.ROOT_INDEX).read_text(encoding="utf-8").replace(
                        '"area":"snapshot","path":"context/snapshot/snapshot.index.md","owner":"context-core"',
                        '"area":"snapshot","path":"context/snapshot/snapshot.index.md","owner":"evil-owner"',
                    ),
                    encoding="utf-8",
                ),
                (repo / "context/snapshot/snapshot.index.md").write_text(
                    (repo / "context/snapshot/snapshot.index.md").read_text(encoding="utf-8").replace(
                        'owner: "context-core"', 'owner: "evil-owner"'
                    ),
                    encoding="utf-8",
                ),
            ),
            "wrong-schema": lambda repo: (repo / "context/snapshot/snapshot.index.md").write_text(
                (repo / "context/snapshot/snapshot.index.md").read_text(encoding="utf-8").replace(
                    'schema: "context-area-index/v1"', 'schema: "context-area-index/v2"'
                ),
                encoding="utf-8",
            ),
            "wrong-path": lambda repo: (repo / context_cli.ROOT_INDEX).write_text(
                (repo / context_cli.ROOT_INDEX).read_text(encoding="utf-8").replace(
                    'context/snapshot/snapshot.index.md', 'context/snapshot/other.index.md'
                ),
                encoding="utf-8",
            ),
            "wrong-authority": lambda repo: (
                (repo / context_cli.ROOT_INDEX).write_text(
                    (repo / context_cli.ROOT_INDEX).read_text(encoding="utf-8").replace(
                        '"area":"snapshot","path":"context/snapshot/snapshot.index.md","owner":"context-core","claims":["snapshot"],"artifact_schema":"context-snapshot/v1","authority":"staging"',
                        '"area":"snapshot","path":"context/snapshot/snapshot.index.md","owner":"context-core","claims":["snapshot"],"artifact_schema":"context-snapshot/v1","authority":"authoritative"',
                    ),
                    encoding="utf-8",
                ),
                (repo / "context/snapshot/snapshot.index.md").write_text(
                    (repo / "context/snapshot/snapshot.index.md").read_text(encoding="utf-8").replace(
                        'authority: "staging"', 'authority: "authoritative"'
                    ),
                    encoding="utf-8",
                ),
            ),
            "wrong-artifact-schema": lambda repo: (
                (repo / context_cli.ROOT_INDEX).write_text(
                    (repo / context_cli.ROOT_INDEX).read_text(encoding="utf-8").replace(
                        '"area":"snapshot","path":"context/snapshot/snapshot.index.md","owner":"context-core","claims":["snapshot"],"artifact_schema":"context-snapshot/v1"',
                        '"area":"snapshot","path":"context/snapshot/snapshot.index.md","owner":"context-core","claims":["snapshot"],"artifact_schema":"context-evil/v1"',
                    ),
                    encoding="utf-8",
                ),
                (repo / "context/snapshot/snapshot.index.md").write_text(
                    (repo / "context/snapshot/snapshot.index.md").read_text(encoding="utf-8").replace(
                        'artifact_schema: "context-snapshot/v1"', 'artifact_schema: "context-evil/v1"'
                    ),
                    encoding="utf-8",
                ),
            ),
            "partial-index-set": lambda repo: (repo / "context/observation/observation.index.md").unlink(),
        }
        for label, corrupt in corruptions.items():
            with self.subTest(corruption=label), vault_dir() as temp:
                repo = Path(temp)
                initialize(repo)
                corrupt(repo)
                before = tree_digest(repo)
                with self.assertRaises(context_cli.ContextError) as caught:
                    context_cli.bootstrap_repository(repo, host="codex")
                self.assertEqual("partial_core_init", caught.exception.code)
                self.assertEqual("failed", caught.exception.details["phases"][-1]["status"])
                self.assertEqual(before, tree_digest(repo))

    def test_target_write_ignores_unrelated_invalid_artifact(self) -> None:
        with vault_dir() as temp:
            repo = Path(temp)
            initialize(repo)
            polluted = repo / "context/snapshot/unrelated-broken.md"
            polluted.write_text("---\nschema: not-json\n---\n", encoding="utf-8")
            polluted_before = polluted.read_bytes()
            self.assertEqual("invalid", context_cli.doctor_repository(repo)["repository_state"])

            preview = context_cli.finalize_owner_result(repo, observation_owner_result())
            applied = context_cli.apply_bundle(repo, preview["bundle"], preview["approval_digest"])

            self.assertIn("context/observation/Cookie-전달-관찰.md", applied["changed_paths"])
            self.assertEqual(polluted_before, polluted.read_bytes())
            self.assertEqual("invalid", context_cli.doctor_repository(repo)["repository_state"])

    def test_write_rechecks_duplicate_id_and_target_area_authority(self) -> None:
        with vault_dir() as temp:
            repo = Path(temp)
            initialize(repo)
            result = observation_owner_result()
            preview = context_cli.finalize_owner_result(repo, result)
            duplicate = repo / "context/observation/unindexed-duplicate.md"
            duplicate.write_text(result["artifact_drafts"][0]["content"], encoding="utf-8")
            before = tree_digest(repo)

            with self.assertRaises(context_cli.ContextError) as caught:
                context_cli.apply_bundle(repo, preview["bundle"], preview["approval_digest"])
            self.assertEqual("duplicate_id", caught.exception.code)
            self.assertEqual(before, tree_digest(repo))

        with vault_dir() as temp:
            repo = Path(temp)
            initialize(repo)
            root_index = repo / context_cli.ROOT_INDEX
            root_before = root_index.read_bytes()
            area_index = repo / "context/observation/observation.index.md"
            area_index.write_text(
                area_index.read_text(encoding="utf-8").replace(
                    'owner: "context-core"', 'owner: "untrusted-owner"'
                ),
                encoding="utf-8",
            )
            repaired = context_cli.repair_derived_indexes(repo)
            self.assertFalse(repaired["applied"])
            self.assertIn("area_index_mismatch", {warning["code"] for warning in repaired["warnings"]})
            self.assertEqual(root_before, root_index.read_bytes())

            with self.assertRaises(context_cli.ContextError) as caught:
                context_cli.finalize_owner_result(repo, observation_owner_result())
            self.assertEqual("area_index_mismatch", caught.exception.code)

    def test_explicit_init_resumes_exact_core_prefix_after_interruption(self) -> None:
        with vault_dir() as temp:
            repo = Path(temp)
            keep = repo / "keep.txt"
            keep.write_text("preserve\n", encoding="utf-8")
            original = context_cli._atomic_write
            calls = 0

            def interrupt_after_first_write(path: Path, content: str) -> None:
                nonlocal calls
                original(path, content)
                calls += 1
                if calls == 1:
                    raise RuntimeError("fault after first core index write")

            context_cli._atomic_write = interrupt_after_first_write
            try:
                with self.assertRaisesRegex(RuntimeError, "first core index"):
                    context_cli.bootstrap_repository(repo, host="codex")
            finally:
                context_cli._atomic_write = original

            self.assertTrue((repo / context_cli.ROOT_INDEX).is_file())
            self.assertFalse((repo / "context/observation/observation.index.md").exists())
            resumed = context_cli.bootstrap_repository(repo, host="codex")
            self.assertEqual("applied", resumed["phases"][0]["status"])
            self.assertEqual("ready", resumed["doctor"]["repository_state"])
            self.assertEqual("preserve\n", keep.read_text(encoding="utf-8"))

    def test_explicit_init_repairs_missing_root_index_in_populated_repository(self) -> None:
        with vault_dir() as temp:
            repo = Path(temp)
            initialize(repo)
            (repo / context_cli.ROOT_INDEX).unlink()
            doctor = context_cli.doctor_repository(repo)
            self.assertEqual("partial", doctor["repository_state"])
            self.assertEqual("index_missing", doctor["warnings"][0]["code"])

            initialized = context_cli.bootstrap_repository(repo, host="codex")
            self.assertEqual("applied", initialized["phases"][0]["status"])
            self.assertIn(context_cli.ROOT_INDEX, initialized["phases"][0]["changed_paths"])
            self.assertEqual("ready", initialized["doctor"]["repository_state"])

    def test_explicit_init_ignores_derived_root_drift_and_refresh_fix_repairs(self) -> None:
        with vault_dir() as temp:
            repo = Path(temp)
            initialize(repo)
            root_index = repo / context_cli.ROOT_INDEX
            root_index.write_text(
                root_index.read_text(encoding="utf-8").replace(
                    " — Snapshot: session handoff staging ", " — Corrupted: session handoff staging "
                ) + "\nOwner-maintained recovery note.\n",
                encoding="utf-8",
            )
            rogue = repo / "context/rogue"
            rogue.mkdir()
            (rogue / "rogue.index.md").write_text(
                context_cli._area_seed(
                    "rogue", "untrusted-owner", "context-rogue/v1", "evidence", "must stay unregistered"
                ),
                encoding="utf-8",
            )

            initialized = context_cli.bootstrap_repository(repo, host="codex")
            self.assertEqual("noop", initialized["phases"][0]["status"])
            self.assertEqual("ready", initialized["doctor"]["repository_state"])
            self.assertIn("root_index_drift", {warning["code"] for warning in initialized["doctor"]["warnings"]})

            repaired = context_cli.repair_derived_indexes(repo)
            self.assertTrue(repaired["applied"])
            self.assertEqual([], repaired["warnings"])
            repaired_root = root_index.read_text(encoding="utf-8")
            self.assertIn("Owner-maintained recovery note.", repaired_root)
            _, areas = context_cli.parse_root_index(repaired_root)
            self.assertEqual(["archive", "observation", "snapshot"], [area["area"] for area in areas])

    def test_explicit_init_rejects_noncanonical_empty_directory_prefix(self) -> None:
        with vault_dir() as temp:
            repo = Path(temp)
            (repo / "context/unexpected").mkdir(parents=True)
            before = tree_digest(repo)
            with self.assertRaises(context_cli.ContextError) as caught:
                context_cli.bootstrap_repository(repo, host="codex")
            self.assertEqual("partial_core_init", caught.exception.code)
            self.assertEqual(before, tree_digest(repo))

    def test_area_register_resumes_exact_root_prefix_after_interruption(self) -> None:
        with vault_dir() as temp:
            repo = Path(temp)
            initialize(repo)
            plan = decision_cli.build_init_plan()
            original = context_cli._atomic_write
            calls = 0

            def interrupt_after_root_write(path: Path, content: str) -> None:
                nonlocal calls
                original(path, content)
                calls += 1
                if calls == 1:
                    raise RuntimeError("fault after area root index write")

            context_cli._atomic_write = interrupt_after_root_write
            try:
                with self.assertRaisesRegex(RuntimeError, "area root index"):
                    context_cli.bootstrap_repository(repo, plan["owner_descriptor"], plan["index_seed"], host="codex")
            finally:
                context_cli._atomic_write = original

            _, rows = context_cli.parse_root_index((repo / context_cli.ROOT_INDEX).read_text(encoding="utf-8"))
            self.assertIn("decision", {row["area"] for row in rows})
            self.assertFalse((repo / "context/decision/decision.index.md").exists())
            resumed = context_cli.bootstrap_repository(repo, plan["owner_descriptor"], plan["index_seed"], host="codex")
            self.assertEqual(
                [("core_init", "noop"), ("area_register", "applied"), ("policy_install", "applied")],
                [(phase["phase"], phase["status"]) for phase in resumed["phases"]],
            )
            self.assertEqual("ready", resumed["doctor"]["repository_state"])

    def test_index_seed_preconditions_are_checked_before_any_write(self) -> None:
        with vault_dir() as temp:
            repo = Path(temp)
            core_init = context_cli.build_init_bundle(repo)
            unexpected = repo / "context/observation/observation.index.md"
            unexpected.parent.mkdir(parents=True)
            unexpected.write_text("out-of-band index bytes\n", encoding="utf-8")
            before = tree_digest(repo)

            with self.assertRaises(context_cli.ContextError) as caught:
                context_cli.apply_bundle(
                    repo,
                    core_init["bundle"],
                    core_init["approval_digest"],
                    approval_source="explicit_init",
                )
            self.assertEqual("precondition_changed", caught.exception.code)
            self.assertEqual(before, tree_digest(repo))
            self.assertFalse((repo / context_cli.ROOT_INDEX).exists())
            self.assertFalse((repo / "context/observation/retired").exists())

        with vault_dir() as temp:
            repo = Path(temp)
            initialize(repo)
            plan = decision_cli.build_init_plan()
            registration = context_cli.build_area_register_bundle(
                repo, plan["owner_descriptor"], plan["index_seed"]
            )
            target = repo / "context/decision/decision.index.md"
            target.parent.mkdir()
            target.write_text(
                plan["index_seed"].replace('owner: "context-decision"', 'owner: "other"'),
                encoding="utf-8",
            )
            before = tree_digest(repo)

            with self.assertRaises(context_cli.ContextError) as caught:
                context_cli.apply_bundle(
                    repo,
                    registration["bundle"],
                    registration["approval_digest"],
                    approval_source="explicit_init",
                )
            self.assertEqual("precondition_changed", caught.exception.code)
            self.assertEqual(before, tree_digest(repo))
            _, rows = context_cli.parse_root_index(
                (repo / context_cli.ROOT_INDEX).read_text(encoding="utf-8")
            )
            self.assertNotIn("decision", {row["area"] for row in rows})

        with vault_dir() as temp:
            repo = Path(temp)
            initialize(repo)
            plan = decision_cli.build_init_plan()
            target = repo / "context/decision/decision.index.md"
            target.parent.mkdir()
            target.write_text(plan["index_seed"], encoding="utf-8")
            seed_before = target.read_bytes()

            initialized = context_cli.bootstrap_repository(
                repo, plan["owner_descriptor"], plan["index_seed"], host="codex"
            )
            area_phase = next(phase for phase in initialized["phases"] if phase["phase"] == "area_register")
            self.assertEqual("applied", area_phase["status"])
            self.assertEqual([context_cli.ROOT_INDEX], area_phase["changed_paths"])
            self.assertEqual(seed_before, target.read_bytes())

        for populated, expected_code in ((False, "owner_descriptor_conflict"), (True, "partial_area_register")):
            with self.subTest(populated=populated), vault_dir() as temp:
                repo = Path(temp)
                initialize(repo)
                plan = decision_cli.build_init_plan()
                target = repo / "context/decision/decision.index.md"
                target.parent.mkdir()
                if populated:
                    target.write_text(plan["index_seed"], encoding="utf-8")
                    candidate = decision_candidate(
                        "cand_550e8400e29b41d4a716446655440010",
                        "인증 세션은 BFF가 소유한다.",
                    )
                    result = decision_cli.build_claim_result(
                        candidate,
                        decision_attestation(candidate),
                        identifier="ctx_550e8400e29b41d4a716446655440010",
                        created_at="2026-08-13T18:20:00+09:00",
                    )
                    draft = result["artifact_drafts"][0]
                    (repo / draft["path"]).write_text(draft["content"], encoding="utf-8")
                    target.write_text(
                        context_cli.render_area_index_from_repository(repo, "decision"),
                        encoding="utf-8",
                    )
                else:
                    target.write_text(
                        plan["index_seed"].replace(
                            'owner: "context-decision"', 'owner: "other"'
                        ),
                        encoding="utf-8",
                    )
                before = tree_digest(repo)

                with self.assertRaises(context_cli.ContextError) as caught:
                    context_cli.bootstrap_repository(
                        repo, plan["owner_descriptor"], plan["index_seed"], host="codex"
                    )
                self.assertEqual(expected_code, caught.exception.code)
                self.assertEqual(before, tree_digest(repo))
                _, rows = context_cli.parse_root_index(
                    (repo / context_cli.ROOT_INDEX).read_text(encoding="utf-8")
                )
                self.assertNotIn("decision", {row["area"] for row in rows})

    def test_area_register_rechecks_exact_empty_directory_before_write(self) -> None:
        with vault_dir() as temp:
            repo = Path(temp)
            initialize(repo)
            plan = decision_cli.build_init_plan()
            registration = context_cli.build_area_register_bundle(
                repo, plan["owner_descriptor"], plan["index_seed"]
            )
            area_root = repo / "context/decision"
            area_root.mkdir()
            (area_root / "rogue.md").write_text("unapproved bytes\n", encoding="utf-8")
            before = tree_digest(repo)

            with self.assertRaises(context_cli.ContextError) as caught:
                context_cli.apply_bundle(
                    repo,
                    registration["bundle"],
                    registration["approval_digest"],
                    approval_source="explicit_init",
                )
            self.assertEqual("precondition_changed", caught.exception.code)
            self.assertEqual(before, tree_digest(repo))
            self.assertFalse((area_root / "decision.index.md").exists())
            _, rows = context_cli.parse_root_index(
                (repo / context_cli.ROOT_INDEX).read_text(encoding="utf-8")
            )
            self.assertNotIn("decision", {row["area"] for row in rows})

    def test_existing_area_rejects_incompatible_descriptor_without_writes(self) -> None:
        with vault_dir() as temp:
            repo = Path(temp)
            initialize(repo)
            plan = decision_cli.build_init_plan()
            context_cli.bootstrap_repository(repo, plan["owner_descriptor"], plan["index_seed"], host="codex")
            incompatible = dict(plan["owner_descriptor"], artifact_schema="context-decision/v2")
            incompatible_seed = plan["index_seed"].replace(
                'artifact_schema: "context-decision/v1"',
                'artifact_schema: "context-decision/v2"',
            )
            before = tree_digest(repo)
            with self.assertRaises(context_cli.ContextError) as caught:
                context_cli.bootstrap_repository(repo, incompatible, incompatible_seed, host="codex")
            self.assertEqual("owner_descriptor_conflict", caught.exception.code)
            self.assertEqual("area_register", caught.exception.details["phases"][-1]["phase"])
            self.assertEqual(before, tree_digest(repo))

    def test_acceptance_05_rename_identity(self) -> None:
        with vault_dir() as temp:
            repo = Path(temp)
            initialize(repo)
            capture = context_cli.finalize_owner_result(repo, observation_owner_result())
            context_cli.apply_bundle(repo, capture["bundle"], capture["approval_digest"])
            rename = context_cli.build_rename_bundle(repo, "ctx_550e8400e29b41d4a716446655440000", "새 이름.md")
            context_cli.apply_bundle(repo, rename["bundle"], rename["approval_digest"])
            old_path = repo / "context/observation/Cookie-전달-관찰.md"
            new_path = repo / "context/observation/새 이름.md"
            self.assertFalse(old_path.exists())
            self.assertEqual("ctx_550e8400e29b41d4a716446655440000", context_cli.parse_document(new_path.read_text(encoding="utf-8")).frontmatter["id"])
            index = context_cli.parse_area_index((repo / "context/observation/observation.index.md").read_text(encoding="utf-8"))
            self.assertEqual("context/observation/새 이름.md", index.current[0]["path"])

    def test_generic_rename_and_discard_support_explicit_receipt_without_default_locator(self) -> None:
        with vault_dir() as temp, tempfile.TemporaryDirectory() as outer:
            repo = Path(temp)
            receipt_root = Path(outer) / "receipts"
            receipt_root.mkdir(mode=0o700)
            initialize(repo)
            capture = context_cli.finalize_owner_result(repo, observation_owner_result())
            context_cli.apply_bundle(repo, capture["bundle"], capture["approval_digest"])

            for command, receipt_name in (
                (("rename", "--id", "ctx_550e8400e29b41d4a716446655440000", "--filename", "새 이름.md"), "rename.json"),
                (("discard", "--id", "ctx_550e8400e29b41d4a716446655440000"), "discard.json"),
            ):
                receipt_path = receipt_root / receipt_name
                preview = subprocess.run(
                    [sys.executable, str(CLI_PATH), *command, "--receipt-file", str(receipt_path), "--json"],
                    cwd=repo,
                    text=True,
                    capture_output=True,
                )
                self.assertEqual(0, preview.returncode, preview.stdout + preview.stderr)
                result = json.loads(preview.stdout)["result"]
                self.assertEqual(
                    {"approval_preview", "approval_digest", "receipt_file", "applied", "state"},
                    set(result),
                )
                self.assertFalse(result["applied"])
                self.assertEqual("awaiting_approval", result["state"])
                applied = subprocess.run(
                    [
                        sys.executable, str(CLI_PATH), "transaction", "apply",
                        "--receipt-file", str(receipt_path),
                        "--approved-digest", result["approval_digest"],
                        "--json",
                    ],
                    cwd=repo,
                    text=True,
                    capture_output=True,
                )
                self.assertEqual(0, applied.returncode, applied.stdout + applied.stderr)
                self.assertFalse(receipt_path.exists())

            self.assertFalse((repo / "context/observation/새 이름.md").exists())

    def test_owner_result_becomes_final_bundle_and_only_core_applies(self) -> None:
        with vault_dir() as temp:
            repo = Path(temp)
            initialize(repo)
            owner_result = observation_owner_result()
            preview = context_cli.finalize_owner_result(repo, owner_result)
            self.assertEqual("context-mutation-bundle/v1", preview["bundle"]["schema"])
            plan = preview["bundle"]["approval_material"]["plan"]
            self.assertEqual("owner_result", plan["source_type"])
            self.assertEqual({"file_create", "index_rebuild"}, {operation["op"] for operation in plan["operations"]})
            self.assertFalse((repo / owner_result["artifact_drafts"][0]["path"]).exists())
            applied = context_cli.apply_bundle(repo, preview["bundle"], preview["approval_digest"])
            self.assertTrue(applied["applied"])
            self.assertTrue((repo / owner_result["artifact_drafts"][0]["path"]).exists())

    def test_acceptance_23_preview(self) -> None:
        with vault_dir() as temp:
            repo = Path(temp)
            initialize(repo)
            owner_result = observation_owner_result()
            before = tree_digest(repo)
            preview = context_cli.finalize_owner_result(repo, owner_result)
            self.assertEqual(before, tree_digest(repo))
            approval = preview["approval_preview"]
            self.assertEqual(owner_result["artifact_drafts"][0]["content"], approval["artifacts"][0]["content"])
            self.assertEqual(owner_result["artifact_drafts"][0]["path"], approval["artifacts"][0]["path"])
            self.assertEqual(owner_result["effects"], approval["effects"])
            self.assertEqual(preview["approval_digest"], preview["bundle"]["approval_digest"])
            frozen = copy.deepcopy(preview["bundle"])
            context_cli.apply_bundle(repo, frozen, preview["approval_digest"])
            self.assertEqual(frozen, preview["bundle"], "apply must not regenerate semantic material")

    def test_approval_digest_material_and_hidden_operations_fail_closed(self) -> None:
        with vault_dir() as temp:
            repo = Path(temp)
            initialize(repo)
            preview = context_cli.finalize_owner_result(repo, observation_owner_result())
            before = tree_digest(repo)
            with self.assertRaises(context_cli.ContextError) as digest_error:
                context_cli.apply_bundle(repo, preview["bundle"], "sha256:" + "0" * 64)
            self.assertEqual("approval_digest_mismatch", digest_error.exception.code)

            tampered = copy.deepcopy(preview["bundle"])
            next(material for material in tampered["materials"] if material["path"] is not None)["content"] += "tamper\n"
            with self.assertRaises(context_cli.ContextError) as material_error:
                context_cli.apply_bundle(repo, tampered, preview["approval_digest"])
            self.assertEqual("material_digest_mismatch", material_error.exception.code)

            hidden = copy.deepcopy(preview["bundle"])
            hidden["approval_material"]["plan"]["operations"].insert(0, copy.deepcopy(hidden["approval_material"]["plan"]["operations"][0]))
            hidden["approval_digest"] = context_cli.canonical_digest(hidden["approval_material"])
            with self.assertRaises(context_cli.ContextError) as hidden_error:
                context_cli.apply_bundle(repo, hidden, hidden["approval_digest"])
            self.assertEqual("plan_preview_mismatch", hidden_error.exception.code)
            self.assertEqual(before, tree_digest(repo))

    def test_bundle_is_bound_to_exact_vault_identity_but_not_unrelated_content(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            first = root / "first"
            second = root / "second"
            for repo in (first, second):
                repo.mkdir(parents=True, exist_ok=True)
                initialize(repo)
            preview = context_cli.finalize_owner_result(first, observation_owner_result())
            identity = preview["bundle"]["approval_material"]["vault_identity"]
            self.assertEqual({"schema", "root"}, set(identity))
            self.assertEqual("context-vault-identity/v1", identity["schema"])
            self.assertEqual({"path", "device", "inode"}, set(identity["root"]))
            self.assertTrue(identity["root"]["device"].isdigit())
            self.assertTrue(identity["root"]["inode"].isdigit())

            second_before = tree_digest(second)
            with self.assertRaises(context_cli.ContextError) as replay:
                context_cli.apply_bundle(second, preview["bundle"], preview["approval_digest"])
            self.assertEqual("vault_identity_mismatch", replay.exception.code)
            self.assertEqual(second_before, tree_digest(second))

            (first / "unrelated.txt").write_text("not an approved precondition\n", encoding="utf-8")
            applied = context_cli.apply_bundle(first, preview["bundle"], preview["approval_digest"])
            self.assertTrue(applied["applied"])
            repeated = context_cli.apply_bundle(first, preview["bundle"], preview["approval_digest"])
            self.assertEqual([], repeated["changed_paths"])

    def test_vault_identity_shape_tamper_and_same_path_recreation_fail_before_write(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            repo = root / "repository"
            repo.mkdir(parents=True, exist_ok=True)
            initialize(repo)
            preview = context_cli.finalize_owner_result(repo, observation_owner_result())

            for label, mutate in (
                ("missing", lambda identity: identity.pop("root")),
                ("extra", lambda identity: identity.update({"extra": "forged"})),
            ):
                with self.subTest(label=label):
                    forged = copy.deepcopy(preview["bundle"])
                    mutate(forged["approval_material"]["vault_identity"])
                    forged["approval_digest"] = context_cli.canonical_digest(forged["approval_material"])
                    before = tree_digest(repo)
                    with self.assertRaises(context_cli.ContextError) as malformed:
                        context_cli.apply_bundle(repo, forged, forged["approval_digest"])
                    self.assertEqual("vault_identity_invalid", malformed.exception.code)
                    self.assertEqual(before, tree_digest(repo))

            repo.rename(root / "previous-vault")
            repo.mkdir(parents=True, exist_ok=True)
            initialize(repo)
            before = tree_digest(repo)
            with self.assertRaises(context_cli.ContextError) as recreated:
                context_cli.apply_bundle(repo, preview["bundle"], preview["approval_digest"])
            self.assertEqual("vault_identity_mismatch", recreated.exception.code)
            self.assertEqual(before, tree_digest(repo))

    def test_legacy_approval_requires_a_fresh_preview_without_writes(self) -> None:
        with vault_dir() as temp:
            repo = Path(temp)
            initialize(repo)
            preview = context_cli.finalize_owner_result(repo, observation_owner_result())
            legacy = copy.deepcopy(preview["bundle"])
            root = legacy["approval_material"].pop("vault_identity")["root"]
            legacy["approval_material"]["repository_identity"] = {
                "schema": "context-repository-identity/v1",
                "worktree": root,
                "git_common_dir": root,
            }
            legacy["approval_digest"] = context_cli.canonical_digest(legacy["approval_material"])
            before = tree_digest(repo)
            with self.assertRaises(context_cli.ContextError) as rejected:
                context_cli.apply_bundle(repo, legacy, legacy["approval_digest"])
            self.assertEqual("vault_identity_invalid", rejected.exception.code)
            self.assertIn("new approval preview", str(rejected.exception))
            self.assertEqual(before, tree_digest(repo))

    def test_copied_vault_cannot_replay_approved_bundle(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source = root / "source"
            copied = root / "copied"
            source.mkdir(parents=True, exist_ok=True)
            initialize(source)
            shutil.copytree(source, copied)
            preview = context_cli.finalize_owner_result(source, observation_owner_result())
            before = tree_digest(copied)
            with self.assertRaises(context_cli.ContextError) as replay:
                context_cli.apply_bundle(copied, preview["bundle"], preview["approval_digest"])
            self.assertEqual("vault_identity_mismatch", replay.exception.code)
            self.assertEqual(before, tree_digest(copied))

    def test_core_control_transitions_reject_forged_file_operations(self) -> None:
        def forge_file_create(bundle: dict) -> dict:
            forged = copy.deepcopy(bundle)
            effect_id = "effect_escape"
            material_id = "material_escape"
            content = "forged core_control payload"
            path = "outside/nested.txt"
            forged["materials"].append({"material_id": material_id, "path": path, "content": content})
            preview = forged["approval_material"]["preview"]
            preview["artifacts"].append({"effect_id": effect_id, "path": path, "content": content})
            preview["effects"].append(
                {"effect_id": effect_id, "action": "create", "area": "observation", "id": "ctx_550e8400e29b41d4a716446655440099", "state": "current"}
            )
            operation = {
                "op": "file_create",
                "effect_id": effect_id,
                "role": "artifact",
                "area": "observation",
                "path": path,
                "before_sha256": None,
                "after_sha256": context_cli.sha256_bytes(context_cli.file_bytes(content)),
                "material": material_id,
            }
            operations = forged["approval_material"]["plan"]["operations"]
            index_operation = next((item for item in operations if item["op"] == "index_rebuild"), None)
            if index_operation is not None:
                index_operation["derived_from"].append(effect_id)
                operations.insert(0, operation)
            else:
                operations.append(operation)
            forged["approval_digest"] = context_cli.canonical_digest(forged["approval_material"])
            return forged

        def area_register(repo: Path) -> dict:
            descriptor = {
                "schema": "context-owner-descriptor/v1",
                "owner": "context-decision",
                "kind": "decision",
                "artifact_schema": "context-decision/v1",
                "authority": "authoritative",
            }
            seed = context_cli._area_seed(
                "decision",
                "context-decision",
                "context-decision/v1",
                "authoritative",
                "결정·취지·반려대안과 현재 유효성을 관리한다.",
                projection_fields=("scope", "decision_key", "revisit_on"),
            )
            return context_cli.build_area_register_bundle(repo, descriptor, seed)

        cases = {
            "core_init": (False, context_cli.build_init_bundle),
            "area_register": (True, area_register),
            "policy_install": (True, lambda repo: context_cli.build_policy_bundle(repo, "CLAUDE.md")),
        }
        for transition, (initialized, build) in cases.items():
            with self.subTest(transition=transition), vault_dir() as temp:
                repo = Path(temp)
                if initialized:
                    initialize(repo)
                legitimate = build(repo)["bundle"]
                before = tree_digest(repo)
                variants = {"file-operation": forge_file_create(legitimate)}
                descriptor = copy.deepcopy(legitimate)
                descriptor["approval_material"]["plan"]["owner_descriptor"]["forged"] = True
                variants["descriptor"] = descriptor
                control = copy.deepcopy(legitimate)
                control["approval_material"]["plan"]["control_input"]["forged"] = True
                variants["control-input"] = control
                effect = copy.deepcopy(legitimate)
                effect["approval_material"]["preview"]["effects"][0]["forged"] = True
                variants["effect"] = effect
                binding = copy.deepcopy(legitimate)
                binding_control = binding["approval_material"]["plan"]["control_input"]
                if transition in {"core_init", "area_register"}:
                    first = next(iter(binding_control["seed_digests"]))
                    binding_control["seed_digests"][first] = "sha256:" + "0" * 64
                elif transition == "policy_install":
                    binding_control["outside_bytes_sha256"] = "sha256:" + "0" * 64
                variants["control-binding"] = binding
                coherent = copy.deepcopy(legitimate)
                coherent_plan = coherent["approval_material"]["plan"]
                coherent_operation = coherent_plan["operations"][-1]
                if transition == "core_init":
                    material = next(item for item in coherent["materials"] if item["path"] == context_cli.ROOT_INDEX)
                    material["content"] = material["content"].replace("# Context", "# Forged Context", 1)
                    digest = context_cli.sha256_bytes(context_cli.file_bytes(material["content"]))
                    coherent_plan["control_input"]["seed_digests"][context_cli.ROOT_INDEX] = digest
                    coherent_operation["after_sha256"][context_cli.ROOT_INDEX] = digest
                elif transition == "area_register":
                    material = next(item for item in coherent["materials"] if item["path"] == context_cli.ROOT_INDEX)
                    material["content"] = material["content"].replace("# Context", "# Forged Context", 1)
                    coherent_operation["after_sha256"][context_cli.ROOT_INDEX] = context_cli.sha256_bytes(
                        context_cli.file_bytes(material["content"])
                    )
                elif transition == "policy_install":
                    material = coherent["materials"][0]
                    material["content"] = "forged outside\n\n" + material["content"]
                    coherent_operation["after_sha256"] = context_cli.sha256_bytes(material["content"].encode("utf-8"))
                    coherent["approval_material"]["preview"]["artifacts"][0]["content"] = material["content"]
                variants["coherent-forgery"] = coherent
                for label, forged in variants.items():
                    with self.subTest(transition=transition, forgery=label):
                        forged["approval_digest"] = context_cli.canonical_digest(forged["approval_material"])
                        with self.assertRaises(context_cli.ContextError) as caught:
                            context_cli.apply_bundle(repo, forged, forged["approval_digest"])
                        self.assertEqual("plan_preview_mismatch", caught.exception.code)
                        self.assertEqual(before, tree_digest(repo))
                        self.assertFalse((repo / "outside/nested.txt").exists())

    def test_acceptance_24_digest(self) -> None:
        with vault_dir() as temp:
            repo = Path(temp)
            initialize(repo)
            preview = context_cli.finalize_owner_result(repo, observation_owner_result())
            before = tree_digest(repo)
            variants = []
            changed_preview = copy.deepcopy(preview["bundle"])
            changed_preview["approval_material"]["preview"]["effects"][0]["state"] = "history"
            variants.append(changed_preview)
            changed_plan = copy.deepcopy(preview["bundle"])
            changed_plan["approval_material"]["plan"]["transition"] = "autonomous_maintenance"
            variants.append(changed_plan)
            changed_owner = copy.deepcopy(preview["bundle"])
            owner_material = next(item for item in changed_owner["materials"] if item["path"] is None)
            owner_material["content"] += " "
            variants.append(changed_owner)
            for bundle in variants:
                with self.subTest(bundle=bundle), self.assertRaises(context_cli.ContextError):
                    context_cli.apply_bundle(repo, bundle, preview["approval_digest"])
                self.assertEqual(before, tree_digest(repo))
            with self.assertRaises(context_cli.ContextError) as caught:
                context_cli.apply_bundle(repo, preview["bundle"], preview["approval_digest"], approval_source="autonomous")
            self.assertEqual("approval_required", caught.exception.code)
            with self.assertRaises(context_cli.ContextError) as caught:
                context_cli.apply_bundle(repo, preview["bundle"], preview["approval_digest"], approval_source="explicit_init")
            self.assertEqual("approval_required", caught.exception.code)
            self.assertEqual(before, tree_digest(repo))

    def test_acceptance_38_crash_resume(self) -> None:
        with vault_dir() as temp:
            repo = Path(temp)
            initialize(repo)
            context_cli.apply_bundle(
                repo,
                (capture := context_cli.finalize_owner_result(repo, observation_owner_result()))["bundle"],
                capture["approval_digest"],
            )
            preview = context_cli.build_observation_invalidate_bundle(
                repo,
                "ctx_550e8400e29b41d4a716446655440000",
                "재현 전제가 사라짐",
                now="2026-08-14T09:00:00+09:00",
            )
            plan = preview["bundle"]["approval_material"]["plan"]
            move = next(operation for operation in plan["operations"] if operation["op"] == "file_move")
            materials = {item["material_id"]: item for item in preview["bundle"]["materials"]}
            destination = repo / move["to_path"]
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_bytes(context_cli.file_bytes(materials[move["material"]]["content"]))
            self.assertTrue((repo / move["from_path"]).exists())
            result = context_cli.apply_bundle(repo, preview["bundle"], preview["approval_digest"])
            self.assertTrue(result["applied"])
            self.assertFalse((repo / move["from_path"]).exists())
            self.assertTrue(destination.exists())
            repeated = context_cli.apply_bundle(repo, preview["bundle"], preview["approval_digest"])
            self.assertEqual([], repeated["changed_paths"])

    def test_decision_supersede_preview_matches_applied_repository_index(self) -> None:
        with vault_dir() as temp:
            repo = Path(temp)
            initialize(repo)
            decision_init = decision_cli.build_init_plan()
            registration = context_cli.build_area_register_bundle(
                repo, decision_init["owner_descriptor"], decision_init["index_seed"]
            )
            context_cli.apply_bundle(repo, registration["bundle"], registration["approval_digest"])

            predecessor_candidate = decision_candidate(
                "cand_550e8400e29b41d4a716446655440000",
                "인증 세션은 BFF가 소유한다.",
            )
            predecessor = decision_cli.build_claim_result(
                predecessor_candidate,
                decision_attestation(predecessor_candidate),
                identifier="ctx_550e8400e29b41d4a716446655440000",
                created_at="2026-08-13T18:20:00+09:00",
            )
            predecessor_validation = decision_cli.validate_batch(repo, predecessor)
            capture = context_cli.finalize_owner_result(repo, predecessor, predecessor_validation)
            context_cli.apply_bundle(repo, capture["bundle"], capture["approval_digest"])

            successor_candidate = decision_candidate(
                "cand_550e8400e29b41d4a716446655440001",
                "인증 세션은 auth service가 소유한다.",
            )
            successor = decision_cli.build_supersede_result(
                repo,
                "ctx_550e8400e29b41d4a716446655440000",
                successor_candidate,
                decision_attestation(successor_candidate),
                identifier="ctx_123e4567e89b42d3a456426614174001",
                retired_at="2026-08-14T09:00:00+09:00",
            )
            validation = decision_cli.validate_batch(repo, successor)
            preview = context_cli.finalize_owner_result(repo, successor, validation)
            index_operation = next(
                operation
                for operation in preview["bundle"]["approval_material"]["plan"]["operations"]
                if operation["op"] == "index_rebuild"
            )
            index_path = "context/decision/decision.index.md"

            context_cli.apply_bundle(repo, preview["bundle"], preview["approval_digest"])

            applied = (repo / index_path).read_text(encoding="utf-8")
            regenerated = context_cli.render_area_index_from_repository(repo, "decision")
            self.assertEqual(
                index_operation["after_sha256"][index_path],
                context_cli.sha256_bytes(context_cli.file_bytes(applied)),
            )
            self.assertEqual(applied, regenerated)

    def test_owner_area_allowlist_and_seed_requirements_fail_closed(self) -> None:
        with vault_dir() as temp:
            repo = Path(temp)
            initialize(repo)
            invalid = observation_owner_result()
            invalid["owner"] = "context-decision"
            with self.assertRaises(context_cli.ContextError) as owner_error:
                context_cli.finalize_owner_result(repo, invalid)
            self.assertEqual("area_owner_mismatch", owner_error.exception.code)
            descriptor = {"schema": "context-owner-descriptor/v1", "owner": "context-decision", "kind": "decision", "artifact_schema": "context-decision/v1", "authority": "authoritative"}
            with self.assertRaises(context_cli.ContextError) as seed_error:
                context_cli.build_area_register_bundle(repo, descriptor, None)
            self.assertEqual("index_seed_required", seed_error.exception.code)

    def test_exact_precondition_blocks_changed_target(self) -> None:
        with vault_dir() as temp:
            repo = Path(temp)
            initialize(repo)
            capture = context_cli.finalize_owner_result(repo, observation_owner_result())
            target = repo / "context/observation/Cookie-전달-관찰.md"
            target.write_text("out-of-band\n", encoding="utf-8")
            before = tree_digest(repo)
            with self.assertRaises(context_cli.ContextError) as caught:
                context_cli.apply_bundle(repo, capture["bundle"], capture["approval_digest"])
            self.assertEqual("precondition_changed", caught.exception.code)
            self.assertEqual(before, tree_digest(repo))

    def test_index_fix_is_immediate_and_document_authoritative(self) -> None:
        with vault_dir() as temp:
            repo = Path(temp)
            initialize(repo)
            artifact = repo / "context/observation/out-of-band.md"
            artifact.write_text(
                context_cli.render_document(
                    {
                        "schema": "context-observation/v1",
                        "id": "ctx_550e8400e29b41d4a716446655440001",
                        "title": "Out-of-band observation",
                        "summary": "Index repair must derive this row from the document.",
                        "created_at": "2026-08-13T18:21:00+09:00",
                        "captured_from": "workspace",
                    },
                    {"관찰": "Out-of-band observation", "근거": "integration fixture"},
                ),
                encoding="utf-8",
            )
            artifact_before = artifact.read_bytes()
            repair = context_cli.repair_derived_indexes(repo)
            self.assertTrue(repair["applied"])
            self.assertTrue(context_cli.refresh_repository(repo)["ok"])
            self.assertEqual(artifact_before, artifact.read_bytes())
            area_index = context_cli.parse_area_index(
                (repo / "context/observation/observation.index.md").read_text(encoding="utf-8")
            )
            self.assertEqual(["ctx_550e8400e29b41d4a716446655440001"], [row["id"] for row in area_index.current])

    def test_schema_runs_without_vault_and_supports_root_override(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            completed = subprocess.run(["python3", str(CLI_PATH), "schema", "--json"], cwd=temp, text=True, capture_output=True)
            self.assertEqual(0, completed.returncode, completed.stderr)
            result = json.loads(completed.stdout)
            self.assertTrue(result["ok"])
            self.assertEqual("context-common/v2", result["result"]["protocol"])
            self.assertTrue(result["result"]["root_override"])
            self.assertIn("filesystem-vault/v1", result["result"]["features"])
            self.assertNotIn("ambiguous", result["result"]["exit_codes"])


if __name__ == "__main__":
    unittest.main()
