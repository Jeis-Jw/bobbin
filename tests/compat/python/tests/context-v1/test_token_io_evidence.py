#!/usr/bin/env python3
from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = next(p for p in Path(__file__).resolve().parents if (p / "pytest.ini").is_file())
CLI_PATH = ROOT / "plugins/bobbin/skills/context/scripts/context_cli.py"
SPEC = importlib.util.spec_from_file_location("context_cli_token", CLI_PATH)
assert SPEC and SPEC.loader
context_cli = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = context_cli
SPEC.loader.exec_module(context_cli)


def encoded(value: object) -> int:
    return len(context_cli.canonical_json(value).encode("utf-8"))


def candidate(number: int) -> dict:
    return {
        "schema": "context-capture-candidate/v1",
        "candidate_id": f"cand_550e8400e29b41d4a71644665544{number:04x}",
        "title": f"관찰 {number}",
        "claim": f"재사용 가능한 관찰 {number}",
        "summary": f"후속 판단에 쓰는 근거 {number}",
        "captured_from": "conversation",
        "requested_kind": "observation",
        "specialized_kinds": ["observation"],
        "fallback_kind": None,
        "owner_inputs": {
            "observation": {
                "observation": f"재사용 가능한 관찰 {number}",
                "evidence": [f"fixture {number}"],
            }
        },
    }


def opaque_owner_input(size: int) -> dict:
    value = {"payload": ""}
    remaining = size - encoded(value)
    if remaining < 0:
        raise AssertionError(f"unrepresentable owner input boundary: {size}")
    value["payload"] = "x" * remaining
    assert encoded(value) == size
    return value


class SyntheticRepository:
    CURRENT_COUNTS = {"snapshot": 100, "observation": 2000, "decision": 2000}
    HISTORY_COUNTS = {"snapshot": 0, "observation": 500, "decision": 500}
    AUTHORITIES = {"snapshot": "staging", "observation": "evidence", "decision": "authoritative"}
    SCHEMAS = {
        "snapshot": "context-snapshot/v1",
        "observation": "context-observation/v1",
        "decision": "context-decision/v1",
    }
    OWNERS = {"snapshot": "context-core", "observation": "context-core", "decision": "context-decision"}

    def __init__(self, root: Path):
        self.root = root
        self._build()

    @staticmethod
    def _identifier(area: str, state: str, number: int) -> str:
        prefix = {
            ("snapshot", "current"): 1,
            ("observation", "current"): 2,
            ("observation", "history"): 3,
            ("decision", "current"): 4,
            ("decision", "history"): 5,
        }[(area, state)]
        value = list(f"{prefix:02x}{number:030x}")
        value[12] = "4"
        value[16] = "8"
        return "ctx_" + "".join(value)

    @staticmethod
    def _probe(number: int) -> str:
        return f"probe{number:04d}" if number < 100 else f"record{number:04d}"

    def _document(self, area: str, state: str, number: int) -> str:
        probe = self._probe(number)
        identifier = self._identifier(area, state, number)
        frontmatter = {
            "schema": self.SCHEMAS[area],
            "id": identifier,
            "title": f"{area} {state} {probe}",
            "summary": f"{probe} " + "s" * 240,
            "created_at": "2026-08-13T12:00:00+09:00",
            "captured_from": "workspace",
            "search_terms": [probe],
        }
        if state == "history":
            frontmatter.update(
                retired_at="2026-08-14T12:00:00+09:00",
                retired_reason="invalidated" if area == "observation" else "withdrawn",
            )
            if area == "observation":
                frontmatter["retirement_note"] = "synthetic history fixture"
        if area == "snapshot":
            sections = {
                "현재 맥락": f"{probe} handoff context",
                "열린 항목": "- synthetic item",
                "다음 단계": "- synthetic next step",
            }
        elif area == "observation":
            sections = {"관찰": f"{probe} observation", "근거": "- deterministic fixture"}
        else:
            frontmatter.update(
                scope="project/synthetic",
                decision_key=f"slot-{state}-{number:04d}",
            )
            rationale = "r" * 3000 if state == "current" and number == 0 else f"{probe} rationale"
            sections = {"결정": f"{probe} decision", "취지": rationale, "반려대안": "synthetic alternative"}
        return context_cli.render_document(frontmatter, sections)

    def _build(self) -> None:
        area_rows = []
        for area in ("snapshot", "observation", "decision"):
            area_root = self.root / "context" / area
            area_root.mkdir(parents=True)
            if self.HISTORY_COUNTS[area]:
                (area_root / "retired").mkdir()
            projection_fields = ("scope", "decision_key") if area == "decision" else ()
            seed = context_cli._area_seed(
                area,
                self.OWNERS[area],
                self.SCHEMAS[area],
                self.AUTHORITIES[area],
                f"Synthetic {area} corpus",
                search_terms=(area,),
                projection_fields=projection_fields,
            )
            metadata = {
                "artifact_schema": self.SCHEMAS[area],
                "projection_fields": list(projection_fields),
            }
            rows = {"current": [], "history": []}
            for state, count in (("current", self.CURRENT_COUNTS[area]), ("history", self.HISTORY_COUNTS[area])):
                parent = area_root if state == "current" else area_root / "retired"
                for number in range(count):
                    path = parent / f"{area}-{state}-{number:04d}.md"
                    path.write_text(self._document(area, state, number), encoding="utf-8")
                    rows[state].append(context_cli._entry_from_document(self.root, path, metadata, state))
            rows["current"].sort(key=lambda row: (row["created_at"], row["id"]))
            rows["history"].sort(key=lambda row: (row["created_at"], row["id"]))
            rendered = context_cli._replace_block(seed, "current", [context_cli._entry_row(row) for row in rows["current"]])
            if area != "snapshot":
                rendered = context_cli._replace_block(rendered, "history", [context_cli._entry_row(row) for row in rows["history"]])
            index_path = area_root / f"{area}.index.md"
            index_path.write_text(rendered, encoding="utf-8")
            area_rows.append(
                (
                    {
                        "area": area,
                        "path": index_path.relative_to(self.root).as_posix(),
                        "owner": self.OWNERS[area],
                        "claims": [area],
                        "artifact_schema": self.SCHEMAS[area],
                        "authority": self.AUTHORITIES[area],
                    },
                    area.title(),
                    f"Synthetic {area} corpus",
                )
            )
        root_index = self.root / context_cli.ROOT_INDEX
        root_index.write_text(context_cli.render_root_index(context_cli._root_seed(), area_rows), encoding="utf-8")


