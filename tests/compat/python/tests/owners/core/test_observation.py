#!/usr/bin/env python3
from __future__ import annotations

import copy
import hashlib
import importlib.util
import json
import os
import stat
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


PLUGIN = next(p for p in Path(__file__).resolve().parents if (p / "pytest.ini").is_file()) / "plugins/bobbin"
CLI_PATH = PLUGIN / "skills/context/scripts/context_cli.py"
SPEC = importlib.util.spec_from_file_location("context_cli_observation", CLI_PATH)
assert SPEC and SPEC.loader
context_cli = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = context_cli
SPEC.loader.exec_module(context_cli)


def vault_dir() -> tempfile.TemporaryDirectory[str]:
    temp = tempfile.TemporaryDirectory()
    return temp


def tree_digest(root: Path) -> str:
    digest = hashlib.sha256()
    for path in sorted(path for path in root.rglob("*") if path.is_file()):
        digest.update(path.relative_to(root).as_posix().encode())
        digest.update(path.read_bytes())
    return digest.hexdigest()


def initialize(repo: Path) -> None:
    preview = context_cli.build_init_bundle(repo)
    context_cli.apply_bundle(repo, preview["bundle"], preview["approval_digest"])


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


def claim_attestation(candidate: dict) -> dict:
    digest = context_cli.canonical_digest(candidate)
    return {
        "schema": "context-semantic-attestation/v1",
        "operation": "claim",
        "input_schema": candidate["schema"],
        "input_digest": digest,
        "assertions": [
            {"name": "reusable_observation", "value": True, "evidence_pointers": ["/owner_inputs/observation/observation"]},
            {"name": "evidence_present", "value": True, "evidence_pointers": ["/owner_inputs/observation/evidence/0"]},
        ],
    }


def observation_candidate(title: str, claim: str | None = None, *, kind_hint: str | None = None) -> dict:
    claim = claim or f"{title} claim"
    return context_cli.direct_candidate(
        "observation",
        title=title,
        summary=f"{title} summary",
        captured_from="workspace",
        owner_inputs={"observation": claim, "evidence": [f"{title} evidence"]},
        kind_hint=kind_hint,
    )


def observation_owner_result(title: str, claim: str | None = None, *, kind_hint: str | None = None, now: str = "2026-08-13T18:20:00+09:00") -> dict:
    candidate = observation_candidate(title, claim, kind_hint=kind_hint)
    return context_cli.draft_owner_result(candidate, claim_attestation(candidate), now=now)


def capture(repo: Path, title: str, claim: str | None = None, *, kind_hint: str | None = None, now: str = "2026-08-13T18:20:00+09:00") -> tuple[dict, str]:
    owner_result = observation_owner_result(title, claim, kind_hint=kind_hint, now=now)
    preview = context_cli.finalize_owner_result(repo, owner_result)
    context_cli.apply_bundle(repo, preview["bundle"], preview["approval_digest"])
    return owner_result, preview["approval_preview"]["effects"][0]["id"]


def same_claim_attestation(lifecycle_input: dict) -> dict:
    digest = context_cli.canonical_digest(lifecycle_input)
    return {
        "schema": "context-semantic-attestation/v1",
        "operation": "same_claim",
        "input_schema": lifecycle_input["schema"],
        "input_digest": digest,
        "assertions": [{
            "name": "same_semantic_claim",
            "value": True,
            "evidence_pointers": ["/predecessor/primary_claim", "/successor/primary_claim"],
        }],
    }


