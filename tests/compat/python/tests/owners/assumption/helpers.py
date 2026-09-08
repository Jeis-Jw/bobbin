from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path


ROOT = next(p for p in Path(__file__).resolve().parents if (p / "pytest.ini").is_file())
PLUGIN = ROOT / "plugins/bobbin"
CLI_PATH = PLUGIN / "skills/assumption/scripts/assumption_cli.py"
INIT_PATH = PLUGIN / "skills/init/scripts/assumption_init.py"
CORE_CLI_PATH = ROOT / "plugins/bobbin/skills/context/scripts/context_cli.py"


def load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


assumption_cli = load("context_assumption_test_cli", CLI_PATH)
core_cli = load("context_assumption_test_core", CORE_CLI_PATH)


def tree_digest(root: Path) -> str:
    digest = hashlib.sha256()
    for path in sorted(item for item in root.rglob("*") if item.is_file()):
        digest.update(path.relative_to(root).as_posix().encode("utf-8"))
        digest.update(path.read_bytes())
    return digest.hexdigest()


def candidate(
    *,
    candidate_id: str = "cand_550e8400e29b41d4a716446655440000",
    assumption: str = "외부 IdP는 callback 요청을 5초 안에 반환할 것이다.",
    scope: str = "project/auth",
    title: str = "IdP callback latency 전제",
) -> dict:
    return {
        "schema": "context-capture-candidate/v1",
        "candidate_id": candidate_id,
        "title": title,
        "claim": assumption,
        "summary": "인증 timeout 설계에 사용하는 아직 검증되지 않은 전제다.",
        "captured_from": "conversation",
        "requested_kind": "assumption",
        "specialized_kinds": ["assumption"],
        "fallback_kind": None,
        "scope_hint": scope,
        "evidence": [],
        "tags": ["auth"],
        "search_terms": ["IdP", "latency"],
        "source_refs": ["conversation:test"],
        "owner_inputs": {
            "assumption": {
                "assumption": assumption,
                "basis": ["현재 provider SLA 설명은 5초 이내를 목표로 한다."],
                "unverified_ok": True,
                "confirm_conditions": ["production p95가 5초 미만으로 7일 유지된다."],
                "refute_conditions": ["production p95가 5초 이상이다."],
            }
        },
    }


def attestation(value: dict) -> dict:
    return {
        "schema": "context-semantic-attestation/v1",
        "operation": "claim",
        "input_schema": value["schema"],
        "input_digest": assumption_cli.canonical_digest(value),
        "assertions": [
            {"name": "assumption_present", "value": True, "evidence_pointers": ["/owner_inputs/assumption/assumption"]},
            {"name": "unverified_ok", "value": True, "evidence_pointers": ["/owner_inputs/assumption/unverified_ok"]},
        ],
    }


def semantic_candidate(kind: str) -> dict:
    value = candidate()
    value["requested_kind"] = kind
    value["specialized_kinds"] = [kind]
    value["owner_inputs"] = {kind: {"observation": "관측했다."} if kind == "observation" else {"decision": "현재 이 선택을 따른다."}}
    value["claim"] = "실측된 callback p95는 3초다." if kind == "observation" else "callback timeout은 5초로 확정한다."
    return value


def init_repo(repo: Path) -> None:
    repo.mkdir(parents=True, exist_ok=True)
    core_cli.bootstrap_repository(repo, assumption_cli.owner_descriptor(), assumption_cli.assumption_index_seed(), host="codex")


def apply_result(repo: Path, result: dict) -> dict:
    receipt = assumption_cli.validate_batch(repo, result)
    preview = core_cli.finalize_owner_result(repo, result, receipt)
    return core_cli.apply_bundle(repo, preview["bundle"], preview["approval_digest"])


def capture(repo: Path, *, identifier: str = "ctx_550e8400e29b41d4a716446655440000") -> dict:
    value = candidate()
    result = assumption_cli.build_claim_result(value, attestation(value), identifier=identifier, created_at="2026-08-22T01:00:00+09:00")
    apply_result(repo, result)
    return result


def write_preflight(root: Path, state: str = "ready", *, entrypoint: Path | None = None) -> tuple[Path, Path]:
    inventory = root / "inventory.json"
    doctor = root / "doctor.json"
    inventory.write_text(json.dumps({"plugins": [{"marketplace": "bobbin", "plugin": "bobbin", "source": "Jeis-Jw/bobbin", "enabled": True, "protocols": ["context-common/v2"], "entrypoint": str((entrypoint or CORE_CLI_PATH).resolve())}]}), encoding="utf-8")
    doctor.write_text(json.dumps({"schema": "context-core-doctor/v1", "owner": "context-core", "supported_protocols": ["context-common/v2"], "repository_state": state, "root": "context/", "issues": [], "warnings": [], "plugin_version": "1.0.0", "entrypoint": str((entrypoint or CORE_CLI_PATH).resolve()), "protocol": "context-common/v2"}), encoding="utf-8")
    return inventory, doctor


def preflight_args(inventory: Path, doctor: Path) -> list[str]:
    return ["--host", "codex", "--core-inventory", f"@{inventory}", "--core-doctor", f"@{doctor}"]


def run_cli(repo: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run([sys.executable, str(CLI_PATH), *args], cwd=repo, env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"}, text=True, capture_output=True)
