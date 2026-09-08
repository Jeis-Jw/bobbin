from __future__ import annotations

import importlib.util
import sys
from pathlib import Path


ROOT = next(p for p in Path(__file__).resolve().parents if (p / "pytest.ini").is_file())
PLUGIN = ROOT / "plugins/bobbin"
CLI_PATH = PLUGIN / "skills/intent/scripts/intent_cli.py"
CORE_CLI_PATH = ROOT / "plugins/bobbin/skills/context/scripts/context_cli.py"


def load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


intent_cli = load("context_intent_test_cli", CLI_PATH)
core_cli = load("context_intent_test_core", CORE_CLI_PATH)


def candidate(
    *,
    candidate_id: str = "cand_550e8400e29b41d4a716446655440020",
    intent: str = "고객이 배포 전 변경 영향을 이해할 수 있게 한다.",
    title: str = "변경 영향 가시성",
    scope: str = "product/release",
    intent_key: str = "change-visibility",
) -> dict:
    return {
        "schema": "context-capture-candidate/v1",
        "candidate_id": candidate_id,
        "title": title,
        "claim": intent,
        "summary": "릴리스 변경 영향의 desired direction을 고정한다.",
        "captured_from": "conversation",
        "requested_kind": "intent",
        "specialized_kinds": ["intent"],
        "fallback_kind": None,
        "scope_hint": scope,
        "tags": ["release"],
        "search_terms": ["change", "visibility"],
        "source_refs": ["conversation:test"],
        "owner_inputs": {
            "intent": {
                "intent": intent,
                "intent_key": intent_key,
                "success_criteria": ["릴리스 전에 영향 범위를 확인할 수 있다."],
                "constraints": ["기존 승인 흐름을 우회하지 않는다."],
                "revisit_conditions": ["릴리스 모델이 바뀐다."],
            }
        },
    }


def attestation(value: dict) -> dict:
    return {
        "schema": "context-semantic-attestation/v1",
        "operation": "claim",
        "input_schema": value["schema"],
        "input_digest": intent_cli.canonical_digest(value),
        "assertions": [
            {"name": "intent_present", "value": True, "evidence_pointers": ["/owner_inputs/intent/intent"]},
            {"name": "desired_direction", "value": True, "evidence_pointers": ["/owner_inputs/intent/intent"]},
        ],
    }


def same_claim_attestation(value: dict) -> dict:
    return {
        "schema": "context-semantic-attestation/v1",
        "operation": "same_claim",
        "input_schema": value["schema"],
        "input_digest": intent_cli.canonical_digest(value),
        "assertions": [{
            "name": "same_semantic_claim",
            "value": True,
            "evidence_pointers": ["/predecessor/primary_claim", "/successor/primary_claim"],
        }],
    }


def init_repo(repo: Path) -> None:
    repo.mkdir(parents=True, exist_ok=True)
    core_cli.bootstrap_repository(repo, intent_cli.owner_descriptor(), intent_cli.intent_index_seed(), host="codex")


def apply_result(repo: Path, result: dict) -> dict:
    receipt = intent_cli.validate_batch(repo, result)
    preview = core_cli.finalize_owner_result(repo, result, receipt)
    return core_cli.apply_bundle(repo, preview["bundle"], preview["approval_digest"])


def capture(repo: Path, *, identifier: str = "ctx_550e8400e29b41d4a716446655440020") -> dict:
    value = candidate()
    result = intent_cli.build_claim_result(
        value,
        attestation(value),
        identifier=identifier,
        created_at="2026-09-01T01:00:00+09:00",
    )
    apply_result(repo, result)
    return result
