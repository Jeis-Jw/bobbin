#!/usr/bin/env python3
from __future__ import annotations

import ast
import hashlib
import importlib.util
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


PLUGIN = next(p for p in Path(__file__).resolve().parents if (p / "pytest.ini").is_file()) / "plugins/bobbin"
CLI_PATH = PLUGIN / "skills/decision/scripts/decision_cli.py"
SPEC = importlib.util.spec_from_file_location("decision_cli", CLI_PATH)
assert SPEC and SPEC.loader
decision_cli = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = decision_cli
SPEC.loader.exec_module(decision_cli)


def vault_dir() -> tempfile.TemporaryDirectory[str]:
    temp = tempfile.TemporaryDirectory()
    return temp


def tree_digest(root: Path) -> str:
    digest = hashlib.sha256()
    for path in sorted(path for path in root.rglob("*") if path.is_file()):
        digest.update(path.relative_to(root).as_posix().encode())
        digest.update(path.read_bytes())
    return digest.hexdigest()


def candidate(
    *,
    scope: str = "project/auth",
    key: str = "session-owner",
    decision: str = "인증 세션은 BFF가 소유한다.",
    rationale: str = "브라우저별 cookie 차이를 서버 경계 안으로 모은다.",
    alternatives: list[str] | None = None,
    title: str = "인증 세션 소유권",
    candidate_id: str = "cand_550e8400e29b41d4a716446655440000",
    revisit_on: str | None = None,
) -> dict:
    values = {
        "decision": decision,
        "rationale": rationale,
        "rejected_alternatives": alternatives or ["SPA token 소유: XSS 노출이 커져 반려"],
        "decision_key": key,
    }
    if revisit_on:
        values["revisit_on"] = revisit_on
    return {
        "schema": "context-capture-candidate/v1",
        "candidate_id": candidate_id,
        "search_terms": ["인증 주체", "세션 owner"],
        "title": title,
        "claim": decision,
        "summary": "OAuth callback과 cookie boundary를 한 경계로 통합한다.",
        "captured_from": "conversation",
        "requested_kind": "decision",
        "specialized_kinds": ["decision"],
        "fallback_kind": None,
        "scope_hint": scope,
        "source_refs": ["conversation:test"],
        "evidence": ["결정 권한자가 현재 따를 선택으로 확정했다."],
        "tags": ["auth"],
        "owner_inputs": {"decision": values},
    }


def attestation(value: dict, *, truth: bool = True) -> dict:
    return {
        "schema": "context-semantic-attestation/v1",
        "operation": "claim",
        "input_schema": value["schema"],
        "input_digest": decision_cli.canonical_digest(value),
        "assertions": [
            {"name": "explicit_choice", "value": truth, "evidence_pointers": ["/owner_inputs/decision/decision"]},
            {"name": "scope_identified", "value": truth, "evidence_pointers": ["/scope_hint"]},
            {"name": "commitment_present", "value": truth, "evidence_pointers": ["/evidence/0"]},
        ],
    }


def claim_result(
    value: dict | None = None,
    *,
    identifier: str = "ctx_550e8400e29b41d4a716446655440000",
    created_at: str = "2026-08-13T18:20:00+09:00",
    filename: str | None = None,
    repo: Path | None = None,
    acknowledgements: tuple[str, ...] = (),
) -> dict:
    value = value or candidate()
    return decision_cli.build_claim_result(
        value,
        attestation(value),
        identifier=identifier,
        created_at=created_at,
        filename=filename,
        repo=repo,
        acknowledged_conflicts=acknowledgements,
    )


def _entry(path: str, content: str, state: str) -> str:
    frontmatter, _ = decision_cli.parse_document(content)
    row = {
        "id": frontmatter["id"],
        "path": path,
        "title": frontmatter["title"],
        "summary": frontmatter["summary"],
        "state": state,
        "created_at": frontmatter["created_at"],
        "terms": list(frontmatter.get("tags", [])) + list(frontmatter.get("search_terms", [])),
    }
    if state == "history":
        row["retired_at"] = frontmatter["retired_at"]
        row["retired_reason"] = frontmatter["retired_reason"]
        if "superseded_by" in frontmatter:
            row["superseded_by"] = frontmatter["superseded_by"]
    for field in ("scope", "decision_key", "revisit_on"):
        if field in frontmatter:
            row[field] = frontmatter[field]
    visible = f"- [[{path[:-3]}]] — {frontmatter['title']} — {frontmatter['summary']}"
    return f"{visible} <!-- context-entry {json.dumps(row, ensure_ascii=False, separators=(',', ':'))} -->"


