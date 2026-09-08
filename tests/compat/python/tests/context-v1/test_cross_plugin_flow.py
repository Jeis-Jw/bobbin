#!/usr/bin/env python3
from __future__ import annotations

import concurrent.futures
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


ROOT = next(p for p in Path(__file__).resolve().parents if (p / "pytest.ini").is_file())


def load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


context_cli = load("context_cli_cross", ROOT / "plugins/bobbin/skills/context/scripts/context_cli.py")
decision_cli = load("decision_cli_cross", ROOT / "plugins/bobbin/skills/decision/scripts/decision_cli.py")
assumption_cli = load("assumption_cli_cross", ROOT / "plugins/bobbin/skills/assumption/scripts/assumption_cli.py")
term_cli = load("term_cli_cross", ROOT / "plugins/bobbin/skills/term/scripts/term_cli.py")
CORE_CLI = ROOT / "plugins/bobbin/skills/context/scripts/context_cli.py"
DECISION_CLI = ROOT / "plugins/bobbin/skills/decision/scripts/decision_cli.py"
DECISION_INIT = ROOT / "plugins/bobbin/skills/init/scripts/decision_init.py"
ASSUMPTION_CLI = ROOT / "plugins/bobbin/skills/assumption/scripts/assumption_cli.py"
TERM_CLI = ROOT / "plugins/bobbin/skills/term/scripts/term_cli.py"


def run_cli(repo: Path, cli: Path, *arguments: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(cli), *arguments],
        cwd=repo,
        env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
        text=True,
        capture_output=True,
    )


def public_result(completed: subprocess.CompletedProcess[str]) -> dict:
    if completed.returncode != 0:
        raise AssertionError(completed.stdout + completed.stderr)
    envelope = json.loads(completed.stdout)
    if envelope.get("ok") is not True or not isinstance(envelope.get("result"), dict):
        raise AssertionError(completed.stdout + completed.stderr)
    return envelope["result"]


def write_json(path: Path, value: object, *, canonical: bool = False) -> Path:
    text = (
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        if canonical
        else json.dumps(value, ensure_ascii=False)
    )
    path.write_text(text, encoding="utf-8")
    return path


def repository_bytes(repo: Path) -> dict[str, str]:
    output: dict[str, str] = {}
    for path in sorted(repo.rglob("*")):
        if not path.is_file():
            continue
        output[path.relative_to(repo).as_posix()] = hashlib.sha256(path.read_bytes()).hexdigest()
    return output


def public_preflight(root: Path, repo: Path, *, prefix: str) -> tuple[Path, Path]:
    doctor = public_result(run_cli(repo, CORE_CLI, "doctor", "--json"))
    inventory = {
        "plugins": [{
            "marketplace": "bobbin",
            "plugin": "bobbin",
            "source": "Jeis-Jw/bobbin",
            "enabled": True,
            "protocols": ["context-common/v2"],
            "entrypoint": str(CORE_CLI.resolve()),
        }],
    }
    return (
        write_json(root / f"{prefix}-inventory.json", inventory),
        write_json(root / f"{prefix}-doctor.json", doctor),
    )


def preflight_arguments(inventory: Path, doctor: Path) -> list[str]:
    return [
        "--host", "codex",
        "--core-inventory", f"@{inventory}",
        "--core-doctor", f"@{doctor}",
    ]


def bootstrap_three_owners(root: Path, repo: Path) -> dict:
    public_result(run_cli(repo, CORE_CLI, "init", "--host", "codex", "--json"))

    decision_inventory, decision_doctor = public_preflight(root, repo, prefix="decision-init")
    decision_plan = public_result(run_cli(
        repo,
        DECISION_CLI,
        "init",
        *preflight_arguments(decision_inventory, decision_doctor),
        "--json",
    ))
    decision_descriptor = write_json(root / "decision-descriptor.json", decision_plan["owner_descriptor"])
    decision_seed = root / "decision.index.md"
    decision_seed.write_text(decision_plan["index_seed"], encoding="utf-8")
    decision_bootstrap = public_result(run_cli(
        repo,
        CORE_CLI,
        "bootstrap",
        "--descriptor", f"@{decision_descriptor}",
        "--index-seed", f"@{decision_seed}",
        "--host", "codex",
        "--json",
    ))

    inventory, doctor = public_preflight(root, repo, prefix="assumption-init")
    assumption_plan = public_result(run_cli(
        repo,
        ASSUMPTION_CLI,
        "init",
        *preflight_arguments(inventory, doctor),
        "--json",
    ))
    assumption_descriptor = write_json(
        root / "assumption-descriptor.json",
        assumption_plan["owner_descriptor"],
        canonical=True,
    )
    assumption_seed = root / "assumption.index.md"
    assumption_seed.write_text(assumption_plan["index_seed"], encoding="utf-8")
    assumption_bootstrap = public_result(run_cli(
        repo,
        CORE_CLI,
        "bootstrap",
        "--descriptor", f"@{assumption_descriptor}",
        "--index-seed", f"@{assumption_seed}",
        "--host", "codex",
        "--json",
    ))

    inventory, doctor = public_preflight(root, repo, prefix="ready")
    core_capabilities = public_result(run_cli(repo, CORE_CLI, "capabilities", "--json"))
    decision_capabilities = public_result(run_cli(repo, DECISION_CLI, "capabilities", "--json"))
    assumption_capabilities = public_result(run_cli(repo, ASSUMPTION_CLI, "capabilities", "--json"))
    capabilities = {
        "schema": "context-owner-capabilities/v1",
        "owners": [
            *core_capabilities["owners"],
            *decision_capabilities["owners"],
            *assumption_capabilities["owners"],
        ],
    }
    return {
        "inventory": inventory,
        "doctor": doctor,
        "capabilities": capabilities,
        "decision_bootstrap": decision_bootstrap,
        "assumption_bootstrap": assumption_bootstrap,
        "assumption_descriptor": assumption_plan["owner_descriptor"],
    }


def bootstrap_term_owners(root: Path, repo: Path) -> dict:
    state = bootstrap_three_owners(root, repo)
    inventory, doctor = public_preflight(root, repo, prefix="term-init")
    term_plan = public_result(run_cli(
        repo,
        TERM_CLI,
        "init",
        *preflight_arguments(inventory, doctor),
        "--json",
    ))
    descriptor = write_json(
        root / "term-descriptor.json",
        term_plan["owner_descriptor"],
        canonical=True,
    )
    seed = root / "term.index.md"
    seed.write_text(term_plan["index_seed"], encoding="utf-8")
    term_bootstrap = public_result(run_cli(
        repo,
        CORE_CLI,
        "bootstrap",
        "--descriptor", f"@{descriptor}",
        "--index-seed", f"@{seed}",
        "--host", "codex",
        "--json",
    ))
    inventory, doctor = public_preflight(root, repo, prefix="term-ready")
    term_capabilities = public_result(run_cli(repo, TERM_CLI, "capabilities", "--json"))
    state.update({
        "inventory": inventory,
        "doctor": doctor,
        "term_bootstrap": term_bootstrap,
        "term_descriptor": term_plan["owner_descriptor"],
        "capabilities": {
            "schema": "context-owner-capabilities/v1",
            "owners": [*state["capabilities"]["owners"], *term_capabilities["owners"]],
        },
    })
    return state


