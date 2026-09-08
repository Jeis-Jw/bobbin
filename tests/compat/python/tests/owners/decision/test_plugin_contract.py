#!/usr/bin/env python3
from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = next(p for p in Path(__file__).resolve().parents if (p / "pytest.ini").is_file())
PHASE0 = ROOT / "tests/context-v1/phase0/phase0_contract.py"


def load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


phase0 = load("phase0_distribution_contract", PHASE0)
CLI_PATH = ROOT / "plugins/bobbin/skills/decision/scripts/decision_cli.py"
WORKFLOW_PATH = ROOT / "plugins/bobbin/skills/decision/scripts/decision_workflow.py"
INIT_PATH = ROOT / "plugins/bobbin/skills/init/scripts/decision_init.py"
CORE_CLI_PATH = ROOT / "plugins/bobbin/skills/context/scripts/context_cli.py"
decision_cli = load("context_decision_plugin_contract", CLI_PATH)


def digest_tree(root: Path) -> str:
    digest = hashlib.sha256()
    for path in sorted(item for item in root.rglob("*") if item.is_file()):
        digest.update(path.relative_to(root).as_posix().encode("utf-8"))
        digest.update(path.read_bytes())
    return digest.hexdigest()


def canonical_digest(value: object) -> str:
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return "sha256:" + hashlib.sha256(encoded).hexdigest()


def write_preflight_inputs(root: Path, case: dict) -> tuple[Path, Path]:
    inventory = root / f"{case['id']}-inventory.json"
    doctor = root / f"{case['id']}-doctor.json"
    inventory.write_text(json.dumps(case["inventory"], ensure_ascii=False), encoding="utf-8")
    doctor.write_text(json.dumps(case["doctor"], ensure_ascii=False), encoding="utf-8")
    return inventory, doctor


def preflight_args(case: dict, inventory: Path, doctor: Path) -> list[str]:
    return [
        "--host",
        case["host"],
        "--core-inventory",
        f"@{inventory}",
        "--core-doctor",
        f"@{doctor}",
    ]


def run_cli(repo: Path, *arguments: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(CLI_PATH), *arguments],
        cwd=repo,
        env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
        text=True,
        capture_output=True,
    )


