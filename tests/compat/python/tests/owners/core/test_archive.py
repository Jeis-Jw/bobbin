#!/usr/bin/env python3
from __future__ import annotations

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
SPEC = importlib.util.spec_from_file_location("context_cli_archive", CLI_PATH)
assert SPEC and SPEC.loader
context_cli = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = context_cli
SPEC.loader.exec_module(context_cli)


def initialize(repo: Path) -> None:
    preview = context_cli.build_init_bundle(repo)
    context_cli.apply_bundle(repo, preview["bundle"], preview["approval_digest"])


def run_cli(repo: Path, *arguments: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(CLI_PATH), "--vault", str(repo), *arguments],
        cwd=repo,
        text=True,
        capture_output=True,
    )


def archive_attestation(candidate: dict) -> dict:
    return {
        "schema": "context-semantic-attestation/v1",
        "operation": "claim",
        "input_schema": candidate["schema"],
        "input_digest": context_cli.canonical_digest(candidate),
        "assertions": [
            {
                "name": "source_adopted_as_evidence",
                "value": True,
                "evidence_pointers": ["/source_refs/0"],
            },
            {
                "name": "immutable_original_present",
                "value": True,
                "evidence_pointers": ["/owner_inputs/archive/content"],
            },
        ],
    }


def apply_archive(
    repo: Path,
    *,
    identifier: str = "ctx_550e8400e29b41d4a716446655440070",
    content: str = "시점 고정 리서치 원문",
) -> dict:
    candidate = context_cli.direct_candidate(
        "archive",
        title="리서치 원문",
        summary="판단 근거로 채택된 시점 고정 원문이다.",
        captured_from="import",
        owner_inputs={"content": content},
        source_refs=["docs/research-source.md"],
        search_terms=["research", "source"],
    )
    owner_result = context_cli.draft_owner_result(
        candidate,
        archive_attestation(candidate),
        identifier=identifier,
        now="2026-09-01T10:00:00+09:00",
    )
    preview = context_cli.finalize_owner_result(repo, owner_result)
    context_cli.apply_bundle(repo, preview["bundle"], preview["approval_digest"])
    return {"candidate": candidate, "owner_result": owner_result, "preview": preview}


def observation_attestation(candidate: dict) -> dict:
    return {
        "schema": "context-semantic-attestation/v1",
        "operation": "claim",
        "input_schema": candidate["schema"],
        "input_digest": context_cli.canonical_digest(candidate),
        "assertions": [
            {
                "name": "reusable_observation",
                "value": True,
                "evidence_pointers": ["/owner_inputs/observation/observation"],
            },
            {
                "name": "evidence_present",
                "value": True,
                "evidence_pointers": ["/owner_inputs/observation/evidence/0"],
            },
        ],
    }


