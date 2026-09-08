from __future__ import annotations

import importlib.util
import sys
from pathlib import Path


ROOT = next(p for p in Path(__file__).resolve().parents if (p / "pytest.ini").is_file())
PLUGIN = ROOT / "plugins/bobbin"
CLI_PATH = PLUGIN / "skills/document/scripts/document_cli.py"
CORE_CLI_PATH = ROOT / "plugins/bobbin/skills/context/scripts/context_cli.py"


def load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


document_cli = load("context_document_test_cli", CLI_PATH)
core_cli = load("context_document_test_core", CORE_CLI_PATH)


def candidate(
    *,
    candidate_id: str = "cand_550e8400e29b41d4a716446655440030",
    content: str = "릴리스 전에 변경 범위, 운영 영향, 복구 절차를 검토한다.",
    title: str = "릴리스 운영 문서",
    scope: str = "product/release",
    document_key: str = "release-operations",
) -> dict:
    return {
        "schema": "context-capture-candidate/v1",
        "candidate_id": candidate_id,
        "title": title,
        "claim": content,
        "summary": "릴리스 운영 절차의 living document다.",
        "captured_from": "conversation",
        "requested_kind": "document",
        "specialized_kinds": ["document"],
        "fallback_kind": None,
        "scope_hint": scope,
        "tags": ["release"],
        "search_terms": ["release", "operations"],
        "source_refs": ["conversation:test"],
        "owner_inputs": {"document": {"document_key": document_key, "content": content}},
    }


def attestation(value: dict) -> dict:
    return {
        "schema": "context-semantic-attestation/v1",
        "operation": "claim",
        "input_schema": value["schema"],
        "input_digest": document_cli.canonical_digest(value),
        "assertions": [
            {"name": "content_present", "value": True, "evidence_pointers": ["/owner_inputs/document/content"]},
            {"name": "living_document", "value": True, "evidence_pointers": ["/owner_inputs/document/document_key", "/owner_inputs/document/content"]},
        ],
    }


def init_repo(repo: Path) -> None:
    repo.mkdir(parents=True, exist_ok=True)
    core_cli.bootstrap_repository(repo, document_cli.owner_descriptor(), document_cli.document_index_seed(), host="codex")


def apply_result(repo: Path, result: dict) -> dict:
    receipt = document_cli.validate_batch(repo, result)
    preview = core_cli.finalize_owner_result(repo, result, receipt)
    return core_cli.apply_bundle(repo, preview["bundle"], preview["approval_digest"])


def capture(repo: Path, *, identifier: str = "ctx_550e8400e29b41d4a716446655440030") -> dict:
    value = candidate()
    result = document_cli.build_claim_result(
        value,
        attestation(value),
        identifier=identifier,
        created_at="2026-09-01T01:00:00+09:00",
    )
    apply_result(repo, result)
    return result
