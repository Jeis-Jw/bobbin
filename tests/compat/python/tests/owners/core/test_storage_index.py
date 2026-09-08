#!/usr/bin/env python3
from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock


PLUGIN = next(p for p in Path(__file__).resolve().parents if (p / "pytest.ini").is_file()) / "plugins/bobbin"
CLI_PATH = PLUGIN / "skills/context/scripts/context_cli.py"
SPEC = importlib.util.spec_from_file_location("context_cli", CLI_PATH)
assert SPEC and SPEC.loader
context_cli = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = context_cli
SPEC.loader.exec_module(context_cli)


def vault_dir() -> tempfile.TemporaryDirectory[str]:
    temp = tempfile.TemporaryDirectory()
    return temp


def initialize(repo: Path) -> None:
    result = context_cli.build_init_bundle(repo)
    context_cli.apply_bundle(repo, result["bundle"], result["approval_digest"])


def observation(
    identifier: str,
    title: str,
    summary: str,
    created_at: str = "2026-08-13T18:20:00+09:00",
) -> str:
    return context_cli.render_document(
        {
            "schema": "context-observation/v1",
            "id": identifier,
            "title": title,
            "summary": summary,
            "created_at": created_at,
            "captured_from": "workspace",
            "tags": ["auth"],
            "search_terms": ["cookie"],
        },
        {"관찰": title, "근거": "workspace fixture evidence"},
    )


def artifact(
    schema: str,
    identifier: str,
    *,
    title: str = "무결성 fixture",
    extra: dict | None = None,
) -> str:
    frontmatter = {
        "schema": schema,
        "id": identifier,
        "title": title,
        "summary": "strict integrity negative fixture",
        "created_at": "2026-08-13T18:20:00+09:00",
        "captured_from": "workspace",
    }
    if schema == "context-decision/v1":
        frontmatter.update({"scope": "project/auth", "decision_key": "session-owner"})
        sections = {"결정": title, "취지": "현재 저장 계약을 고정한다.", "반려대안": "무결성 검증 생략"}
    else:
        sections = {"관찰": title, "근거": "실제 임시 저장소 fixture"}
    frontmatter.update(extra or {})
    return context_cli.render_document(frontmatter, sections)


def register_decision(repo: Path) -> None:
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
    result = context_cli.build_area_register_bundle(repo, descriptor, seed)
    context_cli.apply_bundle(repo, result["bundle"], result["approval_digest"])
    (repo / "context/decision/retired").mkdir(exist_ok=True)


def refresh_area(repo: Path, area: str) -> None:
    index = repo / f"context/{area}/{area}.index.md"
    index.write_text(context_cli.render_area_index_from_repository(repo, area), encoding="utf-8")


def diagnostic_codes(repo: Path) -> set[str]:
    result = context_cli.refresh_repository(repo)
    return {item["code"] for item in [*result["issues"], *result["warnings"]]}


def with_legacy_field(content: str, field: str) -> str:
    return content.replace(
        'schema: "context-observation/v1"\n',
        f'schema: "context-observation/v1"\n{field}: "sha256:{"0" * 24}"\n',
        1,
    )