class ArchiveTests(unittest.TestCase):
    def test_archive_preview_apply_read_search_and_default_recall_exclusion(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            repo = Path(temp) / "vault"
            repo.mkdir()
            initialize(repo)
            content = ("시점 고정 리서치 원문 문단이다.\n" * 80).strip()
            source = Path(temp) / "research.md"
            source.write_text(content, encoding="utf-8")
            receipt = Path(temp) / "archive-receipt.json"

            preview = run_cli(
                repo,
                "archive", "preview",
                "--title", "리서치 원문",
                "--summary", "판단 근거로 채택된 시점 고정 원문이다.",
                "--captured-from", "import",
                "--content", f"@{source}",
                "--source-ref", "docs/research.md",
                "--search-term", "research",
                "--attest-source-adopted",
                "--attest-immutable-original",
                "--receipt-file", str(receipt),
                "--json",
            )
            self.assertEqual(0, preview.returncode, preview.stdout + preview.stderr)
            envelope = json.loads(preview.stdout)
            self.assertFalse(envelope["applied"])
            self.assertEqual("awaiting_approval", envelope["state"])
            self.assertEqual([], [path for path in (repo / "context/archive").glob("*.md") if not path.name.endswith(".index.md")])

            result = envelope["result"]
            applied = run_cli(
                repo,
                "transaction", "apply",
                "--receipt-file", str(receipt),
                "--approved-digest", result["approval_digest"],
                "--json",
            )
            self.assertEqual(0, applied.returncode, applied.stdout + applied.stderr)
            self.assertTrue(json.loads(applied.stdout)["result"]["applied"])

            archive_id = result["approval_preview"]["effects"][0]["id"]
            read = run_cli(repo, "archive", "read", "--id", archive_id, "--json")
            self.assertEqual(0, read.returncode, read.stdout + read.stderr)
            self.assertEqual(content, json.loads(read.stdout)["result"]["sections"]["Content"])
            self.assertEqual([], context_cli.recall_repository(repo, query="research")["items"])
            included = context_cli.recall_repository(repo, query="research", include_archive=True)
            self.assertEqual([archive_id], [item["id"] for item in included["items"]])
            searched = run_cli(repo, "archive", "search", "--query", "research", "--json")
            self.assertEqual([archive_id], [item["id"] for item in json.loads(searched.stdout)["result"]["items"]])

            with self.assertRaises(context_cli.ContextError) as rename_error:
                context_cli.build_rename_bundle(repo, archive_id, "renamed.md")
            self.assertEqual("lifecycle_unsupported", rename_error.exception.code)

    def test_archive_limit_and_observation_evidence_reference_integrity(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            repo = Path(temp) / "vault"
            repo.mkdir()
            initialize(repo)
            archive_id = "ctx_550e8400e29b41d4a716446655440070"
            apply_archive(repo, identifier=archive_id, content="원문" * 800)

            accepted = context_cli.direct_candidate(
                "archive",
                title="최대 원문",
                summary="장문 archive 상한 fixture다.",
                captured_from="import",
                owner_inputs={"content": "x" * 65_000},
                source_refs=["source:max"],
            )
            self.assertEqual(65_000, len(accepted["claim"]))
            with self.assertRaises(context_cli.ContextError) as oversized:
                context_cli.direct_candidate(
                    "archive",
                    title="초과 원문",
                    summary="장문 archive 상한 초과 fixture다.",
                    captured_from="import",
                    owner_inputs={"content": "x" * 65_001},
                    source_refs=["source:over"],
                )
            self.assertEqual("candidate_invalid", oversized.exception.code)

            observation = context_cli.direct_candidate(
                "observation",
                title="원문 기반 관찰",
                summary="archive 원문을 직접 근거로 참조한다.",
                captured_from="workspace",
                owner_inputs={"observation": "원문의 조사 결과를 재사용한다.", "evidence": [archive_id]},
            )
            owner_result = context_cli.draft_owner_result(
                observation,
                observation_attestation(observation),
                identifier="ctx_550e8400e29b41d4a716446655440071",
                now="2026-09-01T10:01:00+09:00",
            )
            preview = context_cli.finalize_owner_result(repo, owner_result)
            context_cli.apply_bundle(repo, preview["bundle"], preview["approval_digest"])
            self.assertTrue(context_cli.refresh_repository(repo)["ok"])

            with self.assertRaises(context_cli.ContextError) as inbound:
                context_cli.build_archive_discard_bundle(repo, archive_id)
            self.assertEqual("inbound_reference", inbound.exception.code)

            actual = next(path for path in (repo / "context/archive").glob("*.md") if path.name != "archive.index.md")
            actual.unlink()
            broken = context_cli.refresh_repository(repo)
            self.assertIn(
                ("broken_internal_ref", archive_id),
                {(issue.get("code"), issue.get("target")) for issue in broken["issues"]},
            )

    def test_explicit_init_additively_registers_archive_in_a_pre_archive_vault(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            repo = Path(temp) / "vault"
            repo.mkdir()
            initialize(repo)
            observation = repo / "context/observation/preserved.md"
            observation.write_text(
                context_cli.render_document(
                    {
                        "schema": "context-observation/v1",
                        "id": "ctx_550e8400e29b41d4a716446655440072",
                        "title": "보존 관찰",
                        "summary": "archive area 추가 전 byte 보존 fixture다.",
                        "created_at": "2026-09-01T09:00:00+09:00",
                        "captured_from": "workspace",
                    },
                    {"Observation": "기존 artifact는 유지된다.", "Evidence": "- fixture"},
                ),
                encoding="utf-8",
            )
            observation_index = repo / "context/observation/observation.index.md"
            observation_index.write_text(context_cli.render_area_index_from_repository(repo, "observation"), encoding="utf-8")
            preserved = observation.read_bytes()

            old_specs = context_cli._builtin_area_specs()[:2]
            root = repo / context_cli.ROOT_INDEX
            root.write_text(context_cli.render_root_index(context_cli._root_seed(), old_specs), encoding="utf-8")
            shutil.rmtree(repo / "context/archive")

            migration = context_cli.build_init_bundle(repo)
            self.assertEqual("area_register", migration["bundle"]["approval_material"]["plan"]["transition"])
            context_cli.apply_bundle(repo, migration["bundle"], migration["approval_digest"], approval_source="explicit_init")
            self.assertEqual(preserved, observation.read_bytes())
            self.assertTrue((repo / "context/archive/archive.index.md").is_file())
            self.assertTrue(context_cli.refresh_repository(repo)["ok"])


if __name__ == "__main__":
    unittest.main()