def assumption_candidate(candidate_id: str = "cand_123e4567e89b42d3a456426614174100") -> dict:
    claim = "외부 인증 공급자의 장애율이 이번 분기에도 0.1% 미만일 것이다."
    return {
        "schema": "context-capture-candidate/v1",
        "candidate_id": candidate_id,
        "title": "인증 공급자 가용성 가정",
        "claim": claim,
        "summary": "인증 공급자의 최근 안정성이 당분간 유지된다고 가정한다.",
        "captured_from": "conversation",
        "requested_kind": "assumption",
        "specialized_kinds": ["assumption"],
        "fallback_kind": None,
        "scope_hint": "project/auth",
        "source_refs": ["conversation:test"],
        "search_terms": ["인증 공급자", "장애율"],
        "owner_inputs": {
            "assumption": {
                "assumption": claim,
                "basis": ["최근 90일 장애율이 0.1% 미만이었다."],
                "unverified_ok": True,
                "confirm_conditions": ["다음 분기 SLA 보고서를 확인한다."],
                "refute_conditions": ["장애율이 0.1% 이상이면 반증한다."],
            },
        },
    }


def assumption_attestation(value: dict) -> dict:
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


def term_candidate(candidate_id: str = "cand_123e4567e89b42d3a456426614174600") -> dict:
    definition = "이 프로젝트에서 browser session과 backend API 사이의 인증 경계를 소유하는 서비스다."
    return {
        "schema": "context-capture-candidate/v1",
        "candidate_id": candidate_id,
        "title": "BFF 프로젝트 용어",
        "claim": definition,
        "summary": "인증 아키텍처에서 사용하는 project-specific 의미를 고정한다.",
        "captured_from": "conversation",
        "requested_kind": "term",
        "specialized_kinds": ["term"],
        "fallback_kind": None,
        "scope_hint": "project/auth",
        "source_refs": ["conversation:test"],
        "search_terms": ["BFF", "terminology"],
        "owner_inputs": {
            "term": {
                "term": "BFF",
                "definition": definition,
                "project_signal": "project-special-meaning",
                "aliases": ["Backend for Frontend"],
                "deprecated_terms": ["API Facade"],
                "related": ["Session Owner"],
            },
        },
    }


def term_attestation(value: dict) -> dict:
    return {
        "schema": "context-semantic-attestation/v1",
        "operation": "claim",
        "input_schema": value["schema"],
        "input_digest": term_cli.canonical_digest(value),
        "assertions": [
            {"name": "term_identified", "value": True, "evidence_pointers": ["/owner_inputs/term/term"]},
            {"name": "definition_present", "value": True, "evidence_pointers": ["/owner_inputs/term/definition"]},
        ],
    }


def generic_decline(candidate: dict, capability: dict, reason: str) -> dict:
    return {
        "schema": "context-owner-result/v1",
        "result_type": "claim",
        "transition": "capture",
        "owner": capability["owner"],
        "target_kind": capability["kind"],
        "candidate_id": candidate["candidate_id"],
        "decision": "decline",
        "reason": reason,
        "capability_digest": context_cli.canonical_digest(capability),
        "semantic_inputs": [{
            "operation": "claim",
            "input_schema": candidate["schema"],
            "input_digest": context_cli.canonical_digest(candidate),
            "value": candidate,
        }],
        "semantic_attestations": [],
        "artifact_drafts": [],
        "effects": [],
        "proposed_plan": None,
    }


def initialize(repo: Path) -> None:
    core = context_cli.build_init_bundle(repo)
    context_cli.apply_bundle(repo, core["bundle"], core["approval_digest"])
    addon = decision_cli.build_init_plan()
    area = context_cli.build_area_register_bundle(repo, addon["owner_descriptor"], addon["index_seed"])
    context_cli.apply_bundle(repo, area["bundle"], area["approval_digest"])


def choice(candidate_id: str = "cand_550e8400e29b41d4a716446655440000", *, informed_by: list[str] | None = None) -> dict:
    value = {
        "schema": "context-capture-candidate/v1",
        "candidate_id": candidate_id,
        "title": "인증 세션 소유권",
        "claim": "인증 세션은 BFF가 소유한다.",
        "summary": "OAuth callback과 cookie 경계를 BFF로 통합한다.",
        "captured_from": "conversation",
        "requested_kind": None,
        "specialized_kinds": ["decision"],
        "fallback_kind": "observation",
        "scope_hint": "project/auth",
        "source_refs": ["conversation:test"],
        "search_terms": ["인증 주체", "세션 owner"],
        "evidence": ["결정 권한자가 현재 따를 선택으로 확정했다."],
        "owner_inputs": {
            "decision": {
                "decision": "인증 세션은 BFF가 소유한다.",
                "rationale": "브라우저별 cookie 차이를 서버 경계 안으로 모은다.",
                "rejected_alternatives": ["SPA token 소유: XSS 노출이 커져 반려"],
                "decision_key": "session-owner",
            },
            "observation": {
                "observation": "인증 세션은 BFF가 소유한다.",
                "evidence": ["결정 권한자가 현재 따를 선택으로 확정했다."],
            },
        },
    }
    if informed_by:
        value["informed_by"] = informed_by
    return value


def decision_attestation(value: dict) -> dict:
    return {
        "schema": "context-semantic-attestation/v1",
        "operation": "claim",
        "input_schema": value["schema"],
        "input_digest": decision_cli.canonical_digest(value),
        "assertions": [
            {"name": "explicit_choice", "value": True, "evidence_pointers": ["/owner_inputs/decision/decision"]},
            {"name": "scope_identified", "value": True, "evidence_pointers": ["/scope_hint"]},
            {"name": "commitment_present", "value": True, "evidence_pointers": ["/evidence/0"]},
        ],
    }


def obs_attestation(value: dict) -> dict:
    return {
        "schema": "context-semantic-attestation/v1",
        "operation": "claim",
        "input_schema": value["schema"],
        "input_digest": context_cli.canonical_digest(value),
        "assertions": [
            {"name": "reusable_observation", "value": True, "evidence_pointers": ["/owner_inputs/observation/observation"]},
            {"name": "evidence_present", "value": True, "evidence_pointers": ["/owner_inputs/observation/evidence/0"]},
        ],
    }