class StorageIndexTests(unittest.TestCase):
    def test_acceptance_47_removed_artifact_fields_warn_and_lazy_clean(self) -> None:
        for field in ("claim_fingerprint", "source_claim_fingerprint"):
            with self.subTest(field=field):
                legacy = with_legacy_field(
                    artifact("context-observation/v1", "ctx_550e8400e29b41d4a716446655440000"),
                    field,
                )
                parsed = context_cli.parse_document(legacy)
                self.assertEqual(["schema_removed_field"], [warning["code"] for warning in parsed.warnings])
                self.assertNotIn(field, context_cli.render_document(parsed.frontmatter, parsed.sections))

            candidate = {
                "schema": "context-capture-candidate/v1",
                "candidate_id": "cand_550e8400e29b41d4a716446655440000",
                "title": "구형 후보",
                "claim": "구형 의미 지문 field가 있는 후보",
                "summary": "제거된 field를 조용히 수락하지 않는다.",
                "captured_from": "manual",
                "requested_kind": "observation",
                "specialized_kinds": ["observation"],
                "fallback_kind": None,
                "evidence": ["migration fixture"],
                "owner_inputs": {
                    "observation": {
                        "observation": "구형 의미 지문 field가 있는 후보",
                        "evidence": ["migration fixture"],
                    }
                },
                field: "sha256:" + "0" * 24,
            }
            with self.assertRaises(context_cli.ContextError) as candidate_error:
                context_cli.validate_candidate_batch([candidate], context_cli.capabilities_result())
            self.assertEqual("schema_removed_field", candidate_error.exception.code)

        legacy_candidate = {
            "schema": "context-capture-candidate/v1",
            "candidate_id": "cand_550e8400e29b41d4a716446655440000",
            "claim_key": "legacy-identity",
            "title": "구형 후보",
            "claim": "구형 의미 key가 있는 후보",
            "summary": "제거된 key를 조용히 수락하지 않는다.",
            "captured_from": "manual",
            "requested_kind": "observation",
            "specialized_kinds": ["observation"],
            "fallback_kind": None,
            "evidence": ["migration fixture"],
            "owner_inputs": {
                "observation": {
                    "observation": "구형 의미 key가 있는 후보",
                    "evidence": ["migration fixture"],
                }
            },
        }
        with self.assertRaises(context_cli.ContextError) as candidate_error:
            context_cli.validate_candidate_batch([legacy_candidate], context_cli.capabilities_result())
        self.assertEqual("schema_removed_field", candidate_error.exception.code)

    def test_acceptance_03_natural_filename_and_id(self) -> None:
        self.assertEqual("인증-세션-BFF.md", context_cli.natural_filename(" 인증 세션 / BFF "))
        identifier = context_cli.new_context_id()
        self.assertRegex(identifier, r"^ctx_[0-9a-f]{32}$")
        self.assertTrue(context_cli.is_context_id(identifier))
        self.assertFalse(context_cli.natural_filename("인증 세션").startswith(("OBS-", "DEC-", "SNAP-")))

    def test_acceptance_04_path_collision(self) -> None:
        with vault_dir() as temp:
            repo = Path(temp)
            initialize(repo)
            area = repo / "context/observation"
            (area / "Ａuth.md").write_text("fixture\n", encoding="utf-8")
            with self.assertRaises(context_cli.ContextError) as caught:
                context_cli.resolve_artifact_path(repo, "observation", "Auth.md")
            self.assertEqual("path_exists", caught.exception.code)
            self.assertEqual(["observation.index.md", "retired", "Ａuth.md"], sorted(p.name for p in area.iterdir()))

    def test_acceptance_06_reserved_paths(self) -> None:
        invalid = ["../escape.md", "a/b.md", "a\\b.md", "observation.index.md", "CON.md", "x?.md", "x..md."]
        for value in invalid:
            with self.subTest(value=value), self.assertRaises(context_cli.ContextError):
                context_cli.validate_filename(value)

    def test_acceptance_07_index_determinism(self) -> None:
        with vault_dir() as temp:
            repo = Path(temp)
            initialize(repo)
            path = repo / "context/observation/인증.md"
            path.write_text(observation("ctx_550e8400e29b41d4a716446655440000", "인증 관찰", "cookie 관찰"), encoding="utf-8")
            first = context_cli.render_area_index_from_repository(repo, "observation")
            (repo / "context/observation/observation.index.md").write_text(first, encoding="utf-8")
            second = context_cli.render_area_index_from_repository(repo, "observation")
            self.assertEqual(first.encode(), second.encode())
            self.assertNotIn('"path":"context/observation/observation.index.md"', first)

    def test_acceptance_08_stage1_has_zero_artifact_io(self) -> None:
        with vault_dir() as temp:
            repo = Path(temp)
            initialize(repo)
            for index in range(2):
                identifier = f"ctx_550e8400e29b41d4a71644665544{index:04x}"
                (repo / f"context/observation/관찰-{index}.md").write_text(
                    observation(identifier, f"Cookie 관찰 {index}", f"Safari cookie evidence {index}"), encoding="utf-8"
                )
            index_path = repo / "context/observation/observation.index.md"
            index_path.write_text(context_cli.render_area_index_from_repository(repo, "observation"), encoding="utf-8")
            metrics = context_cli.IOMetrics()
            result = context_cli.recall_repository(repo, query="cookie", metrics=metrics)
            self.assertEqual(2, result["returned"])
            self.assertEqual(0, metrics.artifact_opens)
            self.assertEqual(0, metrics.artifact_directory_lists)
            self.assertEqual(0, metrics.artifact_stats)
            self.assertEqual(3, metrics.index_opens)

    def test_acceptance_09_index_fallback_and_strict(self) -> None:
        with vault_dir() as temp:
            repo = Path(temp)
            initialize(repo)
            artifact = repo / "context/observation/관찰.md"
            artifact.write_text(observation("ctx_550e8400e29b41d4a716446655440000", "관찰", "근거"), encoding="utf-8")
            index_path = repo / "context/observation/observation.index.md"
            valid_seed = index_path.read_text(encoding="utf-8")
            index_path.write_text("broken\n", encoding="utf-8")
            result = context_cli.recall_repository(repo, query="관찰")
            self.assertTrue(result["index_fallback"])
            self.assertEqual(1, result["returned"])
            self.assertIn("area_index_invalid", result["warnings"])
            with self.assertRaises(context_cli.ContextError) as caught:
                context_cli.recall_repository(repo, query="관찰", strict_index=True)
            self.assertEqual(6, caught.exception.exit_code)
            self.assertEqual("index_stale", caught.exception.code)

            index_path.write_text(valid_seed, encoding="utf-8")
            index_path.write_text(context_cli.render_area_index_from_repository(repo, "observation"), encoding="utf-8")
            moved = repo / "context/observation/renamed.md"
            artifact.rename(moved)
            selected = context_cli.recall_repository(
                repo,
                read_ids=["ctx_550e8400e29b41d4a716446655440000"],
            )
            self.assertTrue(selected["index_fallback"])
            self.assertEqual("context/observation/renamed.md", selected["items"][0]["path"])

    def test_acceptance_10_output_limit(self) -> None:
        with vault_dir() as temp:
            repo = Path(temp)
            initialize(repo)
            for index in range(12):
                identifier = f"ctx_550e8400e29b41d4a71644665544{index:04x}"
                (repo / f"context/observation/long-{index}.md").write_text(
                    observation(identifier, f"긴 관찰 {index}", "a" * 120, f"2026-08-13T18:{index:02d}:00+09:00"),
                    encoding="utf-8",
                )
            index_path = repo / "context/observation/observation.index.md"
            index_path.write_text(context_cli.render_area_index_from_repository(repo, "observation"), encoding="utf-8")
            result = context_cli.recall_repository(repo, limit=12, max_bytes=900)
            self.assertTrue(result["truncated"])
            self.assertEqual(12 - result["returned"], result["omitted"])
            for item in result["items"]:
                self.assertEqual({"id", "kind", "state", "title", "summary", "path", "authority", "score"}, set(item))
            self.assertLessEqual(len(context_cli.canonical_json(result["items"]).encode()), 900)

    def test_index_first_artifact_lookup_and_fallback_warning(self) -> None:
        with vault_dir() as temp:
            repo = Path(temp)
            initialize(repo)
            snapshot_id = "ctx_550e8400e29b41d4a716446655440010"
            observation_id = "ctx_550e8400e29b41d4a716446655440011"
            discard_id = "ctx_550e8400e29b41d4a716446655440012"
            snapshot_path = repo / "context/snapshot/handoff.md"
            snapshot_path.write_text(
                context_cli.render_document(
                    {
                        "schema": "context-snapshot/v1",
                        "id": snapshot_id,
                        "title": "index handoff",
                        "summary": "index-first snapshot fixture",
                        "created_at": "2026-08-13T18:20:00+09:00",
                        "captured_from": "workspace",
                        "anchors": [observation_id],
                    },
                    {"현재 맥락": "selected snapshot body", "열린 항목": "open", "다음 단계": "next"},
                ),
                encoding="utf-8",
            )
            observation_path = repo / "context/observation/indexed.md"
            observation_path.write_text(
                observation(observation_id, "indexed observation", "selected observation body"),
                encoding="utf-8",
            )
            (repo / "context/observation/discardable.md").write_text(
                observation(discard_id, "discardable observation", "unanchored observation body"),
                encoding="utf-8",
            )
            refresh_area(repo, "snapshot")
            refresh_area(repo, "observation")

            with (
                mock.patch.object(context_cli, "_scan_area_paths", side_effect=AssertionError("full scan")),
                mock.patch.object(context_cli, "parse_document", wraps=context_cli.parse_document) as parsed,
            ):
                loaded = context_cli.snapshot_load(repo, snapshot_id)
                self.assertEqual(1, parsed.call_count)
                read = context_cli.observation_read(repo, observation_id)
                self.assertEqual(2, parsed.call_count)
            self.assertEqual("selected snapshot body", loaded["sections"]["Current context"])
            self.assertEqual("workspace fixture evidence", read["sections"]["Evidence"])
            self.assertEqual([], loaded["warnings"])
            self.assertEqual([], read["warnings"])

            duplicate_path = repo / "context/observation/unindexed-duplicate.md"
            duplicate_path.write_text(
                observation(observation_id, "duplicate observation", "duplicate body"),
                encoding="utf-8",
            )
            with self.assertRaises(context_cli.ContextError) as duplicate:
                context_cli.build_rename_bundle(repo, observation_id, "renamed.md")
            self.assertEqual("duplicate_id", duplicate.exception.code)
            duplicate_path.unlink()

            index_path = repo / "context/observation/observation.index.md"
            index = context_cli.parse_area_index(index_path.read_text(encoding="utf-8"))
            index_path.write_text(context_cli._replace_block(index.text, "current", []), encoding="utf-8")
            fallback = context_cli.observation_read(repo, observation_id)
            self.assertIn("index_lookup_fallback", fallback["warnings"])
            rename = context_cli.build_rename_bundle(repo, observation_id, "renamed.md")
            discard = context_cli.build_discard_bundle(repo, discard_id)
            self.assertIn("index_lookup_fallback", rename["warnings"])
            self.assertIn("index_lookup_fallback", discard["warnings"])

    def test_recall_ranks_multi_term_matches_and_cuts_path_only_rows(self) -> None:
        with vault_dir() as temp:
            repo = Path(temp)
            initialize(repo)
            relevant_id = "ctx_550e8400e29b41d4a716446655440020"
            partial_id = "ctx_550e8400e29b41d4a716446655440021"
            path_only_id = "ctx_550e8400e29b41d4a716446655440022"
            area = repo / "context/observation"
            (area / "relevant.md").write_text(
                observation(relevant_id, "결제 재시도 정책", "결제 실패를 안전하게 재시도한다"),
                encoding="utf-8",
            )
            (area / "partial.md").write_text(
                observation(partial_id, "재시도 메모", "일반 재시도 기록"),
                encoding="utf-8",
            )
            (area / "결제-재시도-archive.md").write_text(
                observation(path_only_id, "무관 메모", "별도 운영 기록"),
                encoding="utf-8",
            )
            refresh_area(repo, "observation")

            result = context_cli.recall_repository(repo, query="결제 재시도")
            self.assertEqual([relevant_id, partial_id], [item["id"] for item in result["items"]])
            self.assertGreater(result["items"][0]["score"], result["items"][1]["score"])
            filler_query = context_cli.recall_repository(repo, query="결제 재시도 관련 문서 찾아줘")
            self.assertEqual([relevant_id], [item["id"] for item in filler_query["items"]])

    def test_snapshot_load_and_observation_read_enforce_byte_budget(self) -> None:
        with vault_dir() as temp:
            repo = Path(temp)
            initialize(repo)
            snapshot_id = "ctx_550e8400e29b41d4a716446655440030"
            observation_id = "ctx_550e8400e29b41d4a716446655440031"
            (repo / "context/snapshot/large.md").write_text(
                context_cli.render_document(
                    {
                        "schema": "context-snapshot/v1",
                        "id": snapshot_id,
                        "title": "bounded handoff",
                        "summary": "bounded snapshot fixture",
                        "created_at": "2026-08-13T18:20:00+09:00",
                        "captured_from": "workspace",
                    },
                    {"현재 맥락": "가" * 4000, "열린 항목": "open", "다음 단계": "next"},
                ),
                encoding="utf-8",
            )
            (repo / "context/observation/large.md").write_text(
                context_cli.render_document(
                    {
                        "schema": "context-observation/v1",
                        "id": observation_id,
                        "title": "bounded observation",
                        "summary": "bounded observation fixture",
                        "created_at": "2026-08-13T18:20:00+09:00",
                        "captured_from": "workspace",
                    },
                    {"관찰": "나" * 4000, "근거": "runtime fixture"},
                ),
                encoding="utf-8",
            )
            refresh_area(repo, "snapshot")
            refresh_area(repo, "observation")

            for result in (
                context_cli.snapshot_load(repo, snapshot_id, max_bytes=900),
                context_cli.observation_read(repo, observation_id, max_bytes=900),
            ):
                self.assertTrue(result["truncated"])
                self.assertIn("full_read_hint", result)
                self.assertLessEqual(len(context_cli.canonical_json(result).encode("utf-8")), 900)
            self.assertEqual(
                900,
                context_cli.build_parser().parse_args(
                    ["snapshot", "load", "--id", snapshot_id, "--max-bytes", "900"]
                ).max_bytes,
            )
            self.assertEqual(
                900,
                context_cli.build_parser().parse_args(
                    ["observation", "read", "--id", observation_id, "--max-bytes", "900"]
                ).max_bytes,
            )

    def test_acceptance_35_frontmatter_grammar(self) -> None:
        raw = """---
schema: \"context-observation/v1\"
id: \"ctx_550e8400e29b41d4a716446655440000\"
title: \"colon: comma, quote \\\"\"
summary: \"summary\"
created_at: \"2026-08-13T18:20:00+09:00\"
captured_from: \"workspace\"
unknown_z: {\"note\":\"kept\",\"flags\":[\"a\",\"b\"]}
---

## 관찰

claim

## 근거

evidence
"""
        document = context_cli.parse_document(raw)
        rendered = context_cli.render_document(document.frontmatter, document.sections)
        reparsed = context_cli.parse_document(rendered)
        self.assertEqual(document.frontmatter, reparsed.frontmatter)
        self.assertEqual({"note": "kept", "flags": ["a", "b"]}, reparsed.frontmatter["unknown_z"])
        duplicate = raw.replace('title: "colon: comma, quote \\\""', 'title: "one"\ntitle: "two"')
        with self.assertRaises(context_cli.ContextError):
            context_cli.parse_document(duplicate)
        unsupported = raw.replace('summary: "summary"', "summary: 123")
        with self.assertRaises(context_cli.ContextError):
            context_cli.parse_document(unsupported)

    def test_acceptance_39_refresh_reports_index_drift_as_warnings(self) -> None:
        with vault_dir() as temp:
            repo = Path(temp)
            initialize(repo)
            area = repo / "context/observation"
            first = area / "first.md"
            second = area / "second.md"
            first.write_text(observation("ctx_550e8400e29b41d4a716446655440000", "첫 관찰", "first"), encoding="utf-8")
            second.write_text(observation("ctx_550e8400e29b41d4a716446655440001", "둘 관찰", "second"), encoding="utf-8")
            index_path = area / "observation.index.md"
            index_path.write_text(context_cli.render_area_index_from_repository(repo, "observation"), encoding="utf-8")
            first.rename(area / "renamed.md")
            third = area / "third.md"
            third.write_text(observation("ctx_550e8400e29b41d4a716446655440002", "셋 관찰", "third"), encoding="utf-8")
            second.write_text(second.read_text(encoding="utf-8").replace("둘 관찰", "변경된 관찰"), encoding="utf-8")
            result = context_cli.refresh_repository(repo)
            codes = {warning["code"] for warning in result["warnings"]}
            self.assertTrue({"index_ghost_entry", "index_missing_entry", "index_content_drift"}.issubset(codes))
            self.assertTrue(result["ok"])
            self.assertEqual(3, len(list(p for p in area.glob("*.md") if not p.name.endswith(".index.md"))))

    def test_legacy_artifact_does_not_block_doctor_recall_init_or_capture(self) -> None:
        with vault_dir() as temp:
            repo = Path(temp)
            initialize(repo)
            identifier = "ctx_550e8400e29b41d4a716446655440000"
            legacy_path = repo / "context/observation/legacy.md"
            legacy_path.write_text(
                with_legacy_field(
                    artifact("context-observation/v1", identifier, title="legacy searchable observation"),
                    "claim_fingerprint",
                ),
                encoding="utf-8",
            )
            refresh_area(repo, "observation")

            doctor = context_cli.doctor_repository(repo)
            self.assertEqual("ready", doctor["repository_state"])
            self.assertIn("schema_removed_field", {warning["code"] for warning in doctor["warnings"]})
            recalled = context_cli.recall_repository(repo, query="legacy searchable observation", pack=True)
            self.assertEqual([identifier], [item["id"] for item in recalled["items"]])
            self.assertIn("schema_removed_field", recalled["warnings"])

            initialized = context_cli.bootstrap_repository(repo, host="codex")
            self.assertEqual("ready", initialized["doctor"]["repository_state"])

            candidate = context_cli.direct_candidate(
                "observation",
                title="new capture",
                summary="legacy sibling must not block this capture",
                captured_from="workspace",
                owner_inputs={"observation": "new reusable claim", "evidence": ["temp repo runtime fixture"]},
            )
            attestation = {
                "schema": "context-semantic-attestation/v1",
                "operation": "claim",
                "input_schema": candidate["schema"],
                "input_digest": context_cli.canonical_digest(candidate),
                "assertions": [
                    {"name": "reusable_observation", "value": True, "evidence_pointers": ["/owner_inputs/observation/observation"]},
                    {"name": "evidence_present", "value": True, "evidence_pointers": ["/owner_inputs/observation/evidence/0"]},
                ],
            }
            capture = context_cli.build_observation_capture_bundle(repo, candidate, attestation)
            context_cli.apply_bundle(repo, capture["bundle"], capture["approval_digest"])

            cleanup = context_cli.build_observation_annotate_bundle(repo, identifier, summary="legacy field lazy-cleaned")
            context_cli.apply_bundle(repo, cleanup["bundle"], cleanup["approval_digest"])
            self.assertNotIn("claim_fingerprint", legacy_path.read_text(encoding="utf-8"))
            self.assertEqual([], context_cli.doctor_repository(repo)["warnings"])

    def test_missing_index_row_falls_back_and_repairs_in_one_call(self) -> None:
        with vault_dir() as temp:
            repo = Path(temp)
            initialize(repo)
            identifier = "ctx_550e8400e29b41d4a716446655440000"
            artifact_path = repo / "context/observation/index-miss.md"
            artifact_path.write_text(
                artifact("context-observation/v1", identifier, title="index miss recovery"),
                encoding="utf-8",
            )
            refresh_area(repo, "observation")
            index_path = repo / "context/observation/observation.index.md"
            index_path.write_text(
                "\n".join(line for line in index_path.read_text(encoding="utf-8").splitlines() if "<!-- context-entry " not in line) + "\n",
                encoding="utf-8",
            )
            artifact_before = artifact_path.read_bytes()

            metrics = context_cli.IOMetrics()
            recalled = context_cli.recall_repository(repo, query="index miss recovery", metrics=metrics)
            self.assertTrue(recalled["index_fallback"])
            self.assertIn("index_miss_fallback", recalled["warnings"])
            self.assertEqual([identifier], [item["id"] for item in recalled["items"]])
            self.assertEqual(1, metrics.artifact_opens)
            doctor = context_cli.doctor_repository(repo)
            self.assertEqual("ready", doctor["repository_state"])
            self.assertIn("index_missing_entry", {warning["code"] for warning in doctor["warnings"]})

            repaired = context_cli.repair_derived_indexes(repo)
            self.assertTrue(repaired["applied"])
            self.assertEqual([], repaired["warnings"])
            self.assertEqual("ready", context_cli.doctor_repository(repo)["repository_state"])
            self.assertEqual(artifact_before, artifact_path.read_bytes())

    def test_index_miss_body_reads_have_a_hard_cap(self) -> None:
        with vault_dir() as temp:
            repo = Path(temp)
            initialize(repo)
            for index in range(25):
                identifier = f"ctx_550e8400e29b41d4a71644665544{index:04x}"
                (repo / f"context/observation/unindexed-{index:02d}.md").write_text(
                    observation(identifier, f"unindexed recovery {index}", "unindexed recovery fixture"),
                    encoding="utf-8",
                )
            metrics = context_cli.IOMetrics()
            recalled = context_cli.recall_repository(repo, query="unindexed recovery", metrics=metrics)
            self.assertTrue(recalled["index_fallback"])
            self.assertIn("index_miss_fallback", recalled["warnings"])
            self.assertIn("index_miss_fallback_truncated", recalled["warnings"])
            self.assertEqual(20, metrics.artifact_opens)
            self.assertEqual(20, recalled["returned"] + recalled["omitted"])

            (repo / "context/observation/observation.index.md").write_text("broken\n", encoding="utf-8")
            broken_metrics = context_cli.IOMetrics()
            broken = context_cli.recall_repository(repo, query="unindexed recovery", metrics=broken_metrics)
            self.assertTrue(broken["index_fallback"])
            self.assertIn("area_index_invalid", broken["warnings"])
            self.assertIn("index_fallback_truncated", broken["warnings"])
            self.assertEqual(20, broken_metrics.artifact_opens)

    def test_missing_at_file_uses_structured_error_envelope(self) -> None:
        with vault_dir() as temp:
            completed = subprocess.run(
                [
                    "python3", str(CLI_PATH), "draft", "--kind", "observation",
                    "--candidate", "@does-not-exist.json", "--attestation", "@does-not-exist.json", "--json",
                ],
                cwd=temp,
                text=True,
                capture_output=True,
            )
            self.assertEqual(3, completed.returncode)
            self.assertEqual("", completed.stderr)
            envelope = json.loads(completed.stdout)
            self.assertEqual("input_unavailable", envelope["error"]["code"])

    def test_context_root_missing_is_storage_error(self) -> None:
        with vault_dir() as temp:
            with self.assertRaises(context_cli.ContextError) as caught:
                context_cli.recall_repository(Path(temp))
            self.assertEqual("context_root_missing", caught.exception.code)
            self.assertEqual(3, caught.exception.exit_code)