def write_decision_area(
    repo: Path,
    *,
    current: list[tuple[str, str]] | None = None,
    history: list[tuple[str, str]] | None = None,
) -> None:
    current = current or []
    history = history or []
    (repo / "context/decision/retired").mkdir(parents=True, exist_ok=True)
    for path, content in current + history:
        target = repo / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
    seed = decision_cli.decision_index_seed()
    current_rows = "\n".join(_entry(path, content, "current") for path, content in current)
    history_rows = "\n".join(_entry(path, content, "history") for path, content in history)
    seed = seed.replace(
        "<!-- BEGIN CONTEXT GENERATED:current -->\n<!-- END CONTEXT GENERATED:current -->",
        f"<!-- BEGIN CONTEXT GENERATED:current -->\n{current_rows + chr(10) if current_rows else ''}<!-- END CONTEXT GENERATED:current -->",
    )
    seed = seed.replace(
        "<!-- BEGIN CONTEXT GENERATED:history -->\n<!-- END CONTEXT GENERATED:history -->",
        f"<!-- BEGIN CONTEXT GENERATED:history -->\n{history_rows + chr(10) if history_rows else ''}<!-- END CONTEXT GENERATED:history -->",
    )
    (repo / decision_cli.DECISION_INDEX).write_text(seed, encoding="utf-8")


def bundle(result: dict, *, validation: dict | None = None, priors: list[str] | None = None) -> dict:
    owner_content = decision_cli.canonical_json(result)
    owner_digest = decision_cli.bytes_digest(owner_content.encode("utf-8"))
    plan = {
        "schema": "context-mutation-plan/v1",
        "plan_id": "plan_550e8400e29b41d4a716446655440000",
        "owner": "context-decision",
        "source_type": "owner_result",
        "owner_result_digest": owner_digest,
        "owner_result_material": "material_owner_result",
        "capability_digest": decision_cli.canonical_digest(decision_cli.decision_capability()),
        "transition": result["transition"],
        "owner_descriptor": {"owner": "context-decision", "kind": "decision", "artifact_schema": "context-decision/v1", "authority": "authoritative"},
        "owner_validation": validation,
        "prior_bundle_digests": list(priors or []),
        "read_preconditions": result["proposed_plan"].get("read_preconditions", []),
        "operations": [],
    }
    preview = {"schema": "context-approval-preview/v1", "owner": "context-decision", "candidate_id": result.get("candidate_id"), "artifacts": result["artifact_drafts"], "effects": result["effects"]}
    approval_material = {"preview": preview, "plan": plan}
    approval_digest = decision_cli.canonical_digest(approval_material)
    return {
        "schema": "context-mutation-bundle/v1",
        "approval_material": approval_material,
        "approval_digest": approval_digest,
        "materials": [{"material_id": "material_owner_result", "path": None, "content": owner_content}],
    }