class ObservationTests(unittest.TestCase):
    def test_flag_only_capture_freezes_private_receipt_and_applies_once(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            repo = root / "repository"
            private_temp = root / "private-temp"
            repo.mkdir()
            private_temp.mkdir(mode=0o700)
            repo.mkdir(parents=True, exist_ok=True)
            initialize(repo)
            before = tree_digest(repo)

            preview = run_cli(
                repo,
                "observation", "capture",
                "--title", "Safari cookie 관찰",
                "--summary", "브라우저 통합 fixture에서 재현했다.",
                "--captured-from", "workspace",
                "--attest-reusable-observation",
                "--attest-evidence-present",
                "--sec-observation", "Safari에서 third-party cookie 전달이 차단된다.",
                "--sec-evidence", "integration fixture에서 재현",
                "--json",
                environment={"TMPDIR": str(private_temp)},
            )

            self.assertEqual(0, preview.returncode, preview.stdout + preview.stderr)
            response = json.loads(preview.stdout)
            self.assertFalse(response["applied"])
            self.assertEqual("awaiting_approval", response["state"])
            result = response["result"]
            self.assertEqual(
                {"approval_preview", "approval_digest", "receipt_file", "applied", "state"},
                set(result),
            )
            self.assertFalse(result["applied"])
            self.assertEqual("awaiting_approval", result["state"])
            self.assertEqual(before, tree_digest(repo), "preview must be repository byte-noop")
            receipt_path = Path(result["receipt_file"])
            self.assertEqual((private_temp / "context-core").resolve(), receipt_path.parent)
            self.assertEqual(0o700, stat.S_IMODE(receipt_path.parent.stat().st_mode))
            self.assertEqual(0o600, stat.S_IMODE(receipt_path.stat().st_mode))
            receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
            self.assertEqual(
                {"schema", "status", "created_at", "plan_id", "core", "plan_bundle", "receipt_digest"},
                set(receipt),
            )
            self.assertEqual("context-core-workflow-receipt/v1", receipt["schema"])
            self.assertEqual("pending", receipt["status"])
            self.assertEqual(
                receipt["plan_bundle"]["approval_material"]["plan"]["plan_id"],
                receipt["plan_id"],
            )
            self.assertEqual(f"{receipt['plan_id']}.json", receipt_path.name)
            self.assertEqual(
                {"path": str(CLI_PATH.resolve()), "sha256": context_cli.sha256_bytes(CLI_PATH.read_bytes())},
                receipt["core"],
            )
            receipt_body = dict(receipt)
            receipt_body.pop("receipt_digest")
            self.assertEqual(context_cli.canonical_digest(receipt_body), receipt["receipt_digest"])
            self.assertEqual(
                context_cli.canonical_digest({"core": receipt["core"], "plan_bundle": receipt["plan_bundle"]}),
                result["approval_digest"],
            )
            self.assertNotEqual(receipt["receipt_digest"], result["approval_digest"])

            applied = run_cli(
                repo,
                "transaction", "apply",
                "--receipt-file", str(receipt_path),
                "--approved-digest", result["approval_digest"],
                "--json",
                environment={"TMPDIR": str(private_temp)},
            )
            self.assertEqual(0, applied.returncode, applied.stdout + applied.stderr)
            applied_result = json.loads(applied.stdout)["result"]
            self.assertTrue(applied_result["applied"])
            self.assertFalse(receipt_path.exists())
            artifacts = [
                path for path in (repo / "context/observation").glob("*.md")
                if path.name != "observation.index.md"
            ]
            self.assertEqual(1, len(artifacts))

    def test_preview_command_names_the_non_applied_state_explicitly(self) -> None:
        self.assertEqual(
            {
                "ok": True,
                "applied": False,
                "state": "awaiting_approval",
                "result": {"applied": False, "state": "awaiting_approval"},
            },
            context_cli.schema_result()["preview_success"],
        )
        with tempfile.TemporaryDirectory() as temp:
            repo = Path(temp) / "repository"
            repo.mkdir()
            initialize(repo)
            completed = run_cli(
                repo,
                "observation", "preview",
                "--title", "Preview state",
                "--summary", "Preview does not imply storage.",
                "--captured-from", "workspace",
                "--attest-reusable-observation",
                "--attest-evidence-present",
                "--sec-observation", "Preview 응답만으로 미기록 상태를 판단할 수 있다.",
                "--sec-evidence", "artifact 목록은 preview 직후 비어 있다.",
                "--json",
            )
            self.assertEqual(0, completed.returncode, completed.stdout + completed.stderr)
            response = json.loads(completed.stdout)
            self.assertFalse(response["applied"])
            self.assertEqual("awaiting_approval", response["state"])
            result = response["result"]
            self.assertFalse(result["applied"])
            self.assertEqual("awaiting_approval", result["state"])
            artifacts = [
                path
                for path in (repo / "context/observation").glob("*.md")
                if path.name != "observation.index.md"
            ]
            self.assertEqual([], artifacts)

    def test_human_preview_warns_and_capture_help_marks_the_alias_deprecated(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            repo = Path(temp) / "repository"
            repo.mkdir()
            initialize(repo)
            completed = run_cli(
                repo,
                "observation", "preview",
                "--title", "Human preview state",
                "--summary", "Human output must not imply storage.",
                "--captured-from", "workspace",
                "--attest-reusable-observation",
                "--attest-evidence-present",
                "--sec-observation", "Human output states that the observation is not recorded.",
                "--sec-evidence", "The final output line names the required apply step.",
            )
            self.assertEqual(0, completed.returncode, completed.stdout + completed.stderr)
            self.assertTrue(
                completed.stdout.rstrip().endswith(
                    "Not recorded yet — approval and transaction apply are required."
                )
            )
            alias_help = run_cli(repo, "observation", "capture", "--help")
            self.assertEqual(0, alias_help.returncode, alias_help.stdout + alias_help.stderr)
            self.assertIn("Deprecated alias", alias_help.stdout)
            self.assertIn("does not record the OBS", alias_help.stdout)

    def test_attestation_file_and_observation_flags_are_mutually_exclusive(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            repo = root / "repository"
            private_temp = root / "private-temp"
            repo.mkdir()
            private_temp.mkdir(mode=0o700)
            repo.mkdir(parents=True, exist_ok=True)
            initialize(repo)
            proof = root / "attestation.json"
            proof.write_text("{}", encoding="utf-8")
            before = tree_digest(repo)

            completed = run_cli(
                repo,
                "observation", "capture",
                "--title", "혼용 차단",
                "--summary", "attestation transport는 하나만 선택한다.",
                "--captured-from", "workspace",
                "--attestation", f"@{proof}",
                "--attest-reusable-observation",
                "--attest-evidence-present",
                "--sec-observation", "하나의 transport만 허용한다.",
                "--sec-evidence", "executable contract",
                "--json",
                environment={"TMPDIR": str(private_temp)},
            )

            self.assertNotEqual(0, completed.returncode)
            self.assertEqual(before, tree_digest(repo))
            self.assertFalse((private_temp / "context-core").exists())

    def test_observation_requires_substantive_claim_and_evidence(self) -> None:
        for inputs in (
            {"observation": "TODO", "evidence": ["evidence"]},
            {"observation": "claim", "evidence": []},
            {"observation": "claim", "evidence": ["..."]},
        ):
            with self.subTest(inputs=inputs), self.assertRaises(context_cli.ContextError):
                context_cli.direct_candidate(
                    "observation",
                    title="invalid",
                    summary="invalid observation",
                    captured_from="workspace",
                    owner_inputs=inputs,
                )

    def test_metadata_correction_does_not_change_claim(self) -> None:
        with vault_dir() as temp:
            repo = Path(temp)
            initialize(repo)
            _, identifier = capture(repo, "원래 제목", "같은 claim")
            before = context_cli.observation_read(repo, identifier)
            preview = context_cli.build_observation_annotate_bundle(repo, identifier, title="교정 제목", tags=["corrected"])
            context_cli.apply_bundle(repo, preview["bundle"], preview["approval_digest"])
            after = context_cli.observation_read(repo, identifier)
            self.assertEqual(before["sections"], after["sections"])
            self.assertNotIn("claim_fingerprint", before["artifact"])
            self.assertNotIn("claim_fingerprint", after["artifact"])
            self.assertEqual("교정 제목", after["artifact"]["title"])

    def test_acceptance_14_invalidate(self) -> None:
        with vault_dir() as temp:
            repo = Path(temp)
            initialize(repo)
            _, identifier = capture(repo, "무효화할 관찰")
            preview = context_cli.build_observation_invalidate_bundle(
                repo, identifier, "재현 조건이 반증됨", now="2026-08-13T19:00:00+09:00"
            )
            context_cli.apply_bundle(repo, preview["bundle"], preview["approval_digest"])
            index = context_cli.parse_area_index((repo / "context/observation/observation.index.md").read_text(encoding="utf-8"))
            self.assertEqual([], index.current)
            self.assertEqual("invalidated", index.history[0]["retired_reason"])
            self.assertEqual("2026-08-13T19:00:00+09:00", index.history[0]["retired_at"])
            retired = context_cli.observation_read(repo, identifier)
            self.assertEqual("재현 조건이 반증됨", retired["artifact"]["retirement_note"])
            self.assertEqual("evidence", retired["authority"])

    def test_acceptance_15_supersede(self) -> None:
        with vault_dir() as temp:
            repo = Path(temp)
            initialize(repo)
            _, predecessor = capture(repo, "같은 제목", "Safari cookie claim")
            successor = observation_owner_result("같은 제목", "Safari cookie claim corrected", now="2026-08-13T19:00:00+09:00")
            lifecycle = context_cli.prepare_lifecycle_input(repo, "observation_supersede", predecessor, successor)
            attestation = same_claim_attestation(lifecycle)
            before = tree_digest(repo)
            preview = context_cli.build_observation_supersede_bundle(
                repo,
                predecessor,
                successor,
                lifecycle,
                attestation,
                now="2026-08-13T19:10:00+09:00",
            )
            self.assertEqual(before, tree_digest(repo), "supersede preview must not write")

            tampered = copy.deepcopy(lifecycle)
            tampered["predecessor"]["primary_claim"] += " tampered"
            with self.assertRaises(context_cli.ContextError) as caught:
                context_cli.build_observation_supersede_bundle(repo, predecessor, successor, tampered, attestation)
            self.assertEqual("lifecycle_input_mismatch", caught.exception.code)
            self.assertEqual(before, tree_digest(repo))

            tampered_bundle = copy.deepcopy(preview["bundle"])
            plan = tampered_bundle["approval_material"]["plan"]
            owner_material = next(material for material in tampered_bundle["materials"] if material["path"] is None)
            owner_result = json.loads(owner_material["content"])
            lifecycle_semantic = next(item for item in owner_result["semantic_inputs"] if item["operation"] == "same_claim")
            lifecycle_semantic["value"]["successor"]["primary_claim"] += " tampered"
            lifecycle_semantic["input_digest"] = context_cli.canonical_digest(lifecycle_semantic["value"])
            next(item for item in owner_result["semantic_attestations"] if item["operation"] == "same_claim")["input_digest"] = lifecycle_semantic["input_digest"]
            owner_material["content"] = context_cli.canonical_json(owner_result)
            plan["owner_result_digest"] = context_cli.sha256_bytes(owner_material["content"].encode("utf-8"))
            tampered_bundle["approval_digest"] = context_cli.canonical_digest(tampered_bundle["approval_material"])
            with self.assertRaises(context_cli.ContextError) as apply_error:
                context_cli.apply_bundle(repo, tampered_bundle, tampered_bundle["approval_digest"])
            self.assertEqual("lifecycle_input_mismatch", apply_error.exception.code)
            self.assertEqual(before, tree_digest(repo))

            context_cli.apply_bundle(repo, preview["bundle"], preview["approval_digest"])
            index = context_cli.parse_area_index((repo / "context/observation/observation.index.md").read_text(encoding="utf-8"))
            successor_id = index.current[0]["id"]
            self.assertEqual(predecessor, index.history[0]["id"])
            self.assertEqual(successor_id, index.history[0]["superseded_by"])
            old = context_cli.observation_read(repo, predecessor)["artifact"]
            new = context_cli.observation_read(repo, successor_id)["artifact"]
            self.assertEqual(successor_id, old["superseded_by"])
            self.assertIn(predecessor, new["supersedes"])

    def test_acceptance_16_repeated_supersede(self) -> None:
        with vault_dir() as temp:
            repo = Path(temp)
            initialize(repo)
            _, first = capture(repo, "반복 제목", "claim v1")
            generations = [first]
            current = first
            for number, stamp in ((2, "2026-08-13T19:00:00+09:00"), (3, "2026-08-13T20:00:00+09:00")):
                successor = observation_owner_result("반복 제목", f"claim v{number}", now=stamp)
                lifecycle = context_cli.prepare_lifecycle_input(repo, "observation_supersede", current, successor)
                preview = context_cli.build_observation_supersede_bundle(
                    repo, current, successor, lifecycle, same_claim_attestation(lifecycle), now=stamp
                )
                context_cli.apply_bundle(repo, preview["bundle"], preview["approval_digest"])
                current = next(effect["id"] for effect in preview["approval_preview"]["effects"] if effect["action"] == "create")
                generations.append(current)

            index = context_cli.parse_area_index((repo / "context/observation/observation.index.md").read_text(encoding="utf-8"))
            self.assertEqual(1, len(index.current))
            self.assertEqual(2, len(index.history))
            self.assertEqual(3, len({row["path"] for row in index.current + index.history}))
            for row in index.history:
                self.assertTrue(row["path"].endswith(f"--{row['id'][4:16]}.md"))

    def test_acceptance_17_decision_fallback(self) -> None:
        with vault_dir() as temp:
            repo = Path(temp)
            initialize(repo)
            owner_result, identifier = capture(
                repo,
                "결정처럼 보인 발언",
                "대화에서 BFF가 세션을 소유하기로 합의했다는 진술이 있었다.",
                kind_hint="decision",
            )
            self.assertEqual("observation", owner_result["target_kind"])
            result = context_cli.observation_read(repo, identifier)
            self.assertEqual("decision", result["artifact"]["kind_hint"])
            self.assertEqual("evidence", result["authority"])
            self.assertNotIn("claim_fingerprint", result["artifact"])
            self.assertNotIn("source_claim_fingerprint", result["artifact"])
            self.assertFalse((repo / "context/decision").exists())


if __name__ == "__main__":
    unittest.main()
