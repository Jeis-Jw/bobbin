#!/usr/bin/env python3
from __future__ import annotations

import copy
import hashlib
import importlib.util
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock


PLUGIN = next(p for p in Path(__file__).resolve().parents if (p / "pytest.ini").is_file()) / "plugins/bobbin"
CLI_PATH = PLUGIN / "skills/context/scripts/context_cli.py"
SPEC = importlib.util.spec_from_file_location("context_cli_semantic_transport", CLI_PATH)
assert SPEC and SPEC.loader
context_cli = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = context_cli
SPEC.loader.exec_module(context_cli)


def run_cli(
    repo: Path,
    *arguments: str,
    environment: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    command_environment = {**os.environ, "PYTHONDONTWRITEBYTECODE": "1"}
    if environment:
        command_environment.update(environment)
    return subprocess.run(
        [sys.executable, str(CLI_PATH), *arguments],
        cwd=repo,
        env=command_environment,
        text=True,
        capture_output=True,
    )


def write_json(path: Path, value: object) -> Path:
    path.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")
    return path


def tree_digest(root: Path) -> str:
    digest = hashlib.sha256()
    for path in sorted(item for item in root.rglob("*") if item.is_file()):
        digest.update(path.relative_to(root).as_posix().encode("utf-8"))
        digest.update(path.read_bytes())
    return digest.hexdigest()


def initialize(repo: Path) -> None:
    preview = context_cli.build_init_bundle(repo)
    context_cli.apply_bundle(repo, preview["bundle"], preview["approval_digest"])


def candidate(kind: str, *, mismatch: bool = False) -> dict:
    if kind == "observation":
        primary = "Safari에서 third-party cookie 전달이 차단된다."
        owner_input = {"observation": primary, "evidence": ["integration fixture"]}
    else:
        primary = "인증 handoff 구현과 회귀 검증이 진행 중이다."
        owner_input = {
            "current_context": primary,
            "open_items": ["public route 회귀를 추가한다."],
            "next_steps": ["focused suite를 실행한다."],
        }
    return {
        "schema": "context-capture-candidate/v1",
        "candidate_id": "cand_550e8400e29b41d4a71644665544000" + ("1" if kind == "observation" else "2"),
        "title": "관찰 transport" if kind == "observation" else "handoff transport",
        "claim": "다른 transport claim이다." if mismatch else primary,
        "summary": "built-in semantic transport binding을 검증한다.",
        "captured_from": "workspace",
        "requested_kind": kind,
        "specialized_kinds": [kind],
        "fallback_kind": None,
        "owner_inputs": {kind: owner_input},
    }


def attestation(value: dict, kind: str) -> dict:
    pointers = {
        "observation": (
            ("reusable_observation", "/owner_inputs/observation/observation"),
            ("evidence_present", "/owner_inputs/observation/evidence/0"),
        ),
        "snapshot": (
            ("handoff_requested", "/owner_inputs/snapshot/current_context"),
            ("unfinished_context_present", "/owner_inputs/snapshot/open_items/0"),
        ),
    }
    digest = context_cli.canonical_digest(value)
    return {
        "schema": "context-semantic-attestation/v1",
        "operation": "claim",
        "input_schema": value["schema"],
        "input_digest": digest,
        "assertions": [
            {"name": name, "value": True, "evidence_pointers": [pointer]}
            for name, pointer in pointers[kind]
        ],
    }


def forged_owner_result(kind: str) -> tuple[dict, dict]:
    matching = candidate(kind)
    result = context_cli.draft_owner_result(
        matching,
        attestation(matching, kind),
        now="2026-08-22T12:00:00+09:00",
    )
    forged = copy.deepcopy(result)
    embedded = forged["semantic_inputs"][0]
    embedded["value"]["claim"] = "검증된 owner primary와 다른 재계산된 claim이다."
    embedded["input_digest"] = context_cli.canonical_digest(embedded["value"])
    forged["semantic_attestations"][0]["input_digest"] = embedded["input_digest"]
    return result, forged


def forged_bundle(bundle: dict) -> dict:
    forged = copy.deepcopy(bundle)
    plan = forged["approval_material"]["plan"]
    material = next(item for item in forged["materials"] if item.get("material_id") == plan["owner_result_material"])
    owner_result = json.loads(material["content"])
    embedded = owner_result["semantic_inputs"][0]
    embedded["value"]["claim"] = "승인 bundle 안에서 재계산된 다른 claim이다."
    embedded["input_digest"] = context_cli.canonical_digest(embedded["value"])
    owner_result["semantic_attestations"][0]["input_digest"] = embedded["input_digest"]
    material["content"] = context_cli.canonical_json(owner_result)
    plan["owner_result_digest"] = context_cli.sha256_bytes(material["content"].encode("utf-8"))
    forged["approval_digest"] = context_cli.canonical_digest(forged["approval_material"])
    return forged


def error_code(completed: subprocess.CompletedProcess[str]) -> str:
    if completed.returncode == 0:
        raise AssertionError(completed.stdout + completed.stderr)
    return json.loads(completed.stdout)["error"]["code"]


def preview_observation_receipt(
    repo: Path,
    private_temp: Path,
    *,
    receipt_file: Path | None = None,
) -> tuple[dict, Path]:
    arguments = [
        "observation", "capture",
        "--title", "receipt transport",
        "--summary", "core와 exact plan bundle을 함께 결박한다.",
        "--captured-from", "workspace",
        "--attest-reusable-observation",
        "--attest-evidence-present",
        "--sec-observation", "receipt는 저장소 밖에서 동결된다.",
        "--sec-evidence", "workflow regression fixture",
    ]
    if receipt_file is not None:
        arguments.extend(("--receipt-file", str(receipt_file)))
    completed = run_cli(
        repo,
        *arguments,
        "--json",
        environment={"TMPDIR": str(private_temp)},
    )
    if completed.returncode:
        raise AssertionError(completed.stdout + completed.stderr)
    result = json.loads(completed.stdout)["result"]
    return result, Path(result["receipt_file"])


class SemanticTransportIntegrityTests(unittest.TestCase):
    def test_receipt_external_digest_blocks_fully_rehashed_core_and_bundle_swaps(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            repo = root / "repository"
            private_temp = root / "private-temp"
            repo.mkdir()
            private_temp.mkdir(mode=0o700)
            repo.mkdir(parents=True, exist_ok=True)
            initialize(repo)
            result, receipt_path = preview_observation_receipt(repo, private_temp)
            original = json.loads(receipt_path.read_text(encoding="utf-8"))
            before = tree_digest(repo)

            fake_core = root / "fake-core.py"
            fake_core.write_text("# forged core\n", encoding="utf-8")
            forged_core = copy.deepcopy(original)
            forged_core["core"] = {
                "path": str(fake_core.resolve()),
                "sha256": context_cli.sha256_bytes(fake_core.read_bytes()),
            }
            forged_core_body = dict(forged_core)
            forged_core_body.pop("receipt_digest")
            forged_core["receipt_digest"] = context_cli.canonical_digest(forged_core_body)

            forged_bundle = copy.deepcopy(original)
            forged_bundle["plan_bundle"]["approval_material"]["preview"]["effects"][0]["action"] = "forged"
            forged_bundle["plan_bundle"]["approval_digest"] = context_cli.canonical_digest(
                forged_bundle["plan_bundle"]["approval_material"]
            )
            forged_bundle_body = dict(forged_bundle)
            forged_bundle_body.pop("receipt_digest")
            forged_bundle["receipt_digest"] = context_cli.canonical_digest(forged_bundle_body)

            for label, forged in (("core", forged_core), ("bundle", forged_bundle)):
                with self.subTest(label=label):
                    receipt_path.write_text(context_cli.canonical_json(forged), encoding="utf-8")
                    receipt_path.chmod(0o600)
                    applied = run_cli(
                        repo,
                        "transaction", "apply",
                        "--receipt-file", str(receipt_path),
                        "--approved-digest", result["approval_digest"],
                        "--json",
                    )
                    self.assertEqual("workflow_approval_digest_mismatch", error_code(applied))
                    self.assertEqual(before, tree_digest(repo))

            receipt_path.write_text(context_cli.canonical_json(original), encoding="utf-8")
            receipt_path.chmod(0o600)

    def test_receipt_rejects_shape_and_plan_id_tamper_before_write(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            repo = root / "repository"
            private_temp = root / "private-temp"
            repo.mkdir()
            private_temp.mkdir(mode=0o700)
            repo.mkdir(parents=True, exist_ok=True)
            initialize(repo)
            result, receipt_path = preview_observation_receipt(repo, private_temp)
            original = json.loads(receipt_path.read_text(encoding="utf-8"))
            before = tree_digest(repo)
            cases: list[tuple[str, dict]] = []
            extra = copy.deepcopy(original)
            extra["unexpected"] = True
            cases.append(("extra", extra))
            missing = copy.deepcopy(original)
            missing.pop("created_at")
            cases.append(("missing", missing))
            plan_id = copy.deepcopy(original)
            plan_id["plan_id"] = "plan_00000000000040008000000000000000"
            plan_id_body = dict(plan_id)
            plan_id_body.pop("receipt_digest")
            plan_id["receipt_digest"] = context_cli.canonical_digest(plan_id_body)
            cases.append(("plan-id", plan_id))

            for label, forged in cases:
                with self.subTest(label=label):
                    receipt_path.write_text(context_cli.canonical_json(forged), encoding="utf-8")
                    receipt_path.chmod(0o600)
                    completed = run_cli(
                        repo,
                        "transaction", "apply",
                        "--receipt-file", str(receipt_path),
                        "--approved-digest", result["approval_digest"],
                        "--json",
                    )
                    self.assertNotEqual(0, completed.returncode)
                    self.assertEqual(before, tree_digest(repo))

    def test_receipt_path_tristate_validates_default_before_mkdir_and_explicit_does_not_touch_it(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            repo = root / "repository"
            repo.mkdir()
            initialize(repo)
            unsafe_temp = repo / "private-temp"
            unsafe_temp.mkdir(mode=0o700)
            explicit_root = root / "explicit"
            explicit_root.mkdir(mode=0o700)
            explicit_receipt = explicit_root / "receipt.json"
            before = tree_digest(repo)

            explicit_result, actual_path = preview_observation_receipt(
                repo,
                unsafe_temp,
                receipt_file=explicit_receipt,
            )
            self.assertEqual(explicit_receipt.resolve(), actual_path)
            self.assertEqual(before, tree_digest(repo))
            self.assertFalse((unsafe_temp / "context-core").exists())
            self.assertTrue(explicit_result["approval_digest"].startswith("sha256:"))

            automatic = run_cli(
                repo,
                "observation", "capture",
                "--title", "unsafe default",
                "--summary", "default receipt path를 먼저 검증한다.",
                "--captured-from", "workspace",
                "--attest-reusable-observation",
                "--attest-evidence-present",
                "--sec-observation", "repository 내부 TMPDIR은 거부한다.",
                "--sec-evidence", "no-touch regression",
                "--json",
                environment={"TMPDIR": str(unsafe_temp)},
            )
            self.assertNotEqual(0, automatic.returncode)
            self.assertEqual(before, tree_digest(repo))
            self.assertFalse((unsafe_temp / "context-core").exists())

            proof = root / "attestation.json"
            proof.write_text(json.dumps({
                "assertions": [
                    {"name": "reusable_observation", "value": True, "evidence_pointers": ["/owner_inputs/observation/observation"]},
                    {"name": "evidence_present", "value": True, "evidence_pointers": ["/owner_inputs/observation/evidence/0"]},
                ],
            }), encoding="utf-8")
            low_level = run_cli(
                repo,
                "observation", "capture",
                "--title", "legacy bundle",
                "--summary", "receipt option이 없으면 기존 bundle을 반환한다.",
                "--captured-from", "workspace",
                "--attestation", f"@{proof}",
                "--sec-observation", "legacy low-level bundle은 유지된다.",
                "--sec-evidence", "tri-state contract",
                "--json",
                environment={"TMPDIR": str(unsafe_temp)},
            )
            self.assertEqual(0, low_level.returncode, low_level.stdout + low_level.stderr)
            self.assertIn("bundle", json.loads(low_level.stdout)["result"])
            self.assertFalse((unsafe_temp / "context-core").exists())

    def test_cleanup_failure_warns_once_and_retry_is_rejected_without_writes(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            repo = root / "repository"
            private_temp = root / "private-temp"
            repo.mkdir()
            private_temp.mkdir(mode=0o700)
            repo.mkdir(parents=True, exist_ok=True)
            initialize(repo)
            result, receipt_path = preview_observation_receipt(repo, private_temp)

            with mock.patch.object(context_cli, "_unlink_receipt", side_effect=OSError("fixture")):
                applied = context_cli.apply_receipt(repo, receipt_path, result["approval_digest"])
            self.assertTrue(applied["applied"])
            self.assertEqual(["receipt_cleanup_failed"], applied["warnings"])
            self.assertTrue(receipt_path.exists())
            after = tree_digest(repo)

            with self.assertRaises(context_cli.ContextError) as caught:
                context_cli.apply_receipt(repo, receipt_path, result["approval_digest"])
            self.assertEqual("receipt_already_applied", caught.exception.code)
            self.assertEqual(after, tree_digest(repo))

    def test_receipt_symlink_traversal_and_mode_checks_fail_closed(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            repo = root / "repository"
            private_temp = root / "private-temp"
            repo.mkdir()
            private_temp.mkdir(mode=0o700)
            repo.mkdir(parents=True, exist_ok=True)
            initialize(repo)
            before = tree_digest(repo)

            traversal = root / "explicit" / ".." / "traversal.json"
            (root / "explicit").mkdir(mode=0o700)
            traversal_attempt = run_cli(
                repo,
                "observation", "capture",
                "--title", "traversal",
                "--summary", "non-canonical receipt path를 거부한다.",
                "--captured-from", "workspace",
                "--attest-reusable-observation",
                "--attest-evidence-present",
                "--sec-observation", "path traversal은 허용하지 않는다.",
                "--sec-evidence", "path fixture",
                "--receipt-file", str(traversal),
                "--json",
            )
            self.assertNotEqual(0, traversal_attempt.returncode)
            self.assertFalse((root / "traversal.json").exists())
            self.assertEqual(before, tree_digest(repo))

            default_directory = private_temp / "context-core"
            symlink_target = root / "symlink-target"
            symlink_target.mkdir(mode=0o700)
            default_directory.symlink_to(symlink_target, target_is_directory=True)
            default_attempt = run_cli(
                repo,
                "observation", "capture",
                "--title", "default symlink",
                "--summary", "symlink default directory를 거부한다.",
                "--captured-from", "workspace",
                "--attest-reusable-observation",
                "--attest-evidence-present",
                "--sec-observation", "default directory는 실제 private directory여야 한다.",
                "--sec-evidence", "symlink fixture",
                "--json",
                environment={"TMPDIR": str(private_temp)},
            )
            self.assertNotEqual(0, default_attempt.returncode)
            self.assertEqual([], list(symlink_target.iterdir()))
            default_directory.unlink()

            result, receipt_path = preview_observation_receipt(repo, private_temp)
            receipt_path.chmod(0o644)
            mode_attempt = run_cli(
                repo,
                "transaction", "apply",
                "--receipt-file", str(receipt_path),
                "--approved-digest", result["approval_digest"],
                "--json",
            )
            self.assertNotEqual(0, mode_attempt.returncode)
            self.assertEqual(before, tree_digest(repo))
            receipt_path.chmod(0o600)
            receipt_path.unlink()
            target = root / "replacement.json"
            target.write_text("{}", encoding="utf-8")
            receipt_path.symlink_to(target)
            symlink_attempt = run_cli(
                repo,
                "transaction", "apply",
                "--receipt-file", str(receipt_path),
                "--approved-digest", result["approval_digest"],
                "--json",
            )
            self.assertNotEqual(0, symlink_attempt.returncode)
            self.assertEqual("{}", target.read_text(encoding="utf-8"))
            self.assertEqual(before, tree_digest(repo))

    def test_public_draft_and_batch_route_reject_each_builtin_primary_mismatch(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            repo = root / "repository"
            repo.mkdir()
            capabilities = write_json(root / "capabilities.json", context_cli.capabilities_result())
            results = write_json(root / "results.json", [])
            before = tree_digest(repo)

            for kind in ("observation", "snapshot"):
                with self.subTest(kind=kind):
                    value = candidate(kind, mismatch=True)
                    proof = attestation(value, kind)
                    value_path = write_json(root / f"{kind}-candidate.json", value)
                    proof_path = write_json(root / f"{kind}-attestation.json", proof)
                    batch_path = write_json(root / f"{kind}-batch.json", [value])

                    draft = run_cli(
                        repo,
                        "draft",
                        "--kind", kind,
                        "--candidate", f"@{value_path}",
                        "--attestation", f"@{proof_path}",
                        "--json",
                    )
                    self.assertEqual("candidate_primary_claim_mismatch", error_code(draft))

                    route = run_cli(
                        repo,
                        "candidate", "route",
                        "--batch", f"@{batch_path}",
                        "--capabilities", f"@{capabilities}",
                        "--claim-results", f"@{results}",
                        "--json",
                    )
                    self.assertEqual("candidate_primary_claim_mismatch", error_code(route))
                    self.assertEqual(before, tree_digest(repo))

    def test_matching_builtin_transport_remains_routable(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            repo = root / "repository"
            repo.mkdir()
            capabilities = write_json(root / "capabilities.json", context_cli.capabilities_result())

            for kind in ("observation", "snapshot"):
                with self.subTest(kind=kind):
                    value = candidate(kind)
                    value_path = write_json(root / f"matching-{kind}.json", value)
                    proof_path = write_json(root / f"matching-{kind}-attestation.json", attestation(value, kind))
                    draft = run_cli(
                        repo,
                        "draft",
                        "--kind", kind,
                        "--candidate", f"@{value_path}",
                        "--attestation", f"@{proof_path}",
                        "--json",
                    )
                    self.assertEqual(0, draft.returncode, draft.stdout + draft.stderr)
                    owner_result = json.loads(draft.stdout)["result"]
                    batch_path = write_json(root / f"matching-{kind}-batch.json", [value])
                    results_path = write_json(root / f"matching-{kind}-results.json", [owner_result])
                    route = run_cli(
                        repo,
                        "candidate", "route",
                        "--batch", f"@{batch_path}",
                        "--capabilities", f"@{capabilities}",
                        "--claim-results", f"@{results_path}",
                        "--json",
                    )
                    self.assertEqual(0, route.returncode, route.stdout + route.stderr)
                    self.assertEqual("proposed", json.loads(route.stdout)["result"]["routes"][0]["status"])

    def test_rehashed_reviewer_reproducer_fails_finalize_preview_and_apply_without_writes(self) -> None:
        for kind in ("observation", "snapshot"):
            with self.subTest(kind=kind), tempfile.TemporaryDirectory() as temp:
                root = Path(temp)
                repo = root / "repository"
                repo.mkdir()
                initialize(repo)
                matching, forged = forged_owner_result(kind)
                before = tree_digest(repo)

                with self.assertRaises(context_cli.ContextError) as caught:
                    context_cli.finalize_owner_result(repo, forged)
                self.assertEqual("candidate_primary_claim_mismatch", caught.exception.code)
                self.assertEqual(before, tree_digest(repo))

                forged_result_path = write_json(root / f"forged-{kind}-result.json", forged)
                preview = run_cli(
                    repo,
                    "transaction", "preview",
                    "--owner-result", f"@{forged_result_path}",
                    "--json",
                )
                self.assertEqual("candidate_primary_claim_mismatch", error_code(preview))
                self.assertEqual(before, tree_digest(repo))

                valid_preview = context_cli.finalize_owner_result(repo, matching)
                replay = forged_bundle(valid_preview["bundle"])
                replay_path = write_json(root / f"forged-{kind}-bundle.json", replay)
                applied = run_cli(
                    repo,
                    "transaction", "apply",
                    "--plan-bundle", f"@{replay_path}",
                    "--approved-digest", replay["approval_digest"],
                    "--json",
                )
                self.assertEqual("candidate_primary_claim_mismatch", error_code(applied))
                self.assertEqual(before, tree_digest(repo))


if __name__ == "__main__":
    unittest.main()