class PluginContractTests(unittest.TestCase):
    def test_core_doctor_handshake_requires_the_exact_ten_field_self_report(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            repo = Path(temp)
            repo.mkdir(parents=True, exist_ok=True)
            completed = subprocess.run(
                [sys.executable, str(CORE_CLI_PATH), "doctor", "--json"],
                cwd=repo,
                env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
                text=True,
                capture_output=True,
                check=True,
            )
            doctor = json.loads(completed.stdout)["result"]
            self.assertEqual(
                {
                    "schema", "owner", "supported_protocols", "repository_state", "root", "issues", "warnings",
                    "plugin_version", "entrypoint", "protocol",
                },
                set(doctor),
            )
            self.assertEqual(
                doctor,
                decision_cli.validate_core_doctor_handshake(doctor, allowed_states={"absent"}),
            )
            for field, value in (("protocol", "context-common/v1"), ("plugin_version", "0.5.0"), ("entrypoint", "/tmp/context_cli.py")):
                with self.subTest(field=field):
                    forged = dict(doctor, **{field: value})
                    with self.assertRaises(decision_cli.DecisionError):
                        decision_cli.validate_core_doctor_handshake(forged, allowed_states={"absent"})

    def test_acceptance_02_core_missing(self) -> None:
        fixtures = ROOT / "tests/context-v1/fixtures/host-inventory"
        required = json.loads((fixtures / "required-plugin.json").read_text(encoding="utf-8"))
        cases = json.loads((fixtures / "preflight-cases.json").read_text(encoding="utf-8"))["cases"]
        missing = next(case for case in cases if case["expected_code"] == "core_missing")

        with tempfile.TemporaryDirectory() as temp:
            repo = Path(temp) / "repository"
            host = Path(temp) / "host-config"
            repo.mkdir()
            host.mkdir()
            repo.mkdir(parents=True, exist_ok=True)
            (repo / "keep.txt").write_text("repository bytes\n", encoding="utf-8")
            (host / "settings.json").write_text('{"keep":true}\n', encoding="utf-8")
            before = (digest_tree(repo), digest_tree(host))

            result = phase0.classify_preflight(missing["inventory"], missing["doctor"], required)
            rendered = phase0.render_preflight(result, missing["host"], required)

            self.assertEqual("core_missing", rendered["code"])
            self.assertEqual("bobbin@bobbin", rendered["required_plugin"]["selector"])
            self.assertEqual("Jeis-Jw/bobbin", rendered["required_plugin"]["source"])
            self.assertIn("Install", " ".join(rendered["manual_actions"]))
            self.assertEqual({"repository": "none", "host_configuration": "none"}, rendered["write_policy"])
            self.assertEqual(before, (digest_tree(repo), digest_tree(host)))

    def test_read_only_surfaces_are_core_free_while_write_preflight_remains(self) -> None:
        protocol = (ROOT / "plugins/bobbin/skills/decision/references/decision-protocol.md").read_text(encoding="utf-8")
        init = (ROOT / "plugins/bobbin/skills/init/SKILL.md").read_text(encoding="utf-8")
        self.assertIn(
            "`schema`, `capabilities`, `check`, `search`, `read`, `brief`, `spec-view`, `conflicts`, and `revisit` are core-free",
            protocol,
        )
        for token in ("entrypoint path", "SHA-256", "protocol", "repository_state=absent"):
            self.assertIn(token, protocol)
        self.assertIn("`context-owner-descriptor/v1`", protocol)
        self.assertIn("uses the core embedded in the same Bobbin package", protocol)
        for forbidden in ("install", "enable", "update", "marketplace", "plugin caches", "copy a separate runtime"):
            self.assertIn(forbidden, protocol)
        self.assertIn("bobbin_init.py", init)
        self.assertIn("--features", init)
        self.assertIn("--approval-mode", init)
        self.assertIn("never installs or uninstalls plugins", init)
        self.assertIn("Omitted", init)

    def test_loaded_init_skill_resolves_sibling_entrypoint_without_claude_root(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            installed = root / "installed-context-decision"
            unrelated_cwd = root / "repository"
            shutil.copytree(ROOT / "plugins/bobbin", installed)
            unrelated_cwd.mkdir()
            loaded_skill = installed / "skills/init/SKILL.md"
            entrypoint = loaded_skill.parent / "scripts/bobbin_init.py"
            environment = {**os.environ, "PYTHONDONTWRITEBYTECODE": "1"}
            environment.pop("CLAUDE_PLUGIN_ROOT", None)
            completed = subprocess.run(
                [sys.executable, str(entrypoint), "--help"],
                cwd=unrelated_cwd,
                env=environment,
                text=True,
                capture_output=True,
            )
            self.assertEqual(0, completed.returncode, completed.stdout + completed.stderr)
            self.assertIn("bobbin_init.py", completed.stdout)
            init = loaded_skill.read_text(encoding="utf-8")
            self.assertIn('/loaded/bobbin/skills/init/scripts/bobbin_init.py', init)
            self.assertIn("loaded skill's own path", init)

    def test_public_cli_runs_exact_six_state_preflight_before_init(self) -> None:
        fixtures = ROOT / "tests/context-v1/fixtures/host-inventory"
        required = json.loads((fixtures / "required-plugin.json").read_text(encoding="utf-8"))
        cases = json.loads((fixtures / "preflight-cases.json").read_text(encoding="utf-8"))["cases"]
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            repo = root / "repository"
            host = root / "host-config"
            repo.mkdir()
            host.mkdir()
            repo.mkdir(parents=True, exist_ok=True)
            (repo / "keep.txt").write_text("repository bytes\n", encoding="utf-8")
            (host / "settings.json").write_text('{"keep":true}\n', encoding="utf-8")
            for case in cases:
                with self.subTest(case=case["id"]):
                    inventory, doctor = write_preflight_inputs(root, case)
                    before = (digest_tree(repo), digest_tree(host))
                    completed = run_cli(
                        repo,
                        "init",
                        *preflight_args(case, inventory, doctor),
                        "--json",
                    )
                    payload = json.loads(completed.stdout)
                    self.assertEqual(before, (digest_tree(repo), digest_tree(host)))
                    if case["expected_code"] in {"ready", "core_uninitialized"}:
                        self.assertEqual(0, completed.returncode, completed.stdout + completed.stderr)
                        self.assertTrue(payload["ok"])
                        self.assertEqual("context-decision-init-plan/v1", payload["result"]["schema"])
                        self.assertEqual(case["doctor"]["repository_state"], payload["result"]["core_repository_state"])
                        self.assertEqual("bootstrap", payload["result"]["bootstrap"]["operation"])
                        self.assertEqual(case["host"], payload["result"]["bootstrap"]["host"])
                        self.assertEqual(
                            "AGENTS.md" if case["host"] == "codex" else "CLAUDE.md",
                            payload["result"]["bootstrap"]["policy_install"],
                        )
                        self.assertEqual(
                            ["core_init", "area_register", "policy_install"],
                            [phase["phase"] for phase in payload["result"]["phases"]],
                        )
                    else:
                        self.assertEqual(5, completed.returncode, completed.stdout + completed.stderr)
                        self.assertFalse(payload["ok"])
                        self.assertEqual(case["expected_code"], payload["error"]["code"])
                        self.assertEqual(case["expected_rendered"]["message"], payload["error"]["message"])
                        details = payload["error"]["details"]
                        self.assertEqual(case["host"], details["host"])
                        self.assertEqual(required, details["required_plugin"])
                        self.assertEqual(case["expected_observed"], details["observed"])
                        self.assertEqual(case["expected_rendered"]["manual_actions"], details["manual_actions"])
                        self.assertEqual({"repository": "none", "host_configuration": "none"}, details["write_policy"])
                        self.assertEqual(before, (digest_tree(repo), digest_tree(host)))

    def test_write_pipeline_cli_fails_closed_without_host_preflight(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            repo = Path(temp)
            (repo / "keep.txt").write_text("repository bytes\n", encoding="utf-8")
            before = digest_tree(repo)
            write_commands = (
                ("init",),
                (
                    "candidate", "prepare",
                    "--candidate-id", "cand_550e8400e29b41d4a716446655440000",
                    "--title", "title", "--summary", "summary", "--scope", "project/auth",
                    "--decision-key", "owner", "--captured-from", "conversation",
                    "--commitment-evidence", "evidence", "--sec-decision", "decision",
                    "--sec-rationale", "rationale", "--sec-alternatives", "alternative",
                ),
                ("draft", "--candidate", "@/missing.json", "--attestation", "@/missing.json"),
            )
            for command in write_commands:
                with self.subTest(command=command[:2]):
                    completed = run_cli(repo, *command, "--json")
                    self.assertEqual(5, completed.returncode, completed.stdout + completed.stderr)
                    self.assertEqual("core_preflight_required", json.loads(completed.stdout)["error"]["code"])
                    self.assertEqual(before, digest_tree(repo))
            for command in (("schema", "--json"), ("capabilities", "--json")):
                static = run_cli(repo, *command)
                self.assertEqual(0, static.returncode, static.stdout + static.stderr)

    def test_public_direct_capture_is_two_phase_and_semantic_owner_controlled(self) -> None:
        fixtures = ROOT / "tests/context-v1/fixtures/host-inventory"
        cases = json.loads((fixtures / "preflight-cases.json").read_text(encoding="utf-8"))["cases"]
        ready = next(case for case in cases if case["expected_code"] == "ready")
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            repo = root / "repository"
            repo.mkdir()
            (repo / "keep.txt").write_text("repository bytes\n", encoding="utf-8")
            inventory, doctor = write_preflight_inputs(root, ready)
            preflight = preflight_args(ready, inventory, doctor)
            common = [
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
            ]
            before = digest_tree(repo)
            prepared = run_cli(repo, "candidate", "prepare", *common, *preflight, "--json")
            self.assertEqual(0, prepared.returncode, prepared.stdout + prepared.stderr)
            candidate = json.loads(prepared.stdout)["result"]
            self.assertEqual(["결정 권한자가 현재 따를 선택으로 확정했다."], candidate["evidence"])
            self.assertNotIn("explicit direct decision request", json.dumps(candidate, ensure_ascii=False))
            candidate_path = root / "candidate.json"
            candidate_path.write_text(json.dumps(candidate, ensure_ascii=False), encoding="utf-8")
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
            attestation_path = root / "attestation.json"
            attestation_path.write_text(json.dumps(attestation, ensure_ascii=False), encoding="utf-8")
            accepted = run_cli(
                repo,
                "capture",
                "--candidate",
                f"@{candidate_path}",
                "--attestation",
                f"@{attestation_path}",
                *preflight,
                "--json",
            )
            self.assertEqual(0, accepted.returncode, accepted.stdout + accepted.stderr)
            accepted_result = json.loads(accepted.stdout)["result"]
            self.assertEqual("claim", accepted_result["decision"])
            self.assertEqual(candidate, accepted_result["semantic_inputs"][0]["value"])
            self.assertEqual(before, digest_tree(repo))

            decline_cases = (
                (
                    "fact",
                    "cand_123e4567e89b42d3a456426614174000",
                    "Safari에서 third-party cookie가 차단된다.",
                    "재현 로그에서 확인한 사실이다.",
                    "사실 발견은 현재 따를 선택이 아니다.",
                ),
                (
                    "idea",
                    "cand_987e6543e21b42d3a456426614174002",
                    "인증 세션을 edge에 두면 좋을 것 같다.",
                    "브레인스토밍에서 나온 아이디어다.",
                    "아이디어는 현재 따르기로 확정된 선택이 아니다.",
                ),
            )
            for label, candidate_id, claim, evidence, reason in decline_cases:
                with self.subTest(decision=label):
                    declined_candidate = dict(candidate)
                    declined_candidate["candidate_id"] = candidate_id
                    declined_candidate["claim"] = claim
                    declined_candidate["evidence"] = [evidence]
                    declined_candidate["owner_inputs"] = {
                        "decision": {
                            **candidate["owner_inputs"]["decision"],
                            "decision": claim,
                        }
                    }
                    declined_path = root / f"{label}.json"
                    declined_path.write_text(json.dumps(declined_candidate, ensure_ascii=False), encoding="utf-8")
                    declined = run_cli(
                        repo,
                        "capture",
                        "--candidate",
                        f"@{declined_path}",
                        "--decline-reason",
                        reason,
                        *preflight,
                        "--json",
                    )
                    self.assertEqual(0, declined.returncode, declined.stdout + declined.stderr)
                    declined_result = json.loads(declined.stdout)["result"]
                    self.assertEqual("decline", declined_result["decision"])
                    self.assertEqual([], declined_result["artifact_drafts"])
                    self.assertEqual([], declined_result["semantic_attestations"])
                    self.assertEqual(before, digest_tree(repo))

    def test_public_skill_documents_preflight_and_two_phase_capture(self) -> None:
        decision = (ROOT / "plugins/bobbin/skills/decision/SKILL.md").read_text(encoding="utf-8")
        decision_ko = (ROOT / "plugins/bobbin/skills/decision/SKILL.ko.md").read_text(encoding="utf-8")
        decision_policy = (ROOT / "plugins/bobbin/rules/decision-policy.md").read_text(encoding="utf-8")
        init = (ROOT / "plugins/bobbin/skills/init/SKILL.md").read_text(encoding="utf-8")
        protocol = (ROOT / "plugins/bobbin/skills/decision/references/decision-protocol.md").read_text(encoding="utf-8")
        combined = "\n".join((decision, init, protocol))
        for token in (
            "--host", "--core-inventory", "--core-doctor", "candidate prepare", "capture --candidate", "decline",
            "decision_workflow.py preview", "--inline", "--attest-explicit-choice", "--receipt-file",
            "--approved-digest", "frozen receipt",
        ):
            self.assertIn(token, combined)
        schema = json.loads(run_cli(ROOT, "schema", "--json").stdout)["result"]
        self.assertEqual("decision_workflow.py", schema["workflow_surface"]["entrypoint"])
        self.assertEqual(["inline", "files"], schema["workflow_surface"]["preview_input_modes"])
        self.assertEqual(
            ["explicit_choice", "scope_identified", "commitment_present"],
            schema["workflow_surface"]["inline_assertions"],
        )
        self.assertEqual(["preview", "apply", "reject", "record"], schema["workflow_surface"]["commands"])
        self.assertEqual(["capture", "supersede", "withdraw"], schema["workflow_surface"]["operations"])
        self.assertEqual(
            {
                "top_level_fields": [
                    "schema", "status", "created_at", "candidate_id", "operation",
                    "approval_material", "approval_digest", "receipt_digest",
                ],
                "approval_material_fields": [
                    "schema", "vault_identity", "core", "operation", "workflow_input_digest",
                    "owner_result_digest", "core_approval_digest", "core_bundle",
                ],
                "status": "pending",
                "default_directory": "tempdir/context-decision",
                "directory_mode": "0700",
                "file_mode": "0600",
                "ttl_seconds": 86400,
                "automatic_selection": "exactly_one_fresh_pending_repository_and_core_bound_receipt",
                "approval_transport": "preview_stdout_digest_to_required_apply_argument",
                "success_cleanup": "remove_unless_keep_receipt",
            },
            schema["workflow_surface"]["receipt_contract"],
        )
        for skill in (decision, decision_ko):
            canonical_commands = skill.split("```bash", 1)[1].split("```", 1)[0]
            for low_level_locator in ("--candidate-id", "--captured-from", "--receipt-file", "--approved-digest", "--core-cli"):
                self.assertNotIn(low_level_locator, canonical_commands)
            self.assertIn("decision_workflow.py record", canonical_commands)
            self.assertIn("--approved", canonical_commands)
            self.assertIn("preview stdout", skill)
            self.assertIn("--supersede", skill)
            self.assertIn("--withdraw", skill)
            self.assertIn("reject --core-cli", skill)
        for public_contract in (decision_policy, decision, decision_ko):
            for marker in ("Revisit conditions", "satisfied", "no evidence", "ambiguous", "conflict"):
                self.assertIn(marker, public_contract)
        for public_contract in (decision_policy, decision):
            self.assertIn("verbatim", public_contract)
        self.assertIn("user response에 그대로", decision_ko)
        for contract in (
            "When exact `--scope` and `--decision-key` are known, run one exact-slot `decision_cli.py check`",
            "Reuse sections returned by `check` in the same turn",
            "do not call `read`, `spec-view`, or another context read",
            "Do not pre-run host inventory or core doctor",
            "inspect script source only after an unexplained interface failure",
            "never expose or request",
            "Keep means the action is not performed",
        ):
            self.assertIn(contract, decision)
        for contract in (
            "정확한 `--scope`와 `--decision-key`를 알면 exact-slot `decision_cli.py check`를 한 번만 실행한다",
            "같은 턴에서는 `check`가 반환한 section을 재사용한다",
            "다른 context read를 다시 호출하지 않는다",
            "Host inventory나 core doctor를 미리 실행하지 않는다",
            "설명되지 않는 interface failure 뒤에만 script source를 읽는다",
            "transport detail은 노출하거나 요구하지 않는다",
            "keep이면 수행하지 않고 supersede면 그 명시적 선택 뒤에만 진행한다",
        ):
            self.assertIn(contract, decision_ko)
        self.assertLessEqual(len(decision.encode("utf-8")), 5200)
        self.assertLessEqual(len(decision_ko.encode("utf-8")), 6200)
        self.assertLessEqual(len(decision_policy.encode("utf-8")), 2050)
        self.assertTrue(WORKFLOW_PATH.is_file())


if __name__ == "__main__":
    unittest.main()