class CrossPluginFlowTests(unittest.TestCase):
    def test_acceptance_18_owner_installed(self) -> None:
        value = choice()
        owner_result = decision_cli.build_claim_result(value, decision_attestation(value))
        capabilities = context_cli.capabilities_result()
        capabilities["owners"].append(decision_cli.decision_capability())
        routed = context_cli.route_candidates([value], capabilities, [owner_result])
        self.assertEqual(1, len(routed["routes"]))
        self.assertEqual("context-decision", routed["routes"][0]["owner"])
        self.assertEqual("decision", routed["routes"][0]["target_kind"])

    def test_acceptance_21_independent_claims(self) -> None:
        decision = choice()
        observation = {
            **choice("cand_123e4567e89b42d3a456426614174000"),
            "requested_kind": "observation",
            "specialized_kinds": ["observation"],
            "fallback_kind": None,
            "claim": decision["claim"],
        }
        observation["owner_inputs"] = {"observation": {"observation": observation["claim"], "evidence": ["재현 fixture"]}}
        dec_result = decision_cli.build_claim_result(decision, decision_attestation(decision))
        obs_result = context_cli.draft_owner_result(observation, obs_attestation(observation))
        capabilities = context_cli.capabilities_result()
        capabilities["owners"].append(decision_cli.decision_capability())
        routed = context_cli.route_candidates([observation, decision], capabilities, [obs_result, dec_result])
        self.assertEqual(["observation", "decision"], [item["target_kind"] for item in routed["routes"]])

    def test_acceptance_31_evidence_relation(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            repo = Path(temp)
            initialize(repo)
            obs_candidate = {
                **choice("cand_123e4567e89b42d3a456426614174000"),
                "requested_kind": "observation",
                "specialized_kinds": ["observation"],
                "fallback_kind": None,
                "title": "Safari cookie 관찰",
                "claim": "Safari에서 third-party cookie가 차단된다.",
                "summary": "Safari cookie 제한을 재현했다.",
            }
            obs_candidate["owner_inputs"] = {"observation": {"observation": obs_candidate["claim"], "evidence": ["재현 fixture"]}}
            obs_result = context_cli.draft_owner_result(obs_candidate, obs_attestation(obs_candidate), now="2026-08-14T09:00:00+09:00")
            obs_bundle = context_cli.finalize_owner_result(repo, obs_result)
            context_cli.apply_bundle(repo, obs_bundle["bundle"], obs_bundle["approval_digest"])
            obs_id = obs_result["effects"][0]["id"]

            value = choice(informed_by=[obs_id])
            dec_result = decision_cli.build_claim_result(value, decision_attestation(value), repo=repo)
            receipt = decision_cli.validate_batch(repo, dec_result)
            dec_bundle = context_cli.finalize_owner_result(repo, dec_result, receipt)
            context_cli.apply_bundle(repo, dec_bundle["bundle"], dec_bundle["approval_digest"])
            self.assertEqual("current", context_cli.observation_read(repo, obs_id)["state"])
            dec_id = dec_result["effects"][0]["id"]
            decision = decision_cli.read_decision(repo, dec_id)
            self.assertEqual([obs_id], decision_cli.find_current(repo, dec_id)["frontmatter"]["relations"]["informed_by"])

    def test_acceptance_32_fallback_import(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            repo = Path(temp)
            initialize(repo)
            fallback = choice()
            fallback["requested_kind"] = "observation"
            fallback["specialized_kinds"] = ["observation"]
            fallback["fallback_kind"] = None
            fallback["kind_hint"] = "decision"
            fallback["owner_inputs"] = {"observation": fallback["owner_inputs"]["observation"]}
            obs_result = context_cli.draft_owner_result(fallback, obs_attestation(fallback), now="2026-08-14T09:00:00+09:00")
            obs_bundle = context_cli.finalize_owner_result(repo, obs_result)
            context_cli.apply_bundle(repo, obs_bundle["bundle"], obs_bundle["approval_digest"])
            obs_id = obs_result["effects"][0]["id"]

            dec_candidate = choice("cand_123e4567e89b42d3a456426614174000")
            dec_result = decision_cli.build_claim_result(
                dec_candidate, decision_attestation(dec_candidate), repo=repo,
                created_at="2026-08-14T09:00:00+09:00",
            )
            lifecycle = context_cli.prepare_lifecycle_input(repo, "decision_fallback_import", obs_id, dec_result)
            same_claim = {
                "schema": "context-semantic-attestation/v1",
                "operation": "same_claim",
                "input_schema": lifecycle["schema"],
                "input_digest": decision_cli.canonical_digest(lifecycle),
                "assertions": [{"name": "same_semantic_claim", "value": True, "evidence_pointers": ["/predecessor/primary_claim", "/successor/primary_claim"]}],
            }
            imported = decision_cli.build_fallback_import_result(repo, obs_id, dec_result, lifecycle, same_claim, retired_at="2026-08-14T10:00:00+09:00")
            receipt = decision_cli.validate_batch(repo, imported)
            bundle = context_cli.finalize_owner_result(repo, imported, receipt)
            self.assertEqual({"decision", "observation"}, {effect["area"] for effect in bundle["approval_preview"]["effects"]})
            context_cli.apply_bundle(repo, bundle["bundle"], bundle["approval_digest"])
            self.assertEqual("history", context_cli.observation_read(repo, obs_id)["state"])
            dec_id = next(effect["id"] for effect in imported["effects"] if effect["area"] == "decision")
            self.assertIn(obs_id, decision_cli.find_current(repo, dec_id)["frontmatter"]["supersedes"])

    def test_acceptance_37_parallel(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            repo = Path(temp)
            core = context_cli.build_init_bundle(repo)
            context_cli.apply_bundle(repo, core["bundle"], core["approval_digest"])

            def make(number: int) -> dict:
                value = {
                    **choice(f"cand_550e8400e29b41d4a71644665544{number:04x}"),
                    "requested_kind": "observation",
                    "specialized_kinds": ["observation"],
                    "fallback_kind": None,
                    "title": f"병렬 관찰 {number}",
                    "claim": f"병렬 claim {number}",
                    "summary": f"병렬 capture {number}",
                }
                value["owner_inputs"] = {"observation": {"observation": value["claim"], "evidence": [f"fixture {number}"]}}
                return context_cli.draft_owner_result(value, obs_attestation(value), now=f"2026-08-14T09:00:0{number}+09:00")

            with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
                results = list(pool.map(make, (1, 2)))
            bundles = []
            for result in results:
                bundle = context_cli.finalize_owner_result(repo, result, prior_bundles=bundles)
                context_cli.apply_bundle(repo, bundle["bundle"], bundle["approval_digest"])
                bundles.append(bundle["bundle"])
            index = context_cli.parse_area_index((repo / "context/observation/observation.index.md").read_text(encoding="utf-8"))
            self.assertEqual(2, len(index.current))
            self.assertEqual(2, len({row["id"] for row in index.current}))

    def test_acceptance_44_decision_init_bootstraps_absent_core(self) -> None:
        fixtures = ROOT / "tests/context-v1/fixtures/host-inventory"
        case = next(
            item
            for item in json.loads((fixtures / "preflight-cases.json").read_text(encoding="utf-8"))["cases"]
            if item["expected_code"] == "core_uninitialized"
        )
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            repo = root / "repository"
            repo.mkdir()
            (repo / "keep.txt").write_text("preserve\n", encoding="utf-8")
            completed = run_cli(
                repo,
                DECISION_INIT,
                "--host",
                case["host"],
                "--core-cli",
                str(CORE_CLI),
                "--json",
            )
            self.assertEqual(0, completed.returncode, completed.stdout + completed.stderr)
            result = json.loads(completed.stdout)["result"]
            self.assertEqual("context-decision-init-result/v1", result["schema"])
            self.assertEqual("absent", result["core_repository_state_before"])
            self.assertEqual(
                [("core_init", "applied"), ("area_register", "applied"), ("policy_install", "applied")],
                [(phase["phase"], phase["status"]) for phase in result["phases"]],
            )
            self.assertEqual("ready", result["doctor"]["repository_state"])
            self.assertTrue((repo / "context/decision/decision.index.md").is_file())
            self.assertEqual("preserve\n", (repo / "keep.txt").read_text(encoding="utf-8"))
            self.assertIn(context_cli.POLICY_BODY, (repo / "AGENTS.md").read_text(encoding="utf-8"))
            self.assertFalse((repo / "CLAUDE.md").exists())

            repeated = run_cli(
                repo,
                DECISION_INIT,
                "--host",
                case["host"],
                "--core-cli",
                str(CORE_CLI),
                "--json",
            )
            self.assertEqual(0, repeated.returncode, repeated.stdout + repeated.stderr)
            self.assertEqual(
                ["noop", "noop", "noop"],
                [phase["status"] for phase in json.loads(repeated.stdout)["result"]["phases"]],
            )

    def test_acceptance_45_bootstrap_phase_failure_retries(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            repo = root / "repository"
            repo.mkdir()
            plan = decision_cli.build_init_plan()
            descriptor = root / "descriptor.json"
            invalid_seed = root / "invalid.index.md"
            valid_seed = root / "decision.index.md"
            descriptor.write_text(json.dumps(plan["owner_descriptor"], ensure_ascii=False), encoding="utf-8")
            invalid_seed.write_text(plan["index_seed"].replace('owner: "context-decision"', 'owner: "other"'), encoding="utf-8")
            valid_seed.write_text(plan["index_seed"], encoding="utf-8")

            failed = run_cli(
                repo,
                CORE_CLI,
                "bootstrap",
                "--descriptor",
                f"@{descriptor}",
                "--index-seed",
                f"@{invalid_seed}",
                "--host",
                "codex",
                "--json",
            )
            self.assertEqual(5, failed.returncode, failed.stdout + failed.stderr)
            error = json.loads(failed.stdout)["error"]
            self.assertEqual("index_seed_invalid", error["code"])
            self.assertEqual(
                [("core_init", "applied"), ("area_register", "failed")],
                [(phase["phase"], phase["status"]) for phase in error["details"]["phases"]],
            )
            self.assertEqual("ready", context_cli.doctor_repository(repo)["repository_state"])
            self.assertFalse((repo / "context/decision/decision.index.md").exists())

            retried = run_cli(
                repo,
                CORE_CLI,
                "bootstrap",
                "--descriptor",
                f"@{descriptor}",
                "--index-seed",
                f"@{valid_seed}",
                "--host",
                "codex",
                "--json",
            )
            self.assertEqual(0, retried.returncode, retried.stdout + retried.stderr)
            self.assertEqual(
                [("core_init", "noop"), ("area_register", "applied"), ("policy_install", "applied")],
                [(phase["phase"], phase["status"]) for phase in json.loads(retried.stdout)["result"]["phases"]],
            )
            self.assertIn(context_cli.POLICY_BODY, (repo / "AGENTS.md").read_text(encoding="utf-8"))

    def test_acceptance_57_three_owner_mixed_registration(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            repo = root / "repository"
            repo.mkdir()
            state = bootstrap_three_owners(root, repo)

            self.assertEqual("applied", state["decision_bootstrap"]["phases"][1]["status"])
            self.assertEqual("applied", state["assumption_bootstrap"]["phases"][1]["status"])
            doctor = public_result(run_cli(repo, CORE_CLI, "doctor", "--json"))
            self.assertEqual("ready", doctor["repository_state"])
            self.assertEqual([], doctor["issues"])

            root_index = (repo / context_cli.ROOT_INDEX).read_text(encoding="utf-8")
            self.assertEqual(
                ["assumption"],
                [item["area"] for item in context_cli.parse_root_profiles(root_index)],
            )
            self.assertEqual(
                state["assumption_descriptor"],
                context_cli.parse_area_profile(
                    (repo / "context/assumption/assumption.index.md").read_text(encoding="utf-8")
                ),
            )
            self.assertIsNone(
                context_cli.parse_area_profile(
                    (repo / "context/decision/decision.index.md").read_text(encoding="utf-8")
                )
            )
            recalled = public_result(run_cli(
                repo,
                CORE_CLI,
                "recall",
                "--area", "decision",
                "--area", "assumption",
                "--json",
            ))
            self.assertEqual([], recalled["items"])
            self.assertEqual(
                {"context-core", "context-decision", "context-assumption"},
                {item["owner"] for item in state["capabilities"]["owners"]},
            )

    def test_acceptance_58_assumption_routing_boundaries(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            repo = root / "repository"
            repo.mkdir()
            state = bootstrap_three_owners(root, repo)
            before = repository_bytes(repo)
            preflight = preflight_arguments(state["inventory"], state["doctor"])
            capabilities_path = write_json(root / "capabilities.json", state["capabilities"])

            decision = choice("cand_123e4567e89b42d3a456426614174201")
            decision_path = write_json(root / "decision-candidate.json", decision)
            decision_attestation_path = write_json(
                root / "decision-attestation.json", decision_attestation(decision)
            )
            decision_claim = public_result(run_cli(
                repo,
                DECISION_CLI,
                "capture",
                "--candidate", f"@{decision_path}",
                "--attestation", f"@{decision_attestation_path}",
                *preflight,
                "--json",
            ))
            assumption_decline = public_result(run_cli(
                repo,
                ASSUMPTION_CLI,
                "decline",
                "--candidate", f"@{decision_path}",
                "--reason", "authoritative choice belongs to DEC",
                *preflight,
                "--json",
            ))
            self.assertEqual("decline", assumption_decline["decision"])

            decision_batch = write_json(root / "decision-batch.json", {
                "schema": "context-capture-batch/v1",
                "audit_count": 1,
                "candidates": [decision],
            })
            decision_results = write_json(
                root / "decision-results.json", [decision_claim, assumption_decline]
            )
            routed_decision = public_result(run_cli(
                repo,
                CORE_CLI,
                "candidate",
                "route",
                "--batch", f"@{decision_batch}",
                "--capabilities", f"@{capabilities_path}",
                "--claim-results", f"@{decision_results}",
                "--json",
            ))
            self.assertEqual("proposed", routed_decision["routes"][0]["status"])
            self.assertEqual("context-decision", routed_decision["routes"][0]["owner"])

            assumption = assumption_candidate()
            assumption_path = write_json(root / "assumption-candidate.json", assumption)
            assumption_attestation_path = write_json(
                root / "assumption-attestation.json", assumption_attestation(assumption)
            )
            assumption_claim = public_result(run_cli(
                repo,
                ASSUMPTION_CLI,
                "claim",
                "--candidate", f"@{assumption_path}",
                "--attestation", f"@{assumption_attestation_path}",
                "--route-only",
                *preflight,
                "--json",
            ))
            by_kind = {item["kind"]: item for item in state["capabilities"]["owners"]}
            decision_decline = generic_decline(
                assumption, by_kind["decision"], "unverified premise is outside DEC authority"
            )
            observation_decline = generic_decline(
                assumption, by_kind["observation"], "unverified premise is not observed evidence"
            )
            self.assertEqual(
                ["claim", "decline", "decline"],
                [assumption_claim["decision"], decision_decline["decision"], observation_decline["decision"]],
            )
            assumption_batch = write_json(root / "assumption-batch.json", {
                "schema": "context-capture-batch/v1",
                "audit_count": 1,
                "candidates": [assumption],
            })
            assumption_results = write_json(
                root / "assumption-results.json",
                [assumption_claim, decision_decline, observation_decline],
            )
            routed_assumption = public_result(run_cli(
                repo,
                CORE_CLI,
                "candidate",
                "route",
                "--batch", f"@{assumption_batch}",
                "--capabilities", f"@{capabilities_path}",
                "--claim-results", f"@{assumption_results}",
                "--json",
            ))
            self.assertEqual("context-assumption", routed_assumption["routes"][0]["owner"])
            self.assertEqual("provisional", routed_assumption["routes"][0]["authority"])

            observation = {
                **choice("cand_123e4567e89b42d3a456426614174202"),
                "requested_kind": "observation",
                "specialized_kinds": ["observation"],
                "fallback_kind": None,
            }
            observation["owner_inputs"] = {
                "observation": {
                    "observation": "Safari에서 third-party cookie가 차단된다.",
                    "evidence": ["재현 fixture"],
                }
            }
            observation["claim"] = observation["owner_inputs"]["observation"]["observation"]
            for label, candidate in (("observation", observation), ("decision", decision)):
                candidate_path = write_json(root / f"explicit-{label}.json", candidate)
                declined = public_result(run_cli(
                    repo,
                    ASSUMPTION_CLI,
                    "decline",
                    "--candidate", f"@{candidate_path}",
                    "--reason", f"explicit {label} semantic boundary",
                    *preflight,
                    "--json",
                ))
                self.assertEqual("decline", declined["decision"])
                self.assertEqual("context-assumption", declined["owner"])
            self.assertEqual(before, repository_bytes(repo))

    def test_acceptance_59_assumption_owner_conflict(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            repo = root / "repository"
            repo.mkdir()
            state = bootstrap_three_owners(root, repo)
            before = repository_bytes(repo)
            preflight = preflight_arguments(state["inventory"], state["doctor"])

            base = assumption_candidate("cand_123e4567e89b42d3a456426614174301")
            base_path = write_json(root / "base-assumption.json", base)
            base_attestation = write_json(root / "base-assumption-attestation.json", assumption_attestation(base))
            actual_assumption_claim = public_result(run_cli(
                repo,
                ASSUMPTION_CLI,
                "claim",
                "--candidate", f"@{base_path}",
                "--attestation", f"@{base_attestation}",
                "--route-only",
                *preflight,
                "--json",
            ))

            dual = choice(base["candidate_id"])
            dual.update({
                "title": base["title"],
                "claim": base["claim"],
                "summary": base["summary"],
                "requested_kind": None,
                "specialized_kinds": ["assumption", "decision"],
                "fallback_kind": None,
                "scope_hint": base["scope_hint"],
                "owner_inputs": {
                    "assumption": base["owner_inputs"]["assumption"],
                    "decision": {
                        **dual["owner_inputs"]["decision"],
                        "decision": base["claim"],
                    },
                },
            })
            dual_path = write_json(root / "dual-candidate.json", dual)
            dual_decision_attestation = write_json(
                root / "dual-decision-attestation.json", decision_attestation(dual)
            )
            decision_claim = public_result(run_cli(
                repo,
                DECISION_CLI,
                "capture",
                "--candidate", f"@{dual_path}",
                "--attestation", f"@{dual_decision_attestation}",
                *preflight,
                "--json",
            ))
            real_assumption_outcome = public_result(run_cli(
                repo,
                ASSUMPTION_CLI,
                "claim",
                "--candidate", f"@{dual_path}",
                "--attestation", f"@{base_attestation}",
                "--route-only",
                *preflight,
                "--json",
            ))
            self.assertEqual("decline", real_assumption_outcome["decision"])

            faulty_assumption_claim = copy.deepcopy(actual_assumption_claim)
            digest = assumption_cli.canonical_digest(dual)
            faulty_assumption_claim["semantic_inputs"][0].update({
                "input_digest": digest,
                "value": dual,
            })
            faulty_assumption_claim["semantic_attestations"][0]["input_digest"] = digest
            capabilities_path = write_json(root / "conflict-capabilities.json", state["capabilities"])
            batch_path = write_json(root / "conflict-batch.json", {
                "schema": "context-capture-batch/v1",
                "audit_count": 1,
                "candidates": [dual],
            })
            results_path = write_json(
                root / "conflict-results.json", [faulty_assumption_claim, decision_claim]
            )
            routed = public_result(run_cli(
                repo,
                CORE_CLI,
                "candidate",
                "route",
                "--batch", f"@{batch_path}",
                "--capabilities", f"@{capabilities_path}",
                "--claim-results", f"@{results_path}",
                "--json",
            ))
            self.assertEqual("owner_conflict", routed["routes"][0]["status"])
            self.assertEqual("multiple_specialized_owners_claimed", routed["routes"][0]["reason"])
            self.assertEqual(routed["routes"], routed["conflicts"])
            self.assertEqual(before, repository_bytes(repo))

    def test_acceptance_60_assumption_approval_transaction(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            repo = root / "repository"
            repo.mkdir()
            state = bootstrap_three_owners(root, repo)
            preflight = preflight_arguments(state["inventory"], state["doctor"])
            candidate = assumption_candidate("cand_123e4567e89b42d3a456426614174401")
            candidate_path = write_json(root / "capture-candidate.json", candidate)
            attestation_path = write_json(root / "capture-attestation.json", assumption_attestation(candidate))
            owner_result = public_result(run_cli(
                repo,
                ASSUMPTION_CLI,
                "claim",
                "--candidate", f"@{candidate_path}",
                "--attestation", f"@{attestation_path}",
                "--identifier", "ctx_550e8400e29b41d4a716446655440040",
                "--created-at", "2026-08-22T09:00:00+09:00",
                *preflight,
                "--json",
            ))
            owner_result_path = write_json(root / "capture-owner-result.json", owner_result)
            receipt = public_result(run_cli(
                repo,
                ASSUMPTION_CLI,
                "batch",
                "validate",
                "--owner-result", f"@{owner_result_path}",
                *preflight,
                "--json",
            ))
            receipt_path = write_json(root / "capture-receipt.json", receipt)

            before_preview = repository_bytes(repo)
            preview = public_result(run_cli(
                repo,
                CORE_CLI,
                "transaction",
                "preview",
                "--owner-result", f"@{owner_result_path}",
                "--owner-validation", f"@{receipt_path}",
                "--json",
            ))
            self.assertFalse(preview["applied"])
            self.assertEqual(before_preview, repository_bytes(repo))
            bundle_path = write_json(root / "capture-bundle.json", preview["bundle"])

            rejected = run_cli(
                repo,
                CORE_CLI,
                "transaction",
                "apply",
                "--plan-bundle", f"@{bundle_path}",
                "--approved-digest", "sha256:" + "0" * 64,
                "--json",
            )
            self.assertEqual(5, rejected.returncode, rejected.stdout + rejected.stderr)
            self.assertEqual("approval_digest_mismatch", json.loads(rejected.stdout)["error"]["code"])
            self.assertEqual(before_preview, repository_bytes(repo))

            applied = public_result(run_cli(
                repo,
                CORE_CLI,
                "transaction",
                "apply",
                "--plan-bundle", f"@{bundle_path}",
                "--approved-digest", preview["approval_digest"],
                "--json",
            ))
            self.assertTrue(applied["applied"])
            read = public_result(run_cli(
                repo,
                ASSUMPTION_CLI,
                "read",
                "--signal", "assumption-relevant",
                "--id", "ctx_550e8400e29b41d4a716446655440040",
                *preflight,
                "--json",
            ))
            self.assertEqual("provisional", read["authority"])
            self.assertEqual(candidate["claim"], read["sections"]["Assumption"])
            self.assertEqual("ready", public_result(run_cli(repo, CORE_CLI, "doctor", "--json"))["repository_state"])

    def test_acceptance_61_assumption_receipt_spoof_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            repo = root / "repository"
            repo.mkdir()
            state = bootstrap_three_owners(root, repo)
            preflight = preflight_arguments(state["inventory"], state["doctor"])
            candidate = assumption_candidate("cand_123e4567e89b42d3a456426614174501")
            candidate_path = write_json(root / "spoof-candidate.json", candidate)
            attestation_path = write_json(root / "spoof-attestation.json", assumption_attestation(candidate))
            owner_result = public_result(run_cli(
                repo,
                ASSUMPTION_CLI,
                "claim",
                "--candidate", f"@{candidate_path}",
                "--attestation", f"@{attestation_path}",
                "--identifier", "ctx_550e8400e29b41d4a716446655440050",
                "--created-at", "2026-08-22T09:00:00+09:00",
                *preflight,
                "--json",
            ))
            owner_result_path = write_json(root / "spoof-owner-result.json", owner_result)
            receipt = public_result(run_cli(
                repo,
                ASSUMPTION_CLI,
                "batch",
                "validate",
                "--owner-result", f"@{owner_result_path}",
                *preflight,
                "--json",
            ))
            before = repository_bytes(repo)

            spoofed_receipt = copy.deepcopy(receipt)
            spoofed_receipt["descriptor_digest"] = "sha256:" + "f" * 64
            spoofed_receipt_path = write_json(root / "spoofed-receipt.json", spoofed_receipt)
            valid_result_path = write_json(root / "valid-owner-result.json", owner_result)
            rejected_receipt = run_cli(
                repo,
                CORE_CLI,
                "transaction",
                "preview",
                "--owner-result", f"@{valid_result_path}",
                "--owner-validation", f"@{spoofed_receipt_path}",
                "--json",
            )
            self.assertEqual(5, rejected_receipt.returncode, rejected_receipt.stdout + rejected_receipt.stderr)
            self.assertEqual("owner_validation_invalid", json.loads(rejected_receipt.stdout)["error"]["code"])
            self.assertEqual(before, repository_bytes(repo))

            spoofed_result = copy.deepcopy(owner_result)
            spoofed_result["artifact_drafts"][0]["content"] += "\n"
            spoofed_result_path = write_json(root / "spoofed-owner-result.json", spoofed_result)
            valid_receipt_path = write_json(root / "valid-receipt.json", receipt)
            rejected_result = run_cli(
                repo,
                CORE_CLI,
                "transaction",
                "preview",
                "--owner-result", f"@{spoofed_result_path}",
                "--owner-validation", f"@{valid_receipt_path}",
                "--json",
            )
            self.assertEqual(5, rejected_result.returncode, rejected_result.stdout + rejected_result.stderr)
            self.assertEqual("owner_validation_invalid", json.loads(rejected_result.stdout)["error"]["code"])
            self.assertEqual(before, repository_bytes(repo))

    def test_acceptance_62_term_mixed_registration_and_routing(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            repo = root / "repository"
            repo.mkdir()
            state = bootstrap_term_owners(root, repo)
            self.assertEqual("applied", state["term_bootstrap"]["phases"][1]["status"])
            doctor = public_result(run_cli(repo, CORE_CLI, "doctor", "--json"))
            self.assertEqual(("ready", []), (doctor["repository_state"], doctor["issues"]))
            root_index = (repo / context_cli.ROOT_INDEX).read_text(encoding="utf-8")
            self.assertEqual(
                {"assumption", "term"},
                {item["area"] for item in context_cli.parse_root_profiles(root_index)},
            )
            self.assertEqual(
                state["term_descriptor"],
                context_cli.parse_area_profile(
                    (repo / "context/term/term.index.md").read_text(encoding="utf-8")
                ),
            )
            self.assertIsNone(context_cli.parse_area_profile(
                (repo / "context/decision/decision.index.md").read_text(encoding="utf-8")
            ))
            self.assertEqual(
                {"archive", "observation", "snapshot", "decision", "assumption", "term"},
                {item["kind"] for item in state["capabilities"]["owners"]},
            )

            before = repository_bytes(repo)
            preflight = preflight_arguments(state["inventory"], state["doctor"])
            candidate = term_candidate()
            candidate_path = write_json(root / "term-route-candidate.json", candidate)
            proof_path = write_json(root / "term-route-attestation.json", term_attestation(candidate))
            term_claim = public_result(run_cli(
                repo,
                TERM_CLI,
                "claim",
                "--candidate", f"@{candidate_path}",
                "--attestation", f"@{proof_path}",
                "--route-only",
                *preflight,
                "--json",
            ))
            by_kind = {item["kind"]: item for item in state["capabilities"]["owners"]}
            decision_decline = generic_decline(
                candidate,
                by_kind["decision"],
                "project terminology is outside DEC authority",
            )
            assumption_decline = generic_decline(
                candidate,
                by_kind["assumption"],
                "authoritative terminology is outside ASM authority",
            )
            observation_decline = generic_decline(
                candidate,
                by_kind["observation"],
                "terminology definition is not an observed evidence claim",
            )
            batch_path = write_json(root / "term-route-batch.json", {
                "schema": "context-capture-batch/v1",
                "audit_count": 1,
                "candidates": [candidate],
            })
            results_path = write_json(
                root / "term-route-results.json",
                [term_claim, decision_decline, assumption_decline, observation_decline],
            )
            capabilities_path = write_json(root / "term-route-capabilities.json", state["capabilities"])
            routed = public_result(run_cli(
                repo,
                CORE_CLI,
                "candidate",
                "route",
                "--batch", f"@{batch_path}",
                "--capabilities", f"@{capabilities_path}",
                "--claim-results", f"@{results_path}",
                "--json",
            ))
            self.assertEqual("proposed", routed["routes"][0]["status"])
            self.assertEqual("context-term", routed["routes"][0]["owner"])
            self.assertEqual("authoritative", routed["routes"][0]["authority"])
            self.assertEqual(
                ["claim", "decline", "decline", "decline"],
                [term_claim["decision"], decision_decline["decision"], assumption_decline["decision"], observation_decline["decision"]],
            )

            observation = copy.deepcopy(choice("cand_123e4567e89b42d3a456426614174601"))
            observation.update({
                "requested_kind": "observation",
                "specialized_kinds": ["observation"],
                "fallback_kind": None,
                "claim": "Safari에서 third-party cookie가 차단된다.",
                "owner_inputs": {
                    "observation": {
                        "observation": "Safari에서 third-party cookie가 차단된다.",
                        "evidence": ["재현 fixture"],
                    },
                },
            })
            explicit = {
                "observation": observation,
                "decision": choice("cand_123e4567e89b42d3a456426614174602"),
                "assumption": assumption_candidate("cand_123e4567e89b42d3a456426614174603"),
            }
            for label, foreign in explicit.items():
                foreign_path = write_json(root / f"term-decline-{label}.json", foreign)
                declined = public_result(run_cli(
                    repo,
                    TERM_CLI,
                    "claim",
                    "--candidate", f"@{foreign_path}",
                    "--attestation", f"@{proof_path}",
                    "--route-only",
                    *preflight,
                    "--json",
                ))
                self.assertEqual("decline", declined["decision"])
                self.assertEqual(foreign, declined["semantic_inputs"][0]["value"])
            self.assertEqual(before, repository_bytes(repo))

    def test_acceptance_63_term_owner_conflict(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            repo = root / "repository"
            repo.mkdir()
            state = bootstrap_term_owners(root, repo)
            before = repository_bytes(repo)
            preflight = preflight_arguments(state["inventory"], state["doctor"])

            base = term_candidate("cand_123e4567e89b42d3a456426614174610")
            base_path = write_json(root / "term-conflict-base.json", base)
            base_proof = write_json(root / "term-conflict-base-proof.json", term_attestation(base))
            actual_term_claim = public_result(run_cli(
                repo,
                TERM_CLI,
                "claim",
                "--candidate", f"@{base_path}",
                "--attestation", f"@{base_proof}",
                "--route-only",
                *preflight,
                "--json",
            ))
            dual = copy.deepcopy(base)
            dual.update({
                "requested_kind": None,
                "specialized_kinds": ["term", "decision"],
                "evidence": ["결정 권한자가 현재 따를 선택으로 확정했다."],
            })
            dual["owner_inputs"]["decision"] = {
                "decision": dual["claim"],
                "rationale": "프로젝트 내부에서 이 이름과 정의를 현재 표준으로 사용한다.",
                "rejected_alternatives": ["API Facade: 다른 의미와 충돌해 반려"],
                "decision_key": "bff-terminology",
            }
            dual_path = write_json(root / "term-conflict-dual.json", dual)
            decision_proof = write_json(root / "term-conflict-decision-proof.json", decision_attestation(dual))
            decision_claim = public_result(run_cli(
                repo,
                DECISION_CLI,
                "capture",
                "--candidate", f"@{dual_path}",
                "--attestation", f"@{decision_proof}",
                *preflight,
                "--json",
            ))
            dual_term_proof = write_json(root / "term-conflict-term-proof.json", term_attestation(dual))
            real_term_outcome = public_result(run_cli(
                repo,
                TERM_CLI,
                "claim",
                "--candidate", f"@{dual_path}",
                "--attestation", f"@{dual_term_proof}",
                "--route-only",
                *preflight,
                "--json",
            ))
            self.assertEqual("decline", real_term_outcome["decision"])

            faulty_term_claim = copy.deepcopy(actual_term_claim)
            digest = term_cli.canonical_digest(dual)
            faulty_term_claim["semantic_inputs"][0].update({"input_digest": digest, "value": dual})
            faulty_term_claim["semantic_attestations"][0] = term_attestation(dual)
            capabilities_path = write_json(root / "term-conflict-capabilities.json", state["capabilities"])
            batch_path = write_json(root / "term-conflict-batch.json", {
                "schema": "context-capture-batch/v1",
                "audit_count": 1,
                "candidates": [dual],
            })
            results_path = write_json(root / "term-conflict-results.json", [faulty_term_claim, decision_claim])
            routed = public_result(run_cli(
                repo,
                CORE_CLI,
                "candidate",
                "route",
                "--batch", f"@{batch_path}",
                "--capabilities", f"@{capabilities_path}",
                "--claim-results", f"@{results_path}",
                "--json",
            ))
            self.assertEqual("owner_conflict", routed["routes"][0]["status"])
            self.assertEqual("multiple_specialized_owners_claimed", routed["routes"][0]["reason"])
            self.assertEqual(routed["routes"], routed["conflicts"])
            self.assertEqual(before, repository_bytes(repo))

    def test_acceptance_64_term_approval_transaction(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            repo = root / "repository"
            repo.mkdir()
            state = bootstrap_term_owners(root, repo)
            preflight = preflight_arguments(state["inventory"], state["doctor"])
            candidate = term_candidate("cand_123e4567e89b42d3a456426614174620")
            candidate_path = write_json(root / "term-capture-candidate.json", candidate)
            proof_path = write_json(root / "term-capture-proof.json", term_attestation(candidate))
            owner_result = public_result(run_cli(
                repo,
                TERM_CLI,
                "claim",
                "--candidate", f"@{candidate_path}",
                "--attestation", f"@{proof_path}",
                "--identifier", "ctx_550e8400e29b41d4a716446655440060",
                "--created-at", "2026-08-22T09:00:00+09:00",
                *preflight,
                "--json",
            ))
            owner_result_path = write_json(root / "term-capture-result.json", owner_result)
            receipt = public_result(run_cli(
                repo,
                TERM_CLI,
                "batch",
                "validate",
                "--owner-result", f"@{owner_result_path}",
                *preflight,
                "--json",
            ))
            receipt_path = write_json(root / "term-capture-receipt.json", receipt)
            before_preview = repository_bytes(repo)
            preview = public_result(run_cli(
                repo,
                CORE_CLI,
                "transaction",
                "preview",
                "--owner-result", f"@{owner_result_path}",
                "--owner-validation", f"@{receipt_path}",
                "--json",
            ))
            self.assertFalse(preview["applied"])
            self.assertEqual(before_preview, repository_bytes(repo))
            bundle_path = write_json(root / "term-capture-bundle.json", preview["bundle"])
            rejected = run_cli(
                repo,
                CORE_CLI,
                "transaction",
                "apply",
                "--plan-bundle", f"@{bundle_path}",
                "--approved-digest", "sha256:" + "0" * 64,
                "--json",
            )
            self.assertEqual(5, rejected.returncode, rejected.stdout + rejected.stderr)
            self.assertEqual("approval_digest_mismatch", json.loads(rejected.stdout)["error"]["code"])
            self.assertEqual(before_preview, repository_bytes(repo))
            applied = public_result(run_cli(
                repo,
                CORE_CLI,
                "transaction",
                "apply",
                "--plan-bundle", f"@{bundle_path}",
                "--approved-digest", preview["approval_digest"],
                "--json",
            ))
            self.assertTrue(applied["applied"])
            read = public_result(run_cli(
                repo,
                TERM_CLI,
                "read",
                "--signal", term_cli.SIGNAL,
                "--id", "ctx_550e8400e29b41d4a716446655440060",
                *preflight,
                "--json",
            ))
            self.assertEqual("authoritative", read["authority"])
            self.assertEqual(candidate["claim"], read["sections"]["Definition"])
            self.assertEqual("ready", public_result(run_cli(repo, CORE_CLI, "doctor", "--json"))["repository_state"])

    def test_acceptance_65_term_receipt_spoof_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            repo = root / "repository"
            repo.mkdir()
            state = bootstrap_term_owners(root, repo)
            preflight = preflight_arguments(state["inventory"], state["doctor"])
            candidate = term_candidate("cand_123e4567e89b42d3a456426614174630")
            candidate_path = write_json(root / "term-spoof-candidate.json", candidate)
            proof_path = write_json(root / "term-spoof-proof.json", term_attestation(candidate))
            owner_result = public_result(run_cli(
                repo,
                TERM_CLI,
                "claim",
                "--candidate", f"@{candidate_path}",
                "--attestation", f"@{proof_path}",
                "--identifier", "ctx_550e8400e29b41d4a716446655440061",
                "--created-at", "2026-08-22T09:00:00+09:00",
                *preflight,
                "--json",
            ))
            owner_result_path = write_json(root / "term-spoof-result.json", owner_result)
            receipt = public_result(run_cli(
                repo,
                TERM_CLI,
                "batch",
                "validate",
                "--owner-result", f"@{owner_result_path}",
                *preflight,
                "--json",
            ))
            before = repository_bytes(repo)
            spoofed_receipt = copy.deepcopy(receipt)
            spoofed_receipt["descriptor_digest"] = "sha256:" + "f" * 64
            spoofed_receipt_path = write_json(root / "term-spoofed-receipt.json", spoofed_receipt)
            rejected_receipt = run_cli(
                repo,
                CORE_CLI,
                "transaction",
                "preview",
                "--owner-result", f"@{owner_result_path}",
                "--owner-validation", f"@{spoofed_receipt_path}",
                "--json",
            )
            self.assertEqual(5, rejected_receipt.returncode, rejected_receipt.stdout + rejected_receipt.stderr)
            self.assertEqual("owner_validation_invalid", json.loads(rejected_receipt.stdout)["error"]["code"])
            self.assertEqual(before, repository_bytes(repo))

            spoofed_result = copy.deepcopy(owner_result)
            spoofed_result["artifact_drafts"][0]["content"] += "\n"
            spoofed_result_path = write_json(root / "term-spoofed-result.json", spoofed_result)
            receipt_path = write_json(root / "term-valid-receipt.json", receipt)
            rejected_result = run_cli(
                repo,
                CORE_CLI,
                "transaction",
                "preview",
                "--owner-result", f"@{spoofed_result_path}",
                "--owner-validation", f"@{receipt_path}",
                "--json",
            )
            self.assertEqual(5, rejected_result.returncode, rejected_result.stdout + rejected_result.stderr)
            self.assertEqual("owner_validation_invalid", json.loads(rejected_result.stdout)["error"]["code"])
            self.assertEqual(before, repository_bytes(repo))

    def test_acceptance_66_term_slot_and_lifecycle(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            repo = root / "repository"
            repo.mkdir()
            state = bootstrap_term_owners(root, repo)
            preflight = preflight_arguments(state["inventory"], state["doctor"])
            candidate = term_candidate("cand_123e4567e89b42d3a456426614174640")
            candidate_path = write_json(root / "term-slot-base.json", candidate)
            proof_path = write_json(root / "term-slot-base-proof.json", term_attestation(candidate))
            owner_result = public_result(run_cli(
                repo,
                TERM_CLI,
                "claim",
                "--candidate", f"@{candidate_path}",
                "--attestation", f"@{proof_path}",
                "--identifier", "ctx_550e8400e29b41d4a716446655440062",
                "--created-at", "2026-08-22T09:00:00+09:00",
                *preflight,
                "--json",
            ))
            owner_result_path = write_json(root / "term-slot-base-result.json", owner_result)
            receipt = public_result(run_cli(
                repo,
                TERM_CLI,
                "batch",
                "validate",
                "--owner-result", f"@{owner_result_path}",
                *preflight,
                "--json",
            ))
            receipt_path = write_json(root / "term-slot-base-receipt.json", receipt)
            preview = public_result(run_cli(
                repo,
                CORE_CLI,
                "transaction",
                "preview",
                "--owner-result", f"@{owner_result_path}",
                "--owner-validation", f"@{receipt_path}",
                "--json",
            ))
            bundle_path = write_json(root / "term-slot-base-bundle.json", preview["bundle"])
            public_result(run_cli(
                repo,
                CORE_CLI,
                "transaction",
                "apply",
                "--plan-bundle", f"@{bundle_path}",
                "--approved-digest", preview["approval_digest"],
                "--json",
            ))

            collision_cases = (
                ("primary-primary", "  bff!!  ", ["BFF Gateway"], ["Old BFF Gateway"]),
                ("primary-alias", "Backend for Frontend", ["Backend Adapter"], ["Old Backend Adapter"]),
                ("primary-deprecated", "API Facade", ["Facade Gateway"], ["Old Facade Gateway"]),
                ("alias-alias", "UI Gateway", ["Backend for Frontend"], ["Old UI Gateway"]),
                ("alias-deprecated", "Auth Gateway", ["API Facade"], ["Old Auth Gateway"]),
                ("deprecated-deprecated", "Term Gateway", ["Term Adapter"], ["API Facade"]),
            )
            for index, (name, term, aliases, deprecated_terms) in enumerate(collision_cases):
                with self.subTest(collision=name):
                    collision = term_candidate(f"cand_123e4567e89b42d3a4564266141746{41 + index:02d}")
                    collision.update({"title": f"{name} descendant collision", "scope_hint": "project/auth/api"})
                    collision["owner_inputs"]["term"].update({
                        "term": term,
                        "aliases": aliases,
                        "deprecated_terms": deprecated_terms,
                    })
                    collision_path = write_json(root / f"term-slot-{name}.json", collision)
                    collision_proof = write_json(root / f"term-slot-{name}-proof.json", term_attestation(collision))
                    collision_result = public_result(run_cli(
                        repo,
                        TERM_CLI,
                        "claim",
                        "--candidate", f"@{collision_path}",
                        "--attestation", f"@{collision_proof}",
                        "--identifier", f"ctx_550e8400e29b41d4a7164466554400{0x63 + index:02x}",
                        "--created-at", "2026-08-22T09:10:00+09:00",
                        *preflight,
                        "--json",
                    ))
                    collision_result_path = write_json(root / f"term-slot-{name}-result.json", collision_result)
                    before_collision = repository_bytes(repo)
                    rejected = run_cli(
                        repo,
                        TERM_CLI,
                        "batch",
                        "validate",
                        "--owner-result", f"@{collision_result_path}",
                        *preflight,
                        "--json",
                    )
                    self.assertEqual(5, rejected.returncode, rejected.stdout + rejected.stderr)
                    self.assertEqual("term_slot_conflict", json.loads(rejected.stdout)["error"]["code"])
                    self.assertEqual(before_collision, repository_bytes(repo))

            deprecated = public_result(run_cli(
                repo,
                TERM_CLI,
                "deprecate",
                "--id", "ctx_550e8400e29b41d4a716446655440062",
                "--reason", "새 gateway 구조에서는 이 명칭을 더 이상 쓰지 않는다.",
                "--replacement-term", "Session Gateway",
                "--retired-at", "2026-08-22T10:00:00+09:00",
                *preflight,
                "--json",
            ))
            deprecated_path = write_json(root / "term-deprecate-result.json", deprecated)
            deprecate_receipt = public_result(run_cli(
                repo,
                TERM_CLI,
                "batch",
                "validate",
                "--owner-result", f"@{deprecated_path}",
                *preflight,
                "--json",
            ))
            deprecate_receipt_path = write_json(root / "term-deprecate-receipt.json", deprecate_receipt)
            lifecycle_preview = public_result(run_cli(
                repo,
                CORE_CLI,
                "transaction",
                "preview",
                "--owner-result", f"@{deprecated_path}",
                "--owner-validation", f"@{deprecate_receipt_path}",
                "--json",
            ))
            lifecycle_bundle = write_json(root / "term-deprecate-bundle.json", lifecycle_preview["bundle"])
            public_result(run_cli(
                repo,
                CORE_CLI,
                "transaction",
                "apply",
                "--plan-bundle", f"@{lifecycle_bundle}",
                "--approved-digest", lifecycle_preview["approval_digest"],
                "--json",
            ))
            read = public_result(run_cli(
                repo,
                TERM_CLI,
                "read",
                "--signal", term_cli.SIGNAL,
                "--id", "ctx_550e8400e29b41d4a716446655440062",
                *preflight,
                "--json",
            ))
            self.assertEqual(("history", True), (read["state"], read["do_not_follow"]))
            self.assertEqual("deprecated", read["frontmatter"]["retired_reason"])
            self.assertEqual("Session Gateway", read["frontmatter"]["replacement_term"])
            self.assertEqual("ready", public_result(run_cli(repo, CORE_CLI, "doctor", "--json"))["repository_state"])

    def test_acceptance_67_decision_semantic_transport_binding(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            repo = root / "repository"
            repo.mkdir()
            public_result(run_cli(repo, CORE_CLI, "init", "--host", "codex", "--json"))
            public_result(run_cli(
                repo,
                DECISION_INIT,
                "--host", "codex",
                "--core-cli", str(CORE_CLI),
                "--json",
            ))
            inventory, doctor = public_preflight(root, repo, prefix="decision-transport")
            preflight = preflight_arguments(inventory, doctor)
            candidate = choice("cand_123e4567e89b42d3a456426614174670")
            owner_result = decision_cli.build_claim_result(
                candidate,
                decision_attestation(candidate),
                identifier="ctx_550e8400e29b41d4a716446655440067",
                created_at="2026-08-23T02:30:00+09:00",
                repo=repo,
            )
            honest_receipt = decision_cli.validate_batch(repo, owner_result)
            altered = copy.deepcopy(owner_result)
            draft = altered["artifact_drafts"][0]
            draft["content"] = draft["content"].replace(
                "인증 세션은 BFF가 소유한다.",
                "인증 세션은 SPA가 소유한다.",
            )
            draft["semantic_projection"]["primary_claim"] = "인증 세션은 SPA가 소유한다."
            altered_path = write_json(root / "decision-transport-altered-result.json", altered)
            before = repository_bytes(repo)

            rejected_validation = run_cli(
                repo,
                DECISION_CLI,
                "batch",
                "validate",
                "--owner-result", f"@{altered_path}",
                *preflight,
                "--json",
            )
            self.assertEqual(5, rejected_validation.returncode, rejected_validation.stdout + rejected_validation.stderr)
            self.assertEqual("claim_result_mismatch", json.loads(rejected_validation.stdout)["error"]["code"])
            self.assertEqual(before, repository_bytes(repo))

            receipt_path = write_json(root / "decision-transport-honest-receipt.json", honest_receipt)
            rejected_preview = run_cli(
                repo,
                CORE_CLI,
                "transaction",
                "preview",
                "--owner-result", f"@{altered_path}",
                "--owner-validation", f"@{receipt_path}",
                "--json",
            )
            self.assertEqual(5, rejected_preview.returncode, rejected_preview.stdout + rejected_preview.stderr)
            self.assertEqual("owner_validation_invalid", json.loads(rejected_preview.stdout)["error"]["code"])
            self.assertEqual(before, repository_bytes(repo))


if __name__ == "__main__":
    unittest.main()