class TokenIOEvidenceTests(unittest.TestCase):
    temp: tempfile.TemporaryDirectory[str]
    corpus: SyntheticRepository
    recorded: dict[str, object] = {}

    @classmethod
    def setUpClass(cls) -> None:
        cls.temp = tempfile.TemporaryDirectory()
        cls.corpus = SyntheticRepository(Path(cls.temp.name))

    @classmethod
    def tearDownClass(cls) -> None:
        print("TOKEN_IO_METRICS=" + json.dumps(cls.recorded, ensure_ascii=False, sort_keys=True, separators=(",", ":")))
        cls.temp.cleanup()

    def test_synthetic_corpus_stage1_metrics_for_100_explicit_and_cross_area_queries(self) -> None:
        expected_current = {"snapshot": 100, "observation": 2000, "decision": 2000}
        expected_history = {"observation": 500, "decision": 500}
        self.assertEqual(expected_current, self.corpus.CURRENT_COUNTS)
        self.assertEqual(expected_history, {key: self.corpus.HISTORY_COUNTS[key] for key in expected_history})

        measurements = {}
        for label, areas, expected_index_opens in (
            ("explicit_area", ["observation"], 200),
            ("cross_area", [], 400),
        ):
            metrics = context_cli.IOMetrics()
            maximum_output = 0
            returned = 0
            omitted = 0
            for number in range(100):
                result = context_cli.recall_repository(
                    self.corpus.root,
                    query=f"probe{number:04d}",
                    areas=areas,
                    metrics=metrics,
                )
                output_bytes = encoded(result)
                maximum_output = max(maximum_output, output_bytes)
                returned += result["returned"]
                omitted += result["omitted"]
                self.assertLessEqual(output_bytes, 4 * 1024)
            self.assertEqual(expected_index_opens, metrics.index_opens)
            self.assertEqual(0, metrics.artifact_opens)
            self.assertEqual(0, metrics.artifact_directory_lists)
            self.assertEqual(0, metrics.artifact_stats)
            self.assertEqual(0, metrics.artifact_read_bytes)
            self.assertGreater(metrics.index_read_bytes, 0)
            self.assertGreater(metrics.output_bytes, 0)
            measurements[label] = {
                "queries": 100,
                "index_opens": metrics.index_opens,
                "index_read_bytes": metrics.index_read_bytes,
                "artifact_opens": metrics.artifact_opens,
                "artifact_directory_lists": metrics.artifact_directory_lists,
                "artifact_stats": metrics.artifact_stats,
                "artifact_read_bytes": metrics.artifact_read_bytes,
                "output_bytes": metrics.output_bytes,
                "max_output_bytes": maximum_output,
                "returned": returned,
                "omitted": omitted,
            }
        self.recorded["stage1"] = measurements

    def test_identifier_like_query_is_precise_and_healthy_miss_opens_no_bodies(self) -> None:
        precise_metrics = context_cli.IOMetrics()
        precise = context_cli.recall_repository(
            self.corpus.root,
            query="decision-current-0999",
            areas=["decision"],
            metrics=precise_metrics,
        )
        self.assertEqual(1, precise["returned"])
        self.assertEqual("context/decision/decision-current-0999.md", precise["items"][0]["path"])
        self.assertEqual(0, precise_metrics.artifact_opens)

        miss_metrics = context_cli.IOMetrics()
        missed = context_cli.recall_repository(
            self.corpus.root,
            query="no-metadata-overlap-paraphrase",
            areas=["observation"],
            metrics=miss_metrics,
        )
        self.assertEqual(0, missed["returned"])
        self.assertFalse(missed["index_fallback"])
        self.assertEqual(0, miss_metrics.artifact_opens)
        self.assertEqual(0, miss_metrics.artifact_read_bytes)
        self.recorded["precision_and_miss"] = {
            "corpus_entries": self.corpus.CURRENT_COUNTS["observation"],
            "precise_returned": precise["returned"],
            "precise_artifact_opens": precise_metrics.artifact_opens,
            "miss_returned": missed["returned"],
            "miss_artifact_opens": miss_metrics.artifact_opens,
            "miss_artifact_directory_lists": miss_metrics.artifact_directory_lists,
        }

    def test_stage1_pack_and_section_use_independent_byte_budgets(self) -> None:
        stage1 = context_cli.recall_repository(self.corpus.root, limit=20)
        stage1_default_bytes = encoded(stage1)
        self.assertLessEqual(stage1_default_bytes, 4 * 1024)

        expanded_stage1 = context_cli.recall_repository(self.corpus.root, limit=20, max_bytes=32 * 1024)
        expanded_stage1_bytes = encoded(expanded_stage1)
        self.assertGreater(expanded_stage1_bytes, 4 * 1024)
        self.assertLessEqual(expanded_stage1_bytes, 32 * 1024)

        pack_metrics = context_cli.IOMetrics()
        pack = context_cli.recall_repository(
            self.corpus.root,
            include_history=True,
            limit=20,
            pack=True,
            max_bytes=32 * 1024,
            metrics=pack_metrics,
        )
        pack_bytes = encoded(pack)
        self.assertLessEqual(pack_bytes, 8 * 1024)
        self.assertLessEqual(pack_metrics.artifact_opens, pack["returned"])
        self.assertEqual(5100 - pack["returned"], pack["omitted"])

        narrow_metrics = context_cli.IOMetrics()
        narrow_pack = context_cli.recall_repository(
            self.corpus.root,
            include_history=True,
            limit=20,
            pack=True,
            max_bytes=1024,
            metrics=narrow_metrics,
        )
        self.assertLessEqual(encoded(narrow_pack), 1024)
        self.assertLessEqual(narrow_metrics.artifact_opens, narrow_pack["returned"])

        section_metrics = context_cli.IOMetrics()
        section = context_cli.recall_repository(
            self.corpus.root,
            query="probe0000",
            areas=["decision"],
            sections=["Rationale"],
            limit=20,
            metrics=section_metrics,
        )
        section_bytes = encoded(section)
        self.assertLessEqual(section_bytes, 8 * 1024)
        self.assertLessEqual(section_metrics.artifact_opens, section["returned"])
        self.assertTrue(section["items"][0]["section_truncated"])
        self.assertTrue(section["items"][0]["sections"]["Rationale"].endswith("…"))
        self.assertIn("--read", section["items"][0]["full_read_hint"])
        for item in section["items"]:
            self.assertLessEqual(encoded(item), 2 * 1024)

        with self.assertRaises(context_cli.ContextError) as caught:
            context_cli.recall_repository(self.corpus.root, max_bytes=32 * 1024 + 1)
        self.assertEqual("usage_invalid", caught.exception.code)
        self.recorded["recall_budgets"] = {
            "stage1_default_bytes": stage1_default_bytes,
            "stage1_user_32k_bytes": expanded_stage1_bytes,
            "pack_bytes": pack_bytes,
            "pack_returned": pack["returned"],
            "pack_omitted": pack["omitted"],
            "pack_artifact_opens": pack_metrics.artifact_opens,
            "pack_artifact_read_bytes": pack_metrics.artifact_read_bytes,
            "pack_user_1k_bytes": encoded(narrow_pack),
            "pack_user_1k_returned": narrow_pack["returned"],
            "pack_user_1k_artifact_opens": narrow_metrics.artifact_opens,
            "section_bytes": section_bytes,
            "section_returned": section["returned"],
            "section_omitted": section["omitted"],
            "section_artifact_opens": section_metrics.artifact_opens,
        }

    def test_candidate_0_1_8_9_and_exact_owner_input_boundaries(self) -> None:
        measured = []
        for count in (0, 1, 8):
            values = [candidate(number) for number in range(count)]
            batch = {"schema": "context-capture-batch/v1", "audit_count": 1, "candidates": values}
            result = context_cli.validate_candidate_batch(batch, context_cli.capabilities_result())
            routed = context_cli.route_candidates(batch, context_cli.capabilities_result(), [])
            self.assertEqual(count, len(result))
            self.assertLessEqual(encoded(batch), 16 * 1024)
            self.assertEqual(encoded(batch), routed["canonical_bytes"])
            self.assertEqual(0, routed["router_owner_process_invocations"])
            measured.append(
                {
                    "candidates": count,
                    "batch_bytes": encoded(batch),
                    "audit_count": batch["audit_count"],
                    "router_owner_process_invocations": routed["router_owner_process_invocations"],
                }
            )
        with self.assertRaises(context_cli.ContextError) as caught:
            context_cli.validate_candidate_batch(
                {"schema": "context-capture-batch/v1", "audit_count": 1, "candidates": [candidate(number) for number in range(9)]},
                context_cli.capabilities_result(),
            )
        self.assertEqual("candidate_batch_too_large", caught.exception.code)
        measured.append({"candidates": 9, "result": caught.exception.code})

        external_capability = context_cli.builtin_capability("observation")
        external_capability["owner"] = "addon-boundary"
        capabilities = {"schema": "context-owner-capabilities/v1", "owners": [external_capability]}
        boundary = candidate(0)
        boundary["owner_inputs"]["observation"] = opaque_owner_input(8 * 1024)
        self.assertEqual([boundary], context_cli.validate_candidate_batch([boundary], capabilities))
        over = candidate(0)
        over["owner_inputs"]["observation"] = opaque_owner_input(8 * 1024 + 1)
        self.assertEqual(8 * 1024 + 1, encoded(over["owner_inputs"]["observation"]))
        with self.assertRaises(context_cli.ContextError) as caught:
            context_cli.validate_candidate_batch([over], capabilities)
        self.assertEqual("candidate_too_large", caught.exception.code)
        self.assertEqual(
            {"kind": "observation", "actual_bytes": 8193, "maximum_bytes": 8192, "over_by_bytes": 1},
            caught.exception.details,
        )
        self.recorded["candidate_batches"] = measured
        self.recorded["owner_input_boundary"] = {"accepted_bytes": 8192, "rejected_bytes": 8193}

    def test_addon_count_does_not_multiply_audit_or_router_process_work(self) -> None:
        measurements = []
        for count in (0, 1, 8):
            capabilities = {
                "schema": "context-owner-capabilities/v1",
                "owners": [
                    {
                        "schema": "context-owner-capability/v1",
                        "owner": f"addon-{number}",
                        "kind": f"kind-{number}",
                        "artifact_schema": f"context-addon-{number}/v1",
                        "authority": "evidence",
                        "claim_surface": {
                            "type": "agent_skill",
                            "name": f"addon-{number}:claim",
                            "operation": "claim",
                        },
                    }
                    for number in range(count)
                ],
            }
            batch = {"schema": "context-capture-batch/v1", "audit_count": 1, "candidates": []}
            result = context_cli.route_candidates(batch, capabilities, [])
            self.assertEqual(1, batch["audit_count"])
            self.assertEqual(0, result["router_owner_process_invocations"])
            self.assertEqual(0, result["cache_probe_count"])
            self.assertEqual(0, result["alternate_runtime_count"])
            measurements.append(
                {
                    "addons": count,
                    "audit_count": batch["audit_count"],
                    "router_owner_process_invocations": result["router_owner_process_invocations"],
                }
            )
        self.recorded["addon_scaling"] = measurements

    def test_grouped_preview_accepts_complete_32k_or_less_without_semantic_truncation(self) -> None:
        semantic_content = "## 결정\n\n" + "가" * 9000 + "\n\n## 취지\n\n전체 취지\n\n## 반려대안\n\n전체 반려대안\n"
        preview = {
            "schema": "context-approval-preview/v1",
            "owner": "context-decision",
            "candidate_id": None,
            "artifacts": [{"effect_id": "effect", "path": "context/decision/x.md", "content": semantic_content}],
            "effects": [],
        }
        preview_bytes = encoded(preview)
        self.assertLessEqual(preview_bytes, 32 * 1024)
        with tempfile.TemporaryDirectory() as temp:
            result = context_cli._bundle_result(Path(temp), preview, {}, [])
            self.assertEqual(preview, result["approval_preview"])
            self.assertEqual(semantic_content, result["bundle"]["approval_material"]["preview"]["artifacts"][0]["content"])
            self.assertIn("전체 반려대안", result["approval_preview"]["artifacts"][0]["content"])
        self.recorded["approval_preview"] = {"accepted_bytes": preview_bytes, "semantic_content_bytes": len(semantic_content.encode("utf-8"))}

    def test_grouped_preview_over_32k_rejects_instead_of_truncating(self) -> None:
        preview = {
            "schema": "context-approval-preview/v1",
            "owner": "context-core",
            "candidate_id": None,
            "artifacts": [{"effect_id": "effect", "path": "context/observation/x.md", "content": "가" * (33 * 1024)}],
            "effects": [],
        }
        with tempfile.TemporaryDirectory() as temp:
            with self.assertRaises(context_cli.ContextError) as caught:
                context_cli._bundle_result(Path(temp), preview, {}, [])
            self.assertEqual("approval_preview_too_large", caught.exception.code)

    def test_stdlib_only_imports(self) -> None:
        allowed = set(sys.stdlib_module_names) | {"__future__"}
        for path in (
            ROOT / "plugins/bobbin/skills/context/scripts/context_cli.py",
            ROOT / "plugins/bobbin/skills/decision/scripts/decision_cli.py",
        ):
            import ast

            tree = ast.parse(path.read_text(encoding="utf-8"))
            roots = set()
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    roots.update(alias.name.split(".")[0] for alias in node.names)
                elif isinstance(node, ast.ImportFrom) and node.module:
                    roots.add(node.module.split(".")[0])
            self.assertEqual(set(), roots - allowed, f"non-stdlib imports in {path}")


if __name__ == "__main__":
    unittest.main()