class DecisionSchemaTests(unittest.TestCase):
    def test_legacy_untyped_relations_keep_pre_010_cardinality_and_bytes(self) -> None:
        identifiers = [
            "ctx_550e8400e29b41d4a71644665544" + f"{number:04x}"
            for number in range(13)
        ]
        legacy_candidate = candidate()
        legacy_candidate["informed_by"] = identifiers
        content = claim_result(legacy_candidate)["artifact_drafts"][0]["content"]
        frontmatter, sections = decision_cli.parse_document(content)
        self.assertEqual(identifiers, frontmatter["relations"]["informed_by"])
        self.assertEqual(content, decision_cli.render_document(frontmatter, sections))

        typed_frontmatter = dict(frontmatter)
        typed_frontmatter["relations"] = {"serves:intent": identifiers}
        with self.assertRaises(decision_cli.DecisionError) as typed_error:
            decision_cli.validate_decision_document(typed_frontmatter, sections)
        self.assertEqual("schema_invalid", typed_error.exception.code)

    def test_optional_typed_relation_inputs_preserve_standalone_and_legacy_shapes(self) -> None:
        standalone = claim_result(candidate())["artifact_drafts"][0]["content"]
        standalone_frontmatter, _ = decision_cli.parse_document(standalone)
        self.assertNotIn("relations", standalone_frontmatter)

        legacy_candidate = candidate()
        legacy_candidate["informed_by"] = ["ctx_550e8400e29b41d4a716446655440010"]
        legacy_frontmatter, _ = decision_cli.parse_document(claim_result(legacy_candidate)["artifact_drafts"][0]["content"])
        self.assertEqual(
            {"informed_by": ["ctx_550e8400e29b41d4a716446655440010"]},
            legacy_frontmatter["relations"],
        )

        typed_candidate = candidate()
        typed_candidate["owner_inputs"]["decision"].update({
            "serves_intents": ["ctx_550e8400e29b41d4a716446655440011"],
            "informed_by_observations": ["ctx_550e8400e29b41d4a716446655440012"],
            "informed_by_assumptions": ["ctx_550e8400e29b41d4a716446655440013"],
            "affects_documents": ["ctx_550e8400e29b41d4a716446655440014"],
        })
        typed_frontmatter, _ = decision_cli.parse_document(claim_result(typed_candidate)["artifact_drafts"][0]["content"])
        self.assertEqual(
            {
                "serves:intent": ["ctx_550e8400e29b41d4a716446655440011"],
                "informed_by:observation": ["ctx_550e8400e29b41d4a716446655440012"],
                "informed_by:assumption": ["ctx_550e8400e29b41d4a716446655440013"],
                "affects:document": ["ctx_550e8400e29b41d4a716446655440014"],
            },
            typed_frontmatter["relations"],
        )
        self.assertEqual("context-owner-descriptor/v1", decision_cli.build_init_plan()["owner_descriptor"]["schema"])

    def test_comma_joined_typed_relation_explains_repeat_flag_contract(self) -> None:
        value = candidate()
        value["owner_inputs"]["decision"]["informed_by_observations"] = [
            "ctx_550e8400e29b41d4a716446655440012,ctx_550e8400e29b41d4a716446655440013"
        ]
        with self.assertRaises(decision_cli.DecisionError) as caught:
            decision_cli.validate_candidate(value)
        self.assertEqual("candidate_invalid", caught.exception.code)
        self.assertIn("repeat the flag", caught.exception.message)
        self.assertEqual("--informed-by-observation", caught.exception.details["flag"])

    def test_removed_artifact_fields_are_readable_and_lazy_cleaned(self) -> None:
        content = claim_result()["artifact_drafts"][0]["content"]
        for field in ("claim_fingerprint", "source_claim_fingerprint"):
            with self.subTest(field=field):
                legacy = content.replace("schema: \"context-decision/v1\"\n", f"schema: \"context-decision/v1\"\n{field}: \"sha256:{'0' * 24}\"\n", 1)
                frontmatter, sections = decision_cli.parse_document(legacy)
                self.assertIn(field, frontmatter)
                self.assertNotIn(field, decision_cli.render_document(frontmatter, sections))

            legacy_candidate = candidate()
            legacy_candidate[field] = "sha256:" + "0" * 24
            with self.assertRaises(decision_cli.DecisionError) as candidate_error:
                decision_cli.validate_candidate(legacy_candidate)
            self.assertEqual("schema_removed_field", candidate_error.exception.code)

        legacy_candidate = candidate()
        legacy_candidate["claim_key"] = "choice-1"
        with self.assertRaises(decision_cli.DecisionError) as candidate_error:
            decision_cli.validate_candidate(legacy_candidate)
        self.assertEqual("schema_removed_field", candidate_error.exception.code)

    def test_acceptance_25_sections(self) -> None:
        valid = candidate()
        result = claim_result(valid)
        _, sections = decision_cli.parse_document(result["artifact_drafts"][0]["content"])
        self.assertEqual(set(decision_cli.CORE_SECTIONS), set(sections))

        for field in ("decision", "rationale"):
            invalid = candidate()
            invalid["owner_inputs"]["decision"][field] = "..."
            if field == "decision":
                invalid["claim"] = "..."
            with self.assertRaises(decision_cli.DecisionError):
                claim_result(invalid)
        invalid = candidate(alternatives=["TODO"])
        with self.assertRaises(decision_cli.DecisionError):
            claim_result(invalid)

    def test_requested_decision_does_not_bypass_semantic_validation(self) -> None:
        for label in ("idea", "question", "fact", "preference"):
            value = candidate(decision=f"{label} only")
            self.assertEqual("decision", value["requested_kind"])
            with self.assertRaises(decision_cli.DecisionError) as caught:
                decision_cli.build_claim_result(value, attestation(value, truth=False))
            self.assertEqual("semantic_attestation_invalid", caught.exception.code)
            declined = decision_cli.build_decline_result(value, f"{label} is not an accepted choice")
            self.assertEqual("decline", declined["decision"])
            self.assertEqual([], declined["artifact_drafts"])

    def test_complete_draft_binds_candidate_attestation_and_digest(self) -> None:
        value = candidate()
        result = claim_result(value)
        decision_cli.validate_owner_result(result)
        frontmatter, _ = decision_cli.parse_document(result["artifact_drafts"][0]["content"])
        self.assertEqual(value["search_terms"], frontmatter["search_terms"])
        self.assertEqual(value, result["semantic_inputs"][0]["value"])
        self.assertEqual(decision_cli.canonical_digest(value), result["semantic_attestations"][0]["input_digest"])
        altered = json.loads(json.dumps(result))
        altered["semantic_inputs"][0]["value"]["summary"] += " altered"
        with self.assertRaises(decision_cli.DecisionError):
            decision_cli.validate_owner_result(altered)

    def test_complete_draft_rejects_candidate_owned_semantic_tampering(self) -> None:
        original = claim_result()
        cases = (
            ("인증 세션은 BFF가 소유한다.", "인증 세션은 SPA가 소유한다.", "primary_claim"),
            ("브라우저별 cookie 차이를 서버 경계 안으로 모은다.", "브라우저가 token을 직접 관리한다.", "supporting_context"),
            ("SPA token 소유: XSS 노출이 커져 반려", "BFF 소유: 운영비가 커져 반려", None),
        )
        for expected, replacement, projection_field in cases:
            with self.subTest(field=projection_field or "rejected_alternatives"):
                altered = json.loads(json.dumps(original))
                draft = altered["artifact_drafts"][0]
                draft["content"] = draft["content"].replace(expected, replacement)
                if projection_field == "primary_claim":
                    draft["semantic_projection"][projection_field] = replacement
                elif projection_field == "supporting_context":
                    draft["semantic_projection"][projection_field] = [replacement]
                with self.assertRaises(decision_cli.DecisionError) as caught:
                    decision_cli.validate_owner_result(altered)
                self.assertEqual("claim_result_mismatch", caught.exception.code)

    def test_candidate_id_is_transport_only(self) -> None:
        transport = candidate(candidate_id="cand_00000000000000000000000000000000")
        self.assertEqual("project/auth", decision_cli.validate_candidate(transport)[0])
        for invalid in ("cand_0", "cand_0000000000000000000000000000000G", "ctx_00000000000000000000000000000000"):
            with self.subTest(candidate_id=invalid), self.assertRaises(decision_cli.DecisionError) as caught:
                decision_cli.validate_candidate(candidate(candidate_id=invalid))
            self.assertEqual("candidate_invalid", caught.exception.code)

    def test_acceptance_36_scope_key(self) -> None:
        variants = [
            ("Project/Auth/", "Session Owner"),
            (" project / auth ", "session_owner"),
            ("ＰＲＯＪＥＣＴ/AUTH", "SESSION-OWNER"),
        ]
        for scope, key in variants:
            self.assertEqual("project/auth", decision_cli.canonical_scope(scope))
            self.assertEqual("session-owner", decision_cli.canonical_decision_key(key))
        self.assertTrue(decision_cli.is_ancestor_scope("project", "project/auth"))
        self.assertFalse(decision_cli.is_ancestor_scope("project/a", "project/auth"))
        self.assertFalse(decision_cli.is_ancestor_scope("project/auth", "project/auth"))
        with self.assertRaises(decision_cli.DecisionError):
            decision_cli.canonical_scope("project//auth")
        with self.assertRaises(decision_cli.DecisionError):
            decision_cli.canonical_decision_key("session/owner")

    def test_decision_owner_is_stdlib_only_and_has_no_write_primitive(self) -> None:
        module = ast.parse(CLI_PATH.read_text(encoding="utf-8"))
        allowed = {"__future__", "argparse", "datetime", "hashlib", "importlib", "json", "math", "os", "pathlib", "re", "stat", "subprocess", "sys", "typing", "unicodedata", "uuid"}
        imported = set()
        banned_attributes = {"write_text", "write_bytes", "mkdir", "rename", "unlink", "rmdir", "touch"}
        seen_banned = set()
        for node in ast.walk(module):
            if isinstance(node, ast.Import):
                imported.update(alias.name.split(".")[0] for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported.add(node.module.split(".")[0])
            elif isinstance(node, ast.Attribute) and node.attr in banned_attributes:
                seen_banned.add(node.attr)
            elif isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "open":
                seen_banned.add("open")
        self.assertFalse(imported - allowed, imported - allowed)
        self.assertEqual(set(), seen_banned)


if __name__ == "__main__":
    unittest.main()