class StrictIntegrityTests(unittest.TestCase):
    def test_group_01_reserved_index_paths_and_self_entry(self) -> None:
        with vault_dir() as temp:
            repo = Path(temp)
            initialize(repo)
            root = repo / context_cli.ROOT_INDEX
            root.write_text(
                root.read_text(encoding="utf-8").replace(
                    '"path":"context/observation/observation.index.md"',
                    '"path":"context/observation/not-canonical.index.md"',
                ),
                encoding="utf-8",
            )
            self.assertIn("reserved_index_path", diagnostic_codes(repo))

        with vault_dir() as temp:
            repo = Path(temp)
            initialize(repo)
            root = repo / context_cli.ROOT_INDEX
            lines = [line for line in root.read_text(encoding="utf-8").splitlines() if '"area":"observation"' not in line]
            root.write_text("\n".join(lines) + "\n", encoding="utf-8")
            self.assertIn("reserved_index_missing", diagnostic_codes(repo))

        with vault_dir() as temp:
            repo = Path(temp)
            initialize(repo)
            index = repo / "context/observation/observation.index.md"
            row = {
                "id": "ctx_550e8400e29b41d4a716446655440000",
                "path": "context/observation/observation.index.md",
                "title": "self",
                "summary": "reserved index cannot be an artifact",
                "state": "current",
                "created_at": "2026-08-13T18:20:00+09:00",
                "terms": [],
            }
            index.write_text(
                index.read_text(encoding="utf-8").replace(
                    "<!-- BEGIN CONTEXT GENERATED:current -->\n",
                    "<!-- BEGIN CONTEXT GENERATED:current -->\n" + context_cli._entry_row(row) + "\n",
                ),
                encoding="utf-8",
            )
            self.assertIn("index_self_entry", diagnostic_codes(repo))

    def test_group_02_markers_canonical_json_and_root_bytes(self) -> None:
        fixtures = (
            (
                "index_marker_invalid",
                lambda text: text.replace(
                    "<!-- BEGIN CONTEXT GENERATED:current -->",
                    "<!-- BEGIN CONTEXT GENERATED:current -->\n<!-- BEGIN CONTEXT GENERATED:current -->",
                ),
                "context/observation/observation.index.md",
            ),
            (
                "index_noncanonical",
                lambda text: text.replace("<!-- context-area {", "<!-- context-area { ", 1),
                context_cli.ROOT_INDEX,
            ),
            (
                "root_index_drift",
                lambda text: text.replace("Snapshot: session handoff staging", "Snapshot: tampered summary"),
                context_cli.ROOT_INDEX,
            ),
        )
        for expected, mutate, relative in fixtures:
            with self.subTest(expected=expected), vault_dir() as temp:
                repo = Path(temp)
                initialize(repo)
                path = repo / relative
                path.write_text(mutate(path.read_text(encoding="utf-8")), encoding="utf-8")
                self.assertIn(expected, diagnostic_codes(repo))

    def test_group_03_schema_area_path_fields_and_sections(self) -> None:
        cases: list[tuple[str, str]] = []
        valid_observation = artifact("context-observation/v1", "ctx_550e8400e29b41d4a716446655440000")
        cases.append(("schema_invalid", valid_observation.replace('summary: "strict integrity negative fixture"\n', "")))
        cases.append(("section_schema_error", valid_observation.replace("## 근거\n\n실제 임시 저장소 fixture\n", "")))
        cases.append(("schema_area_mismatch", artifact("context-decision/v1", "ctx_550e8400e29b41d4a716446655440001")))
        for expected, content in cases:
            with self.subTest(expected=expected), vault_dir() as temp:
                repo = Path(temp)
                initialize(repo)
                (repo / "context/observation/fixture.md").write_text(content, encoding="utf-8")
                self.assertIn(expected, diagnostic_codes(repo))

        with vault_dir() as temp:
            repo = Path(temp)
            initialize(repo)
            index = repo / "context/observation/observation.index.md"
            index.write_text(index.read_text(encoding="utf-8").replace('owner: "context-core"', 'owner: "other"'), encoding="utf-8")
            self.assertIn("area_index_mismatch", diagnostic_codes(repo))

    def test_group_04_duplicate_id(self) -> None:
        with vault_dir() as temp:
            repo = Path(temp)
            initialize(repo)
            identifier = "ctx_550e8400e29b41d4a716446655440000"
            for name in ("one.md", "two.md"):
                (repo / f"context/observation/{name}").write_text(artifact("context-observation/v1", identifier), encoding="utf-8")
            refresh_area(repo, "observation")
            self.assertIn("duplicate_id", diagnostic_codes(repo))

    def test_group_05_broken_internal_reference(self) -> None:
        with vault_dir() as temp:
            repo = Path(temp)
            initialize(repo)
            content = artifact(
                "context-observation/v1",
                "ctx_550e8400e29b41d4a716446655440000",
                extra={"relations": {"related": ["ctx_123e4567e89b42d3a456426614174000"]}},
            )
            (repo / "context/observation/ref.md").write_text(content, encoding="utf-8")
            refresh_area(repo, "observation")
            self.assertIn("broken_internal_ref", diagnostic_codes(repo))

    def test_group_06_lifecycle_state_and_reason_metadata(self) -> None:
        fixtures = (
            (
                "context/observation/active.md",
                artifact(
                    "context-observation/v1",
                    "ctx_550e8400e29b41d4a716446655440000",
                    extra={
                        "retired_at": "2026-08-14T09:00:00+09:00",
                        "retired_reason": "invalidated",
                        "retirement_note": "반증됨",
                    },
                ),
            ),
            (
                "context/observation/retired/missing-time.md",
                artifact(
                    "context-observation/v1",
                    "ctx_550e8400e29b41d4a716446655440001",
                    extra={"retired_reason": "invalidated", "retirement_note": "반증됨"},
                ),
            ),
        )
        for relative, content in fixtures:
            with self.subTest(relative=relative), vault_dir() as temp:
                repo = Path(temp)
                initialize(repo)
                (repo / relative).write_text(content, encoding="utf-8")
                self.assertIn("lifecycle_invalid", diagnostic_codes(repo))

        with vault_dir() as temp:
            repo = Path(temp)
            initialize(repo)
            register_decision(repo)
            content = artifact(
                "context-decision/v1",
                "ctx_550e8400e29b41d4a716446655440002",
                extra={
                    "retired_at": "2026-08-14T09:00:00+09:00",
                    "retired_reason": "withdrawn",
                },
            )
            (repo / "context/decision/retired/withdrawn.md").write_text(content, encoding="utf-8")
            self.assertIn("lifecycle_invalid", diagnostic_codes(repo))
            before = (repo / "context/decision/retired/withdrawn.md").read_bytes()
            repair = context_cli.repair_derived_indexes(repo)
            self.assertIn("lifecycle_invalid", {issue["code"] for issue in repair["issues"]})
            self.assertEqual(before, (repo / "context/decision/retired/withdrawn.md").read_bytes())

    def test_group_07_reciprocal_supersede_edges(self) -> None:
        with vault_dir() as temp:
            repo = Path(temp)
            initialize(repo)
            predecessor = "ctx_550e8400e29b41d4a716446655440000"
            successor = "ctx_550e8400e29b41d4a716446655440001"
            (repo / "context/observation/retired/old.md").write_text(
                artifact(
                    "context-observation/v1",
                    predecessor,
                    extra={
                        "retired_at": "2026-08-14T09:00:00+09:00",
                        "retired_reason": "superseded",
                        "superseded_by": successor,
                    },
                ),
                encoding="utf-8",
            )
            (repo / "context/observation/new.md").write_text(artifact("context-observation/v1", successor), encoding="utf-8")
            refresh_area(repo, "observation")
            self.assertIn("supersede_edge_missing", diagnostic_codes(repo))

    def test_group_08_lifecycle_cycle(self) -> None:
        with vault_dir() as temp:
            repo = Path(temp)
            initialize(repo)
            first = "ctx_550e8400e29b41d4a716446655440000"
            second = "ctx_550e8400e29b41d4a716446655440001"
            for name, identifier, successor in (("first", first, second), ("second", second, first)):
                (repo / f"context/observation/retired/{name}.md").write_text(
                    artifact(
                        "context-observation/v1",
                        identifier,
                        extra={
                            "retired_at": "2026-08-14T09:00:00+09:00",
                            "retired_reason": "superseded",
                            "superseded_by": successor,
                            "supersedes": [successor],
                        },
                    ),
                    encoding="utf-8",
                )
            refresh_area(repo, "observation")
            self.assertIn("lifecycle_cycle", diagnostic_codes(repo))
            repair = context_cli.repair_derived_indexes(repo)
            self.assertIn("lifecycle_cycle", {issue["code"] for issue in repair["issues"]})

    def test_group_09_illegal_cross_kind_predecessor(self) -> None:
        with vault_dir() as temp:
            repo = Path(temp)
            initialize(repo)
            register_decision(repo)
            predecessor = "ctx_550e8400e29b41d4a716446655440000"
            successor = "ctx_550e8400e29b41d4a716446655440001"
            (repo / "context/observation/retired/old.md").write_text(
                artifact(
                    "context-observation/v1",
                    predecessor,
                    extra={
                        "retired_at": "2026-08-14T09:00:00+09:00",
                        "retired_reason": "superseded",
                        "superseded_by": successor,
                    },
                ),
                encoding="utf-8",
            )
            (repo / "context/decision/new.md").write_text(
                artifact("context-decision/v1", successor, extra={"supersedes": [predecessor]}),
                encoding="utf-8",
            )
            refresh_area(repo, "observation")
            refresh_area(repo, "decision")
            self.assertIn("illegal_cross_kind_predecessor", diagnostic_codes(repo))

    def test_group_10_duplicate_current_decision_slot(self) -> None:
        with vault_dir() as temp:
            repo = Path(temp)
            initialize(repo)
            register_decision(repo)
            for name, identifier in (
                ("one", "ctx_550e8400e29b41d4a716446655440000"),
                ("two", "ctx_550e8400e29b41d4a716446655440001"),
            ):
                (repo / f"context/decision/{name}.md").write_text(artifact("context-decision/v1", identifier), encoding="utf-8")
            refresh_area(repo, "decision")
            self.assertIn("duplicate_current_slot", diagnostic_codes(repo))

    def test_group_11_index_duplicate_and_wrong_state(self) -> None:
        for expected, mutate in (
            (
                "index_duplicate_entry",
                lambda row: row + "\n" + row,
            ),
            (
                "index_wrong_state",
                lambda row: row.replace('"state":"current"', '"state":"history"'),
            ),
        ):
            with self.subTest(expected=expected), vault_dir() as temp:
                repo = Path(temp)
                initialize(repo)
                (repo / "context/observation/one.md").write_text(
                    artifact("context-observation/v1", "ctx_550e8400e29b41d4a716446655440000"), encoding="utf-8"
                )
                refresh_area(repo, "observation")
                index = repo / "context/observation/observation.index.md"
                lines = index.read_text(encoding="utf-8").splitlines()
                position = next(i for i, line in enumerate(lines) if "<!-- context-entry " in line)
                lines[position] = mutate(lines[position])
                index.write_text("\n".join(lines) + "\n", encoding="utf-8")
                self.assertIn(expected, diagnostic_codes(repo))
                document_before = (repo / "context/observation/one.md").read_bytes()
                repair = context_cli.repair_derived_indexes(repo)
                self.assertTrue(repair["applied"])
                self.assertTrue(context_cli.refresh_repository(repo)["ok"])
                self.assertEqual(document_before, (repo / "context/observation/one.md").read_bytes())

    def test_group_12_duplicate_area_and_claim_owner(self) -> None:
        with vault_dir() as temp:
            repo = Path(temp)
            initialize(repo)
            root = repo / context_cli.ROOT_INDEX
            lines = root.read_text(encoding="utf-8").splitlines()
            position = next(i for i, line in enumerate(lines) if '"area":"observation"' in line)
            lines.insert(position, lines[position])
            root.write_text("\n".join(lines) + "\n", encoding="utf-8")
            codes = diagnostic_codes(repo)
            self.assertTrue({"duplicate_area_owner", "duplicate_claim_owner"}.issubset(codes))

    @unittest.skipIf(sys.platform == "win32", "POSIX symlink integrity fixture")
    def test_group_13_traversal_and_retired_symlink(self) -> None:
        with vault_dir() as temp:
            repo = Path(temp)
            initialize(repo)
            root = repo / context_cli.ROOT_INDEX
            root.write_text(
                root.read_text(encoding="utf-8").replace(
                    '"path":"context/observation/observation.index.md"',
                    '"path":"context/../outside.index.md"',
                ),
                encoding="utf-8",
            )
            self.assertIn("path_escape", diagnostic_codes(repo))

        with vault_dir() as temp, tempfile.TemporaryDirectory() as outside_temp:
            repo = Path(temp)
            initialize(repo)
            retired = repo / "context/observation/retired"
            retired.rmdir()
            outside = Path(outside_temp)
            (outside / "escaped.md").write_text("must not be read\n", encoding="utf-8")
            retired.symlink_to(outside, target_is_directory=True)
            codes = diagnostic_codes(repo)
            self.assertIn("symlink_path", codes)
            repair = context_cli.repair_derived_indexes(repo)
            self.assertIn("symlink_path", {issue["code"] for issue in repair["issues"]})

    def test_refresh_cli_reports_blocking_issue_without_strict_mode(self) -> None:
        with vault_dir() as temp:
            repo = Path(temp)
            initialize(repo)
            content = artifact(
                "context-observation/v1",
                "ctx_550e8400e29b41d4a716446655440000",
                extra={"relations": {"related": ["ctx_123e4567e89b42d3a456426614174000"]}},
            )
            (repo / "context/observation/ref.md").write_text(content, encoding="utf-8")
            refresh_area(repo, "observation")
            completed = subprocess.run(
                ["python3", str(CLI_PATH), "refresh", "--json"],
                cwd=repo,
                text=True,
                capture_output=True,
            )
            self.assertEqual(0, completed.returncode, completed.stdout + completed.stderr)
            envelope = json.loads(completed.stdout)
            self.assertFalse(envelope["result"]["ok"])
            codes = {issue["code"] for issue in envelope["result"]["issues"]}
            self.assertIn("broken_internal_ref", codes)


if __name__ == "__main__":
    unittest.main()
