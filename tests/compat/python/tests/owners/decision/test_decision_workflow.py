#!/usr/bin/env python3
from __future__ import annotations

import hashlib
import json
import os
import stat
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock


ROOT = next(p for p in Path(__file__).resolve().parents if (p / "pytest.ini").is_file())
CORE_CLI = ROOT / "plugins/bobbin/skills/context/scripts/context_cli.py"
DECISION_INIT = ROOT / "plugins/bobbin/skills/init/scripts/decision_init.py"
WORKFLOW = ROOT / "plugins/bobbin/skills/decision/scripts/decision_workflow.py"


sys.path.insert(0, str(WORKFLOW.parent))
import decision_workflow as workflow_module


def run(
    repo: Path,
    script: Path,
    *arguments: str,
    environment: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    command_environment = {**os.environ, "PYTHONDONTWRITEBYTECODE": "1"}
    if environment:
        command_environment.update(environment)
    return subprocess.run(
        [sys.executable, str(script), *arguments],
        cwd=repo,
        env=command_environment,
        text=True,
        capture_output=True,
    )


def canonical_digest(value: object) -> str:
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return "sha256:" + hashlib.sha256(encoded).hexdigest()


def digest_tree(root: Path) -> str:
    digest = hashlib.sha256()
    for path in sorted(item for item in root.rglob("*") if item.is_file()):
        digest.update(path.relative_to(root).as_posix().encode("utf-8"))
        digest.update(path.read_bytes())
    return digest.hexdigest()


def write_json(path: Path, value: object) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")


def vault_identity(repo: Path) -> dict:
    root = repo.resolve(strict=True)
    metadata = root.stat()
    return {
        "schema": "context-vault-identity/v1",
        "root": {"path": str(root), "device": str(metadata.st_dev), "inode": str(metadata.st_ino)},
    }


class DecisionWorkflowTests(unittest.TestCase):
    def test_preview_help_distinguishes_date_condition_and_repeated_relation_flags(self) -> None:
        completed = run(ROOT, WORKFLOW, "preview", "--help")
        self.assertEqual(0, completed.returncode, completed.stdout + completed.stderr)
        help_text = " ".join(completed.stdout.split())
        self.assertIn("Calendar date only (YYYY-MM-DD)", help_text)
        self.assertIn("condition text: --sec-revisit", help_text)
        self.assertIn("repeat the flag for multiple ids", help_text)

    def _workflow_environment(self, root: Path) -> tuple[dict[str, str], Path]:
        temp_root = root / "private-temp"
        temp_root.mkdir(mode=0o700)
        return {"TMPDIR": str(temp_root)}, temp_root / "context-decision"

    def _initialized_repository(self, root: Path) -> Path:
        repo = root / "repository"
        repo.mkdir()
        (repo / "keep.txt").write_text("repository bytes\n", encoding="utf-8")
        core_init = run(repo, CORE_CLI, "init", "--host", "codex", "--json")
        self.assertEqual(0, core_init.returncode, core_init.stdout + core_init.stderr)
        decision_init = run(
            repo,
            DECISION_INIT,
            "--host",
            "codex",
            "--core-cli",
            str(CORE_CLI),
            "--json",
        )
        self.assertEqual(0, decision_init.returncode, decision_init.stdout + decision_init.stderr)
        return repo

    def _semantic_inputs(self, root: Path) -> tuple[Path, Path, dict]:
        candidate = {
            "schema": "context-capture-candidate/v1",
            "candidate_id": "cand_550e8400e29b41d4a716446655440000",
            "title": "인증 세션 소유권",
            "claim": "인증 세션은 BFF가 소유한다.",
            "summary": "OAuth callback과 cookie 경계를 BFF로 통합한다.",
            "captured_from": "conversation",
            "requested_kind": "decision",
            "specialized_kinds": ["decision"],
            "fallback_kind": None,
            "scope_hint": "project/auth",
            "source_refs": [],
            "tags": ["auth"],
            "search_terms": ["session-owner"],
            "evidence": ["결정 권한자가 현재 따를 선택으로 확정했다."],
            "owner_inputs": {
                "decision": {
                    "decision": "인증 세션은 BFF가 소유한다.",
                    "rationale": "브라우저별 cookie 차이를 서버 경계 안으로 모은다.",
                    "rejected_alternatives": ["SPA token 소유: XSS 노출이 커져 반려"],
                    "decision_key": "session-owner",
                }
            },
        }
        attestation = {
            "schema": "context-semantic-attestation/v1",
            "operation": "claim",
            "input_schema": candidate["schema"],
            "input_digest": canonical_digest(candidate),
            "assertions": [
                {"name": "explicit_choice", "value": True, "evidence_pointers": ["/owner_inputs/decision/decision"]},
                {"name": "scope_identified", "value": True, "evidence_pointers": ["/scope_hint"]},
                {"name": "commitment_present", "value": True, "evidence_pointers": ["/evidence/0"]},
            ],
        }
        candidate_path = root / "candidate.json"
        attestation_path = root / "attestation.json"
        write_json(candidate_path, candidate)
        write_json(attestation_path, attestation)
        return candidate_path, attestation_path, candidate

    def _inline_arguments(self) -> list[str]:
        return [
            "--inline",
            "--candidate-id",
            "cand_550e8400e29b41d4a716446655440000",
            "--title",
            "인증 세션 소유권",
            "--summary",
            "OAuth callback과 cookie 경계를 BFF로 통합한다.",
            "--scope",
            "project/auth",
            "--decision-key",
            "session-owner",
            "--captured-from",
            "conversation",
            "--commitment-evidence",
            "결정 권한자가 현재 따를 선택으로 확정했다.",
            "--sec-decision",
            "인증 세션은 BFF가 소유한다.",
            "--sec-rationale",
            "브라우저별 cookie 차이를 서버 경계 안으로 모은다.",
            "--sec-alternatives",
            "SPA token 소유: XSS 노출이 커져 반려",
            "--tag",
            "auth",
            "--search-term",
            "session-owner",
        ]

    def test_preview_freezes_one_bundle_and_apply_uses_only_that_receipt(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            repo = self._initialized_repository(root)
            candidate_path, attestation_path, candidate = self._semantic_inputs(root)
            receipt_path = root / "decision-receipt.json"
            before = digest_tree(repo)
            preview = run(
                repo,
                WORKFLOW,
                "preview",
                "--host",
                "codex",
                "--core-cli",
                str(CORE_CLI),
                "--candidate",
                f"@{candidate_path}",
                "--attestation",
                f"@{attestation_path}",
                "--receipt-file",
                str(receipt_path),
                "--json",
            )
            self.assertEqual(0, preview.returncode, preview.stdout + preview.stderr)
            output = json.loads(preview.stdout)["result"]
            self.assertEqual(before, digest_tree(repo))
            self.assertFalse(output["applied"])
            self.assertEqual(candidate["candidate_id"], output["candidate_id"])
            self.assertNotIn("bundle", output)
            self.assertEqual(0o600, receipt_path.stat().st_mode & 0o777)
            frozen_bytes = receipt_path.read_bytes()
            receipt = json.loads(frozen_bytes)
            workflow_material = receipt["approval_material"]
            self.assertEqual(
                canonical_digest(
                    {
                        "schema": "context-decision-workflow-input/v1",
                        "operation": "capture",
                        "candidate": candidate,
                    }
                ),
                workflow_material["workflow_input_digest"],
            )
            self.assertEqual(output["approval_digest"], receipt["approval_digest"])
            self.assertEqual(
                output["plan_id"],
                workflow_material["core_bundle"]["approval_material"]["plan"]["plan_id"],
            )
            legacy_output = {
                "ok": True,
                "result": {
                    "bundle": workflow_material["core_bundle"],
                    "approval_preview": output["approval_preview"],
                    "approval_digest": workflow_material["core_approval_digest"],
                    "applied": False,
                    "noop": False,
                },
            }
            legacy_bytes = len(json.dumps(legacy_output, ensure_ascii=False, separators=(",", ":")).encode("utf-8"))
            self.assertLess(len(preview.stdout.encode("utf-8")), legacy_bytes)

            duplicate = run(
                repo,
                WORKFLOW,
                "preview",
                "--host",
                "codex",
                "--core-cli",
                str(CORE_CLI),
                "--candidate",
                f"@{candidate_path}",
                "--attestation",
                f"@{attestation_path}",
                "--receipt-file",
                str(receipt_path),
                "--json",
            )
            self.assertEqual(5, duplicate.returncode, duplicate.stdout + duplicate.stderr)
            self.assertEqual("receipt_exists", json.loads(duplicate.stdout)["error"]["code"])
            self.assertEqual(frozen_bytes, receipt_path.read_bytes())
            self.assertEqual(before, digest_tree(repo))

            rejected = run(
                repo,
                WORKFLOW,
                "apply",
                "--core-cli",
                str(CORE_CLI),
                "--receipt-file",
                str(receipt_path),
                "--approved-digest",
                "sha256:" + "0" * 64,
                "--json",
            )
            self.assertEqual(5, rejected.returncode, rejected.stdout + rejected.stderr)
            self.assertEqual("approval_digest_mismatch", json.loads(rejected.stdout)["error"]["code"])
            self.assertEqual(before, digest_tree(repo))
            self.assertEqual(frozen_bytes, receipt_path.read_bytes())

            applied = run(
                repo,
                WORKFLOW,
                "apply",
                "--core-cli",
                str(CORE_CLI),
                "--receipt-file",
                str(receipt_path),
                "--approved-digest",
                output["approval_digest"],
                "--json",
            )
            self.assertEqual(0, applied.returncode, applied.stdout + applied.stderr)
            applied_result = json.loads(applied.stdout)["result"]
            self.assertTrue(applied_result["applied"])
            self.assertEqual(output["plan_id"], applied_result["plan_id"])
            self.assertFalse(receipt_path.exists())
            self.assertNotEqual(before, digest_tree(repo))
            self.assertTrue((repo / output["approval_preview"]["artifacts"][0]["path"]).is_file())

    def test_receipt_is_repository_bound_and_invalid_preflight_writes_nothing(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            repo = self._initialized_repository(root)
            candidate_path, attestation_path, _ = self._semantic_inputs(root)
            receipt_path = root / "invalid-receipt.json"
            fake_core = root / "fake/skills/context/scripts/context_cli.py"
            fake_core.parent.mkdir(parents=True)
            marker = root / "fake-core-executed"
            fake_core.write_text(
                "from pathlib import Path\nPath(" + repr(str(marker)) + ").write_text('executed')\n",
                encoding="utf-8",
            )
            before = digest_tree(repo)
            denied = run(
                repo,
                WORKFLOW,
                "preview",
                "--host",
                "codex",
                "--core-cli",
                str(fake_core),
                "--candidate",
                f"@{candidate_path}",
                "--attestation",
                f"@{attestation_path}",
                "--receipt-file",
                str(receipt_path),
                "--json",
            )
            self.assertEqual(5, denied.returncode, denied.stdout + denied.stderr)
            denied_error = json.loads(denied.stdout)["error"]
            self.assertEqual("core_surface_mismatch", denied_error["code"])
            self.assertEqual(
                "The installed core is not same-major compatible; install a compatible core and start a new session.",
                denied_error["message"],
            )
            self.assertFalse(marker.exists())
            self.assertFalse(receipt_path.exists())
            self.assertEqual(before, digest_tree(repo))

            inside_receipt = repo / "decision-receipt.json"
            inside = run(
                repo,
                WORKFLOW,
                "preview",
                "--host",
                "codex",
                "--core-cli",
                str(CORE_CLI),
                "--candidate",
                f"@{candidate_path}",
                "--attestation",
                f"@{attestation_path}",
                "--receipt-file",
                str(inside_receipt),
                "--json",
            )
            self.assertEqual(5, inside.returncode, inside.stdout + inside.stderr)
            self.assertEqual("receipt_path_invalid", json.loads(inside.stdout)["error"]["code"])
            self.assertFalse(inside_receipt.exists())
            self.assertEqual(before, digest_tree(repo))

            valid_receipt = root / "valid-receipt.json"
            preview = run(
                repo,
                WORKFLOW,
                "preview",
                "--host",
                "codex",
                "--core-cli",
                str(CORE_CLI),
                "--candidate",
                f"@{candidate_path}",
                "--attestation",
                f"@{attestation_path}",
                "--receipt-file",
                str(valid_receipt),
                "--json",
            )
            self.assertEqual(0, preview.returncode, preview.stdout + preview.stderr)
            approval_digest = json.loads(preview.stdout)["result"]["approval_digest"]
            other = root / "other-repository"
            other.mkdir()
            mismatch = run(
                other,
                WORKFLOW,
                "apply",
                "--core-cli",
                str(CORE_CLI),
                "--receipt-file",
                str(valid_receipt),
                "--approved-digest",
                approval_digest,
                "--json",
            )
            self.assertEqual(5, mismatch.returncode, mismatch.stdout + mismatch.stderr)
            self.assertEqual("vault_identity_mismatch", json.loads(mismatch.stdout)["error"]["code"])
            self.assertEqual([], [path for path in other.rglob("*") if path.is_file()])

    def test_tampered_repository_and_rehashed_receipt_cannot_replay_in_identical_repo(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            first_root = root / "first"
            second_root = root / "second"
            first_root.mkdir()
            second_root.mkdir()
            first = self._initialized_repository(first_root)
            second = self._initialized_repository(second_root)
            candidate_path, attestation_path, _ = self._semantic_inputs(root)
            environment, _ = self._workflow_environment(root)
            preview = run(
                first,
                WORKFLOW,
                "preview",
                "--host",
                "codex",
                "--core-cli",
                str(CORE_CLI),
                "--candidate",
                f"@{candidate_path}",
                "--attestation",
                f"@{attestation_path}",
                "--json",
                environment=environment,
            )
            self.assertEqual(0, preview.returncode, preview.stdout + preview.stderr)
            preview_result = json.loads(preview.stdout)["result"]
            approved_digest = preview_result["approval_digest"]
            receipt_path = Path(preview_result["receipt_file"])
            receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
            workflow_material = receipt["approval_material"]
            second_identity = vault_identity(second)
            workflow_material["vault_identity"] = second_identity
            core_bundle = workflow_material["core_bundle"]
            core_bundle["approval_material"]["vault_identity"] = second_identity
            core_digest = canonical_digest(core_bundle["approval_material"])
            core_bundle["approval_digest"] = core_digest
            workflow_material["core_approval_digest"] = core_digest
            receipt["approval_digest"] = canonical_digest(workflow_material)
            material = dict(receipt)
            material.pop("receipt_digest")
            receipt["receipt_digest"] = canonical_digest(material)
            write_json(receipt_path, receipt)
            before = digest_tree(second)

            missing_transport = run(
                second,
                WORKFLOW,
                "apply",
                "--core-cli",
                str(CORE_CLI),
                "--receipt-file",
                str(receipt_path),
                "--json",
                environment=environment,
            )
            self.assertEqual(2, missing_transport.returncode, missing_transport.stdout + missing_transport.stderr)
            self.assertEqual(before, digest_tree(second))

            replay = run(
                second,
                WORKFLOW,
                "apply",
                "--core-cli",
                str(CORE_CLI),
                "--receipt-file",
                str(receipt_path),
                "--approved-digest",
                approved_digest,
                "--json",
                environment=environment,
            )
            self.assertEqual(5, replay.returncode, replay.stdout + replay.stderr)
            self.assertEqual("approval_digest_mismatch", json.loads(replay.stdout)["error"]["code"])
            self.assertEqual(before, digest_tree(second))

            canonical_replay = run(
                second,
                WORKFLOW,
                "apply",
                "--core-cli",
                str(CORE_CLI),
                "--approved-digest",
                approved_digest,
                "--json",
                environment=environment,
            )
            self.assertEqual(5, canonical_replay.returncode, canonical_replay.stdout + canonical_replay.stderr)
            self.assertEqual("approval_digest_mismatch", json.loads(canonical_replay.stdout)["error"]["code"])
            self.assertEqual(before, digest_tree(second))

    def test_inline_preview_serializes_explicit_semantics_without_input_files(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            repo = self._initialized_repository(root)
            receipt_path = root / "inline-receipt.json"
            base = [
                "preview",
                "--host",
                "codex",
                "--core-cli",
                str(CORE_CLI),
                *self._inline_arguments(),
                "--receipt-file",
                str(receipt_path),
                "--json",
            ]
            before = digest_tree(repo)
            unattested = run(repo, WORKFLOW, *base)
            self.assertEqual(5, unattested.returncode, unattested.stdout + unattested.stderr)
            self.assertEqual("semantic_attestation_required", json.loads(unattested.stdout)["error"]["code"])
            self.assertFalse(receipt_path.exists())
            self.assertEqual(before, digest_tree(repo))

            preview = run(
                repo,
                WORKFLOW,
                *base[:-3],
                "--attest-explicit-choice",
                "--attest-scope-identified",
                "--attest-commitment-present",
                *base[-3:],
            )
            self.assertEqual(0, preview.returncode, preview.stdout + preview.stderr)
            output = json.loads(preview.stdout)["result"]
            self.assertEqual("cand_550e8400e29b41d4a716446655440000", output["candidate_id"])
            self.assertFalse(output["applied"])
            self.assertEqual(before, digest_tree(repo))
            self.assertFalse((root / "candidate.json").exists())
            self.assertFalse((root / "attestation.json").exists())
            receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
            self.assertRegex(receipt["approval_material"]["workflow_input_digest"], r"^sha256:[0-9a-f]{64}$")

            applied = run(
                repo,
                WORKFLOW,
                "apply",
                "--core-cli",
                str(CORE_CLI),
                "--receipt-file",
                str(receipt_path),
                "--approved-digest",
                output["approval_digest"],
                "--json",
            )
            self.assertEqual(0, applied.returncode, applied.stdout + applied.stderr)
            self.assertTrue(json.loads(applied.stdout)["result"]["applied"])
            self.assertFalse(receipt_path.exists())

    def test_typed_preview_conditionally_requires_typed_core_feature(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            repo = self._initialized_repository(root)
            receipt_path = root / "typed-old-core-receipt.json"
            old_core_schema = {
                "schema": "context-core-schema/v1",
                "protocol": workflow_module.decision_cli.PROTOCOL,
                "features": ["context-owner-descriptor/v2", "filesystem-vault/v1"],
                "commands": ["doctor", "bootstrap", "transaction preview", "transaction apply"],
            }
            self.assertIs(
                old_core_schema,
                workflow_module.decision_cli.validate_core_schema_handshake(old_core_schema),
                "standalone and legacy DEC must retain the prior same-major handshake",
            )
            args = workflow_module.build_parser().parse_args([
                "--vault", str(repo),
                "preview",
                "--host", "codex",
                "--core-cli", str(CORE_CLI),
                *self._inline_arguments(),
                "--serves-intent", "ctx_550e8400e29b41d4a716446655440099",
                "--attest-explicit-choice",
                "--attest-scope-identified",
                "--attest-commitment-present",
                "--receipt-file", str(receipt_path),
                "--json",
            ])
            before = digest_tree(repo)
            with mock.patch.object(workflow_module, "_run_core", return_value=old_core_schema) as run_core:
                with self.assertRaises(workflow_module.decision_cli.DecisionError) as caught:
                    workflow_module.preview(args)
            self.assertEqual("core_incompatible", caught.exception.code)
            self.assertIn("typed-relations/v1", caught.exception.details["required_features"])
            self.assertEqual(1, run_core.call_count, "typed preview must fail before doctor or receipt creation")
            self.assertFalse(receipt_path.exists())
            self.assertEqual(before, digest_tree(repo))

    def test_inline_body_files_fail_closed_before_receipt_or_repository_write(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            repo = self._initialized_repository(root)
            body = root / "body.txt"
            body.write_text("safe body\n", encoding="utf-8")
            link = root / "body-link.txt"
            link.symlink_to(body)
            oversized = root / "oversized.txt"
            oversized.write_bytes(b"x" * 8193)
            before = digest_tree(repo)

            for label, argument, code, size in (
                ("missing", f"@{root / 'missing.txt'}", "input_unavailable", None),
                ("symlink", f"@{link}", "input_unavailable", None),
                ("oversized", f"@{oversized}", "input_too_large", 8193),
            ):
                with self.subTest(label=label):
                    receipt = root / f"{label}-receipt.json"
                    inline = self._inline_arguments()
                    inline[inline.index("--sec-rationale") + 1] = argument
                    completed = run(
                        repo,
                        WORKFLOW,
                        "preview",
                        "--host",
                        "codex",
                        "--core-cli",
                        str(CORE_CLI),
                        *inline,
                        "--attest-explicit-choice",
                        "--attest-scope-identified",
                        "--attest-commitment-present",
                        "--receipt-file",
                        str(receipt),
                        "--json",
                    )
                    self.assertNotEqual(0, completed.returncode, completed.stdout + completed.stderr)
                    error = json.loads(completed.stdout)["error"]
                    self.assertEqual(code, error["code"])
                    if size is not None:
                        self.assertEqual(
                            {"actual_bytes": 8193, "maximum_bytes": 8192, "over_by_bytes": 1},
                            {key: error["details"][key] for key in ("actual_bytes", "maximum_bytes", "over_by_bytes")},
                        )
                    self.assertFalse(receipt.exists())
                    self.assertEqual(before, digest_tree(repo))

    def test_dogfood_decision_body_427_codepoints_and_2182_owner_bytes_previews(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            repo = self._initialized_repository(root)
            receipt = root / "dogfood-receipt.json"
            decision = "결" * 427
            decision_file = root / "decision.txt"
            decision_file.write_text(decision + "\n", encoding="utf-8")
            values = {
                "decision": decision,
                "rationale": "",
                "rejected_alternatives": ["notes/rejected.md"],
                "decision_key": "dogfood-limit",
            }
            base_bytes = len(json.dumps(values, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8"))
            values["rationale"] = "r" * (2182 - base_bytes)
            self.assertEqual(427, len(decision))
            self.assertEqual(2182, len(json.dumps(values, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")))
            before = digest_tree(repo)
            preview = run(
                repo,
                WORKFLOW,
                "preview",
                "--host",
                "codex",
                "--core-cli",
                str(CORE_CLI),
                "--inline",
                "--candidate-id",
                "cand_950e8400e29b41d4a716446655440000",
                "--title",
                "Dogfood decision size",
                "--summary",
                "기존 2 KiB 제한을 넘는 실제 결정 입력을 보존한다.",
                "--scope",
                "project/dogfood",
                "--decision-key",
                values["decision_key"],
                "--captured-from",
                "conversation",
                "--commitment-evidence",
                "결정권자가 현재 따를 선택으로 확정했다.",
                "--sec-decision",
                f"@{decision_file}",
                "--sec-rationale",
                values["rationale"],
                "--sec-alternatives",
                values["rejected_alternatives"][0],
                "--attest-explicit-choice",
                "--attest-scope-identified",
                "--attest-commitment-present",
                "--receipt-file",
                str(receipt),
                "--json",
            )
            self.assertEqual(0, preview.returncode, preview.stdout + preview.stderr)
            self.assertTrue(receipt.is_file())
            self.assertFalse(json.loads(preview.stdout)["result"]["applied"])
            self.assertEqual(before, digest_tree(repo))

    def test_file_and_inline_inputs_cannot_be_mixed(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            repo = self._initialized_repository(root)
            receipt_path = root / "mixed-receipt.json"
            mixed = run(
                repo,
                WORKFLOW,
                "preview",
                "--host",
                "codex",
                "--core-cli",
                str(CORE_CLI),
                "--candidate",
                "@/does/not/exist.json",
                "--candidate-id",
                "cand_550e8400e29b41d4a716446655440000",
                "--attestation",
                "@/does/not/exist.json",
                "--receipt-file",
                str(receipt_path),
                "--json",
            )
            self.assertEqual(2, mixed.returncode, mixed.stdout + mixed.stderr)
            self.assertEqual("usage_invalid", json.loads(mixed.stdout)["error"]["code"])
            self.assertFalse(receipt_path.exists())

    def test_canonical_defaults_create_one_private_receipt_and_apply_selects_then_cleans_it(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            repo = self._initialized_repository(root)
            environment, receipt_dir = self._workflow_environment(root)
            inline = self._inline_arguments()
            for flag in ("--candidate-id", "--captured-from"):
                index = inline.index(flag)
                del inline[index : index + 2]
            before = digest_tree(repo)

            preview = run(
                repo,
                WORKFLOW,
                "preview",
                "--host",
                "codex",
                "--core-cli",
                str(CORE_CLI),
                *inline,
                "--attest-explicit-choice",
                "--attest-scope-identified",
                "--attest-commitment-present",
                "--json",
                environment=environment,
            )
            self.assertEqual(0, preview.returncode, preview.stdout + preview.stderr)
            output = json.loads(preview.stdout)["result"]
            self.assertRegex(output["candidate_id"], r"^cand_[0-9a-f]{32}$")
            self.assertEqual(before, digest_tree(repo))
            self.assertTrue(receipt_dir.is_dir())
            self.assertEqual(0o700, stat.S_IMODE(receipt_dir.stat().st_mode))
            receipts = list(receipt_dir.iterdir())
            self.assertEqual(1, len(receipts))
            receipt_path = receipts[0]
            self.assertEqual(f"{output['candidate_id']}.json", receipt_path.name)
            self.assertEqual(0o600, stat.S_IMODE(receipt_path.stat().st_mode))
            receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
            self.assertEqual(
                {
                    "schema",
                    "status",
                    "created_at",
                    "candidate_id",
                    "operation",
                    "approval_material",
                    "approval_digest",
                    "receipt_digest",
                },
                set(receipt),
            )
            self.assertEqual("context-decision-workflow-receipt/v1", receipt["schema"])
            self.assertEqual("pending", receipt["status"])
            self.assertEqual("capture", receipt["operation"])
            self.assertEqual(output["candidate_id"], receipt["candidate_id"])
            self.assertEqual(
                {
                    "schema",
                    "vault_identity",
                    "core",
                    "operation",
                    "workflow_input_digest",
                    "owner_result_digest",
                    "core_approval_digest",
                    "core_bundle",
                },
                set(receipt["approval_material"]),
            )

            applied = run(
                repo,
                WORKFLOW,
                "apply",
                "--core-cli",
                str(CORE_CLI),
                "--approved-digest",
                output["approval_digest"],
                "--json",
                environment=environment,
            )
            self.assertEqual(0, applied.returncode, applied.stdout + applied.stderr)
            self.assertTrue(json.loads(applied.stdout)["result"]["applied"])
            self.assertEqual([], list(receipt_dir.iterdir()))
            self.assertNotEqual(before, digest_tree(repo))

    def test_auto_selection_zero_or_multiple_is_fail_closed_and_reject_is_write_free(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            repo = self._initialized_repository(root)
            environment, receipt_dir = self._workflow_environment(root)
            before = digest_tree(repo)
            none = run(
                repo,
                WORKFLOW,
                "apply",
                "--core-cli",
                str(CORE_CLI),
                "--approved-digest",
                "sha256:" + "0" * 64,
                "--json",
                environment=environment,
            )
            self.assertEqual(5, none.returncode, none.stdout + none.stderr)
            self.assertEqual("receipt_selection_none", json.loads(none.stdout)["error"]["code"])
            self.assertEqual(before, digest_tree(repo))

            outputs = []
            for number in range(2):
                inline = self._inline_arguments()
                inline[inline.index("--candidate-id") + 1] = f"cand_{number + 1:032x}"
                inline[inline.index("--scope") + 1] = f"project/auth-{number}"
                inline[inline.index("--decision-key") + 1] = f"session-owner-{number}"
                preview = run(
                    repo,
                    WORKFLOW,
                    "preview",
                    "--host",
                    "codex",
                    "--core-cli",
                    str(CORE_CLI),
                    *inline,
                    "--attest-explicit-choice",
                    "--attest-scope-identified",
                    "--attest-commitment-present",
                    "--json",
                    environment=environment,
                )
                self.assertEqual(0, preview.returncode, preview.stdout + preview.stderr)
                outputs.append(json.loads(preview.stdout)["result"])
            self.assertEqual(2, len(list(receipt_dir.iterdir())))
            older = time.time() - 60
            os.utime(Path(outputs[0]["receipt_file"]), (older, older))
            ambiguous = run(
                repo,
                WORKFLOW,
                "apply",
                "--core-cli",
                str(CORE_CLI),
                "--approved-digest",
                outputs[0]["approval_digest"],
                "--json",
                environment=environment,
            )
            self.assertEqual(5, ambiguous.returncode, ambiguous.stdout + ambiguous.stderr)
            ambiguous_error = json.loads(ambiguous.stdout)["error"]
            self.assertEqual("receipt_selection_ambiguous", ambiguous_error["code"])
            self.assertEqual(2, ambiguous_error["details"]["matching_receipts"])
            self.assertEqual(before, digest_tree(repo))

            rejected = run(
                repo,
                WORKFLOW,
                "reject",
                "--candidate-id",
                outputs[0]["candidate_id"],
                "--json",
                environment=environment,
            )
            self.assertEqual(0, rejected.returncode, rejected.stdout + rejected.stderr)
            self.assertTrue(json.loads(rejected.stdout)["result"]["rejected"])
            self.assertEqual(1, len(list(receipt_dir.iterdir())))
            self.assertEqual(before, digest_tree(repo))

            canonical_reject = run(
                repo,
                WORKFLOW,
                "reject",
                "--core-cli",
                str(CORE_CLI),
                "--json",
                environment=environment,
            )
            self.assertEqual(0, canonical_reject.returncode, canonical_reject.stdout + canonical_reject.stderr)
            self.assertEqual([], list(receipt_dir.iterdir()))
            self.assertEqual(before, digest_tree(repo))

    def test_preview_sweeps_only_expired_regular_default_receipts(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            repo = self._initialized_repository(root)
            environment, receipt_dir = self._workflow_environment(root)
            first = run(
                repo,
                WORKFLOW,
                "preview",
                "--host",
                "codex",
                "--core-cli",
                str(CORE_CLI),
                *self._inline_arguments(),
                "--attest-explicit-choice",
                "--attest-scope-identified",
                "--attest-commitment-present",
                "--json",
                environment=environment,
            )
            self.assertEqual(0, first.returncode, first.stdout + first.stderr)
            first_receipt = Path(json.loads(first.stdout)["result"]["receipt_file"])
            expired = time.time() - (25 * 60 * 60)
            os.utime(first_receipt, (expired, expired))

            ordinary = receipt_dir / "notes.txt"
            ordinary.write_text("keep\n", encoding="utf-8")
            other_schema = receipt_dir / ("cand_" + "a" * 32 + ".json")
            other_schema.write_text('{"schema":"other/v1"}\n', encoding="utf-8")
            other_schema.chmod(0o600)
            os.utime(other_schema, (expired, expired))
            malformed = receipt_dir / ("cand_" + "b" * 32 + ".json")
            malformed.write_text('{"schema":"context-decision-workflow-receipt/v1"}\n', encoding="utf-8")
            malformed.chmod(0o600)
            os.utime(malformed, (expired, expired))
            target = receipt_dir / "target.txt"
            target.write_text("keep target\n", encoding="utf-8")
            symlink = receipt_dir / ("cand_" + "c" * 32 + ".json")
            symlink.symlink_to(target)
            directory = receipt_dir / ("cand_" + "d" * 32 + ".json")
            directory.mkdir()

            inline = self._inline_arguments()
            inline[inline.index("--candidate-id") + 1] = "cand_" + "e" * 32
            inline[inline.index("--scope") + 1] = "project/payments"
            inline[inline.index("--decision-key") + 1] = "settlement-owner"
            second = run(
                repo,
                WORKFLOW,
                "preview",
                "--host",
                "codex",
                "--core-cli",
                str(CORE_CLI),
                *inline,
                "--attest-explicit-choice",
                "--attest-scope-identified",
                "--attest-commitment-present",
                "--json",
                environment=environment,
            )
            self.assertEqual(0, second.returncode, second.stdout + second.stderr)
            self.assertFalse(first_receipt.exists())
            for path in (ordinary, other_schema, malformed, target, symlink, directory):
                self.assertTrue(path.exists() or path.is_symlink(), path)

    def test_receipt_symlink_traversal_and_default_directory_symlink_are_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            repo = self._initialized_repository(root)
            candidate_path, attestation_path, _ = self._semantic_inputs(root)
            target = root / "target.json"
            target.write_text("keep\n", encoding="utf-8")
            receipt_link = root / "receipt-link.json"
            receipt_link.symlink_to(target)
            before = digest_tree(repo)
            linked = run(
                repo,
                WORKFLOW,
                "preview",
                "--host",
                "codex",
                "--core-cli",
                str(CORE_CLI),
                "--candidate",
                f"@{candidate_path}",
                "--attestation",
                f"@{attestation_path}",
                "--receipt-file",
                str(receipt_link),
                "--json",
            )
            self.assertEqual(5, linked.returncode, linked.stdout + linked.stderr)
            self.assertEqual("receipt_exists", json.loads(linked.stdout)["error"]["code"])
            self.assertEqual("keep\n", target.read_text(encoding="utf-8"))

            outside = root / "outside"
            outside.mkdir()
            traversal = outside / ".." / "repository" / "receipt.json"
            escaped = run(
                repo,
                WORKFLOW,
                "preview",
                "--host",
                "codex",
                "--core-cli",
                str(CORE_CLI),
                "--candidate",
                f"@{candidate_path}",
                "--attestation",
                f"@{attestation_path}",
                "--receipt-file",
                str(traversal),
                "--json",
            )
            self.assertEqual(5, escaped.returncode, escaped.stdout + escaped.stderr)
            self.assertEqual("receipt_path_invalid", json.loads(escaped.stdout)["error"]["code"])
            self.assertEqual(before, digest_tree(repo))

            environment, receipt_dir = self._workflow_environment(root)
            receipt_dir.parent.mkdir(mode=0o700, exist_ok=True)
            unsafe_target = root / "unsafe-default-target"
            unsafe_target.mkdir()
            receipt_dir.symlink_to(unsafe_target, target_is_directory=True)
            unsafe = run(
                repo,
                WORKFLOW,
                "preview",
                "--host",
                "codex",
                "--core-cli",
                str(CORE_CLI),
                *self._inline_arguments(),
                "--attest-explicit-choice",
                "--attest-scope-identified",
                "--attest-commitment-present",
                "--json",
                environment=environment,
            )
            self.assertEqual(5, unsafe.returncode, unsafe.stdout + unsafe.stderr)
            self.assertEqual("receipt_directory_invalid", json.loads(unsafe.stdout)["error"]["code"])
            self.assertEqual([], list(unsafe_target.iterdir()))
            self.assertEqual(before, digest_tree(repo))

            mode_case = root / "mode-case"
            mode_case.mkdir()
            mode_repo = self._initialized_repository(mode_case)
            mode_environment, mode_receipt_dir = self._workflow_environment(mode_case)
            mode_receipt_dir.mkdir(mode=0o755)
            mode_receipt_dir.chmod(0o755)
            mode_before = digest_tree(mode_repo)
            unsafe_mode = run(
                mode_repo,
                WORKFLOW,
                "preview",
                "--host",
                "codex",
                "--core-cli",
                str(CORE_CLI),
                *self._inline_arguments(),
                "--attest-explicit-choice",
                "--attest-scope-identified",
                "--attest-commitment-present",
                "--json",
                environment=mode_environment,
            )
            self.assertEqual(5, unsafe_mode.returncode, unsafe_mode.stdout + unsafe_mode.stderr)
            self.assertEqual("receipt_directory_invalid", json.loads(unsafe_mode.stdout)["error"]["code"])
            self.assertEqual(0o755, stat.S_IMODE(mode_receipt_dir.stat().st_mode))
            self.assertEqual(mode_before, digest_tree(mode_repo))

    def test_repository_local_temp_root_is_rejected_before_default_directory_creation(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            repo = self._initialized_repository(root)
            temp_root = repo / "private-temp"
            temp_root.mkdir(mode=0o700)
            default_directory = temp_root / "context-decision"
            environment = {"TMPDIR": str(temp_root)}
            before = digest_tree(repo)

            default_preview = run(
                repo,
                WORKFLOW,
                "preview",
                "--host",
                "codex",
                "--core-cli",
                str(CORE_CLI),
                *self._inline_arguments(),
                "--attest-explicit-choice",
                "--attest-scope-identified",
                "--attest-commitment-present",
                "--json",
                environment=environment,
            )
            self.assertEqual(5, default_preview.returncode, default_preview.stdout + default_preview.stderr)
            self.assertEqual("receipt_path_invalid", json.loads(default_preview.stdout)["error"]["code"])
            self.assertFalse(default_directory.exists())
            self.assertEqual(before, digest_tree(repo))

            explicit_receipt = root / "explicit-receipt.json"
            explicit_preview = run(
                repo,
                WORKFLOW,
                "preview",
                "--host",
                "codex",
                "--core-cli",
                str(CORE_CLI),
                *self._inline_arguments(),
                "--attest-explicit-choice",
                "--attest-scope-identified",
                "--attest-commitment-present",
                "--receipt-file",
                str(explicit_receipt),
                "--json",
                environment=environment,
            )
            self.assertEqual(0, explicit_preview.returncode, explicit_preview.stdout + explicit_preview.stderr)
            self.assertTrue(explicit_receipt.is_file())
            self.assertFalse(default_directory.exists())
            self.assertEqual(before, digest_tree(repo))

    def test_external_approval_digest_rejects_fully_rehashed_workflow_tamper(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            repo = self._initialized_repository(root)
            environment, _ = self._workflow_environment(root)
            preview = run(
                repo,
                WORKFLOW,
                "preview",
                "--host",
                "codex",
                "--core-cli",
                str(CORE_CLI),
                *self._inline_arguments(),
                "--attest-explicit-choice",
                "--attest-scope-identified",
                "--attest-commitment-present",
                "--json",
                environment=environment,
            )
            self.assertEqual(0, preview.returncode, preview.stdout + preview.stderr)
            preview_result = json.loads(preview.stdout)["result"]
            approved_digest = preview_result["approval_digest"]
            receipt_path = Path(preview_result["receipt_file"])
            receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
            receipt["approval_material"]["workflow_input_digest"] = "sha256:" + "0" * 64
            receipt["approval_digest"] = canonical_digest(receipt["approval_material"])
            material = dict(receipt)
            material.pop("receipt_digest")
            receipt["receipt_digest"] = canonical_digest(material)
            write_json(receipt_path, receipt)
            before = digest_tree(repo)

            missing_transport = run(
                repo,
                WORKFLOW,
                "apply",
                "--core-cli",
                str(CORE_CLI),
                "--receipt-file",
                str(receipt_path),
                "--json",
                environment=environment,
            )
            self.assertEqual(2, missing_transport.returncode, missing_transport.stdout + missing_transport.stderr)
            self.assertEqual(before, digest_tree(repo))

            tampered = run(
                repo,
                WORKFLOW,
                "apply",
                "--core-cli",
                str(CORE_CLI),
                "--receipt-file",
                str(receipt_path),
                "--approved-digest",
                approved_digest,
                "--json",
                environment=environment,
            )
            self.assertEqual(5, tampered.returncode, tampered.stdout + tampered.stderr)
            self.assertEqual("approval_digest_mismatch", json.loads(tampered.stdout)["error"]["code"])
            self.assertEqual(before, digest_tree(repo))

            canonical_tampered = run(
                repo,
                WORKFLOW,
                "apply",
                "--core-cli",
                str(CORE_CLI),
                "--approved-digest",
                approved_digest,
                "--json",
                environment=environment,
            )
            self.assertEqual(5, canonical_tampered.returncode, canonical_tampered.stdout + canonical_tampered.stderr)
            self.assertEqual("approval_digest_mismatch", json.loads(canonical_tampered.stdout)["error"]["code"])
            self.assertEqual(before, digest_tree(repo))

    def test_index_only_drift_applies_while_semantic_staleness_rejects_auto_and_explicit_apply(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            repo = self._initialized_repository(root)
            environment, _ = self._workflow_environment(root)
            attest = ["--attest-explicit-choice", "--attest-scope-identified", "--attest-commitment-present"]
            preview = run(
                repo, WORKFLOW, "preview", "--host", "codex", "--core-cli", str(CORE_CLI),
                *self._inline_arguments(), *attest, "--json", environment=environment,
            )
            self.assertEqual(0, preview.returncode, preview.stdout + preview.stderr)
            preview_result = json.loads(preview.stdout)["result"]
            index = repo / "context/decision/decision.index.md"
            # Index-only drift (W3): the projection changed, the approved payload did not.
            index.write_text(index.read_text(encoding="utf-8") + "\n", encoding="utf-8")
            applied = run(
                repo, WORKFLOW, "apply", "--core-cli", str(CORE_CLI),
                "--approved-digest", preview_result["approval_digest"], "--json", environment=environment,
            )
            self.assertEqual(0, applied.returncode, applied.stdout + applied.stderr)
            result = json.loads(applied.stdout)["result"]
            self.assertTrue(result["applied"])
            self.assertIn("index_regenerated:context/decision/decision.index.md", result["warnings"])
            self.assertTrue((repo / "context/decision/인증-세션-소유권.md").is_file())
            self.assertFalse(Path(preview_result["receipt_file"]).exists())

            # Semantic staleness: a Current decision lands in the previewed slot before apply.
            arguments = list(self._inline_arguments())
            arguments[arguments.index("--candidate-id") + 1] = "cand_550e8400e29b41d4a716446655440001"
            arguments[arguments.index("--title") + 1] = "인증 토큰 형식"
            arguments[arguments.index("--decision-key") + 1] = "token-format"
            second = run(
                repo, WORKFLOW, "preview", "--host", "codex", "--core-cli", str(CORE_CLI),
                *arguments, *attest, "--json", environment=environment,
            )
            self.assertEqual(0, second.returncode, second.stdout + second.stderr)
            second_result = json.loads(second.stdout)["result"]
            concurrent = list(arguments)
            concurrent[concurrent.index("--candidate-id") + 1] = "cand_550e8400e29b41d4a716446655440002"
            concurrent[concurrent.index("--title") + 1] = "인증 토큰은 opaque다"
            recorded = run(
                repo, WORKFLOW, "record", "--host", "codex", "--core-cli", str(CORE_CLI),
                *concurrent, *attest, "--approved", "--json", environment=environment,
            )
            self.assertEqual(0, recorded.returncode, recorded.stdout + recorded.stderr)
            before = digest_tree(repo)
            stale_auto = run(
                repo, WORKFLOW, "apply", "--core-cli", str(CORE_CLI),
                "--approved-digest", second_result["approval_digest"], "--json", environment=environment,
            )
            self.assertEqual(5, stale_auto.returncode, stale_auto.stdout + stale_auto.stderr)
            error = json.loads(stale_auto.stdout)["error"]
            self.assertEqual("precondition_changed", error["code"])
            self.assertEqual("State changed before storage; create a new preview.", error["message"])
            self.assertEqual(before, digest_tree(repo))
            stale_explicit = run(
                repo, WORKFLOW, "apply", "--core-cli", str(CORE_CLI),
                "--receipt-file", second_result["receipt_file"],
                "--approved-digest", second_result["approval_digest"], "--json", environment=environment,
            )
            self.assertEqual(5, stale_explicit.returncode, stale_explicit.stdout + stale_explicit.stderr)
            error = json.loads(stale_explicit.stdout)["error"]
            self.assertEqual("precondition_changed", error["code"])
            self.assertEqual("State changed before storage; create a new preview.", error["message"])
            self.assertEqual(before, digest_tree(repo))

    def test_cleanup_failure_and_kept_receipt_do_not_reenter_canonical_apply(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            repo = self._initialized_repository(root)
            candidate_path, attestation_path, _ = self._semantic_inputs(root)
            environment, receipt_dir = self._workflow_environment(root)
            preview = run(
                repo,
                WORKFLOW,
                "preview",
                "--host",
                "codex",
                "--core-cli",
                str(CORE_CLI),
                "--candidate",
                f"@{candidate_path}",
                "--attestation",
                f"@{attestation_path}",
                "--json",
                environment=environment,
            )
            self.assertEqual(0, preview.returncode, preview.stdout + preview.stderr)
            preview_result = json.loads(preview.stdout)["result"]
            receipt_path = Path(preview_result["receipt_file"])
            namespace = SimpleNamespace(
                core_cli=str(CORE_CLI),
                receipt_file=str(receipt_path),
                approved_digest=preview_result["approval_digest"],
                keep_receipt=False,
            )
            original_cwd = Path.cwd()
            try:
                os.chdir(repo)
                with mock.patch.object(workflow_module, "_remove_receipt", side_effect=OSError("injected cleanup failure")):
                    result = workflow_module.apply(namespace)
                self.assertTrue(result["applied"])
                self.assertEqual(["receipt_cleanup_failed"], result["warnings"])
                self.assertTrue(receipt_path.is_file())
                after_first = digest_tree(repo)
                canonical = SimpleNamespace(
                    core_cli=str(CORE_CLI),
                    receipt_file=None,
                    approved_digest=preview_result["approval_digest"],
                    keep_receipt=True,
                )
                with (
                    mock.patch.object(workflow_module, "_default_receipt_dir", return_value=receipt_dir),
                    mock.patch.object(workflow_module, "_run_core") as run_core,
                    self.assertRaises(workflow_module.WorkflowError) as replay,
                ):
                    workflow_module.apply(canonical)
                self.assertEqual("receipt_selection_none", replay.exception.code)
                run_core.assert_not_called()
                self.assertEqual(after_first, digest_tree(repo))
            finally:
                os.chdir(original_cwd)

            second_root = root / "keep-case"
            second_root.mkdir()
            second_repo = self._initialized_repository(second_root)
            second_candidate, second_attestation, _ = self._semantic_inputs(second_root)
            second_environment, second_receipt_dir = self._workflow_environment(second_root)
            preview = run(
                second_repo,
                WORKFLOW,
                "preview",
                "--host",
                "codex",
                "--core-cli",
                str(CORE_CLI),
                "--candidate",
                f"@{second_candidate}",
                "--attestation",
                f"@{second_attestation}",
                "--json",
                environment=second_environment,
            )
            self.assertEqual(0, preview.returncode, preview.stdout + preview.stderr)
            preview_result = json.loads(preview.stdout)["result"]
            kept = Path(preview_result["receipt_file"])
            frozen_bytes = kept.read_bytes()
            applied = run(
                second_repo,
                WORKFLOW,
                "apply",
                "--core-cli",
                str(CORE_CLI),
                "--approved-digest",
                preview_result["approval_digest"],
                "--keep-receipt",
                "--json",
                environment=second_environment,
            )
            self.assertEqual(0, applied.returncode, applied.stdout + applied.stderr)
            self.assertTrue(kept.is_file())
            self.assertEqual(frozen_bytes, kept.read_bytes())

            replay = run(
                second_repo,
                WORKFLOW,
                "apply",
                "--core-cli",
                str(CORE_CLI),
                "--approved-digest",
                preview_result["approval_digest"],
                "--json",
                environment=second_environment,
            )
            self.assertEqual(5, replay.returncode, replay.stdout + replay.stderr)
            self.assertEqual("receipt_selection_none", json.loads(replay.stdout)["error"]["code"])

            expired = time.time() - (25 * 60 * 60)
            os.utime(kept, (expired, expired))
            inline = self._inline_arguments()
            inline[inline.index("--candidate-id") + 1] = "cand_" + "f" * 32
            inline[inline.index("--title") + 1] = "결제 정산 소유권"
            inline[inline.index("--summary") + 1] = "정산 책임 경계를 payment service로 고정한다."
            inline[inline.index("--scope") + 1] = "project/payments"
            inline[inline.index("--decision-key") + 1] = "settlement-owner"
            inline[inline.index("--sec-decision") + 1] = "결제 정산은 payment service가 소유한다."
            sweep = run(
                second_repo,
                WORKFLOW,
                "preview",
                "--host",
                "codex",
                "--core-cli",
                str(CORE_CLI),
                *inline,
                "--attest-explicit-choice",
                "--attest-scope-identified",
                "--attest-commitment-present",
                "--json",
                environment=second_environment,
            )
            self.assertEqual(0, sweep.returncode, sweep.stdout + sweep.stderr)
            self.assertFalse(kept.exists())
            self.assertEqual(1, len(list(second_receipt_dir.iterdir())))

    def test_natural_supersede_and_withdraw_share_frozen_receipt_apply_path(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            repo = self._initialized_repository(root)
            environment, receipt_dir = self._workflow_environment(root)

            def preview_inline(*extra: str, decision: str = "인증 세션은 BFF가 소유한다.") -> subprocess.CompletedProcess[str]:
                inline = self._inline_arguments()
                for flag in ("--candidate-id", "--captured-from"):
                    index = inline.index(flag)
                    del inline[index : index + 2]
                inline[inline.index("--sec-decision") + 1] = decision
                return run(
                    repo,
                    WORKFLOW,
                    "preview",
                    "--host",
                    "codex",
                    "--core-cli",
                    str(CORE_CLI),
                    *extra,
                    *inline,
                    "--attest-explicit-choice",
                    "--attest-scope-identified",
                    "--attest-commitment-present",
                    "--json",
                    environment=environment,
                )

            first = preview_inline()
            self.assertEqual(0, first.returncode, first.stdout + first.stderr)
            first_result = json.loads(first.stdout)["result"]
            applied = run(
                repo,
                WORKFLOW,
                "apply",
                "--core-cli",
                str(CORE_CLI),
                "--approved-digest",
                first_result["approval_digest"],
                "--json",
                environment=environment,
            )
            self.assertEqual(0, applied.returncode, applied.stdout + applied.stderr)
            current = run(repo, Path(workflow_module.decision_cli.__file__), "search", "--json")
            self.assertEqual(0, current.returncode, current.stdout + current.stderr)
            predecessor = json.loads(current.stdout)["result"]["items"][0]

            conflict = preview_inline(decision="인증 세션은 API gateway가 소유한다.")
            self.assertEqual(5, conflict.returncode, conflict.stdout + conflict.stderr)
            conflict_error = json.loads(conflict.stdout)["error"]
            self.assertEqual("decision_slot_conflict", conflict_error["code"])
            self.assertEqual("supersede", conflict_error["details"]["suggested_action"])
            self.assertIn(predecessor["title"], conflict_error["message"])
            self.assertEqual([], list(receipt_dir.iterdir()))

            supersede = preview_inline(
                "--supersede",
                predecessor["id"],
                decision="인증 세션은 API gateway가 소유한다.",
            )
            self.assertEqual(0, supersede.returncode, supersede.stdout + supersede.stderr)
            supersede_result = json.loads(supersede.stdout)["result"]
            superseded = run(
                repo,
                WORKFLOW,
                "apply",
                "--core-cli",
                str(CORE_CLI),
                "--approved-digest",
                supersede_result["approval_digest"],
                "--json",
                environment=environment,
            )
            self.assertEqual(0, superseded.returncode, superseded.stdout + superseded.stderr)
            current = run(repo, Path(workflow_module.decision_cli.__file__), "search", "--json")
            successor = json.loads(current.stdout)["result"]["items"][0]
            self.assertNotEqual(predecessor["id"], successor["id"])
            history = run(repo, Path(workflow_module.decision_cli.__file__), "read", "--id", predecessor["id"], "--json")
            history_result = json.loads(history.stdout)["result"]
            self.assertTrue(history_result["do_not_follow"])
            self.assertEqual("superseded", history_result["lifecycle_reason"])
            history_brief = run(
                repo,
                Path(workflow_module.decision_cli.__file__),
                "brief",
                "--id",
                predecessor["id"],
                "--include-history",
                "--json",
            )
            self.assertEqual(
                successor["id"],
                json.loads(history_brief.stdout)["result"]["items"][0]["successor"],
            )

            withdraw = run(
                repo,
                WORKFLOW,
                "preview",
                "--host",
                "codex",
                "--core-cli",
                str(CORE_CLI),
                "--withdraw",
                successor["id"],
                "--reason",
                "더는 현재 선택으로 따르지 않는다.",
                "--json",
                environment=environment,
            )
            self.assertEqual(0, withdraw.returncode, withdraw.stdout + withdraw.stderr)
            withdraw_result = json.loads(withdraw.stdout)["result"]
            transport_id = withdraw_result["candidate_id"]
            self.assertRegex(transport_id, r"^cand_[0-9a-f]{32}$")
            withdraw_receipt_path = Path(withdraw_result["receipt_file"])
            self.assertEqual(f"{transport_id}.json", withdraw_receipt_path.name)
            withdraw_receipt = json.loads(withdraw_receipt_path.read_text(encoding="utf-8"))
            self.assertEqual("withdraw", withdraw_receipt["operation"])
            self.assertNotIn(
                transport_id,
                json.dumps(withdraw_receipt["approval_material"]["core_bundle"], ensure_ascii=False),
            )
            self.assertNotIn(transport_id, json.dumps(withdraw_result["approval_preview"], ensure_ascii=False))
            withdrawn = run(
                repo,
                WORKFLOW,
                "apply",
                "--core-cli",
                str(CORE_CLI),
                "--approved-digest",
                withdraw_result["approval_digest"],
                "--json",
                environment=environment,
            )
            self.assertEqual(0, withdrawn.returncode, withdrawn.stdout + withdrawn.stderr)
            current = run(repo, Path(workflow_module.decision_cli.__file__), "search", "--json")
            self.assertEqual([], json.loads(current.stdout)["result"]["items"])
            history = run(repo, Path(workflow_module.decision_cli.__file__), "read", "--id", successor["id"], "--json")
            history_result = json.loads(history.stdout)["result"]
            self.assertTrue(history_result["do_not_follow"])
            self.assertEqual("withdrawn", history_result["lifecycle_reason"])


if __name__ == "__main__":
    unittest.main()
