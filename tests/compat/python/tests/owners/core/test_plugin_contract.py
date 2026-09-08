#!/usr/bin/env python3
from __future__ import annotations

import copy
import hashlib
import importlib.util
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock


PLUGIN = next(p for p in Path(__file__).resolve().parents if (p / "pytest.ini").is_file()) / "plugins/bobbin"
CLI_PATH = PLUGIN / "skills/context/scripts/context_cli.py"
SPEC = importlib.util.spec_from_file_location("context_cli_policy", CLI_PATH)
assert SPEC and SPEC.loader
context_cli = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = context_cli
SPEC.loader.exec_module(context_cli)


def tree_digest(root: Path) -> str:
    digest = hashlib.sha256()
    for path in sorted(p for p in root.rglob("*") if p.is_file()):
        digest.update(path.relative_to(root).as_posix().encode())
        digest.update(path.read_bytes())
    return digest.hexdigest()


class PluginContractTests(unittest.TestCase):
    def test_doctor_self_report_is_additive_across_all_repository_states(self) -> None:
        expected_fields = {
            "schema", "owner", "supported_protocols", "repository_state", "root", "issues", "warnings",
            "plugin_version", "entrypoint", "protocol",
        }
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            repositories: dict[str, Path] = {}
            for state in ("absent", "partial", "ready", "invalid"):
                repo = root / state
                repo.mkdir()
                repositories[state] = repo
            (repositories["partial"] / "context").mkdir()
            for state in ("ready", "invalid"):
                preview = context_cli.build_init_bundle(repositories[state])
                context_cli.apply_bundle(repositories[state], preview["bundle"], preview["approval_digest"])
            (repositories["invalid"] / "context/context.index.md").write_text("not a context index\n", encoding="utf-8")

            for expected_state, repo in repositories.items():
                with self.subTest(state=expected_state):
                    doctor = context_cli.doctor_repository(repo)
                    self.assertEqual(expected_fields, set(doctor))
                    self.assertEqual(expected_state, doctor["repository_state"])
                    self.assertEqual("1.0.0", doctor["plugin_version"])
                    self.assertEqual(str(CLI_PATH.resolve()), doctor["entrypoint"])
                    self.assertEqual(context_cli.PROTOCOL, doctor["protocol"])
                    self.assertEqual([context_cli.PROTOCOL], doctor["supported_protocols"])

    def test_schema_reports_the_exact_core_receipt_contract(self) -> None:
        receipt = context_cli.schema_result()["receipt"]
        self.assertEqual("context-core-workflow-receipt/v1", receipt["schema"])
        self.assertEqual(
            ["schema", "status", "created_at", "plan_id", "core", "plan_bundle", "receipt_digest"],
            receipt["fields"],
        )
        self.assertEqual(["core", "plan_bundle"], receipt["workflow_approval_material"])
        self.assertEqual("damage_detection_only", receipt["receipt_digest_role"])
        self.assertEqual("agent_retained_explicit_path", receipt["selection"])

    def test_managed_policy_keeps_the_incremental_loop_call_free_and_ephemeral(self) -> None:
        policy = context_cli.POLICY_BODY
        for contract in ("explicit user language choice", "host's preferred response language",
                         "established conversation language", "OS locale is not authoritative",
                         "machine-readable surfaces in canonical English", "session-only ledger",
                         "Audit each user turn's new meaning once", "zero context tool calls",
                         "actual bodies, scope, and rationale", "rendered storage body",
                         "direct, explicit, unconditional semantic approval", "generic acknowledgement",
                         "same response", "Never regenerate after approval",
                         "explicit", "auto", "adaptive"):
            self.assertIn(contract, policy)
        for forbidden in ("approval_digest", "sha256", "fingerprint"):
            self.assertNotIn(forbidden, policy.casefold())
        self.assertLessEqual(len(policy.encode("utf-8")), 3000)
        rule = (PLUGIN / "rules/context-policy.md").read_text(encoding="utf-8")
        self.assertEqual(policy, rule.strip())
        agents = (PLUGIN.parents[1] / "AGENTS.md").read_text(encoding="utf-8")
        self.assertIn(policy, agents)

    def test_public_protocol_states_non_proportional_cost_invariant_and_limit(self) -> None:
        protocol = (PLUGIN / "skills/context/references/context-protocol.md").read_text(encoding="utf-8")
        for contract in (
            "Bounded-cost scope",
            "Hard bounds apply to artifact body materialization/open",
            "does not guarantee O(1) end-to-end recall computation or model tokens",
            "tests/context-v1/test_token_io_evidence.py",
            "not a hard runtime guarantee",
        ):
            self.assertIn(contract, protocol)

    def test_runtime_skills_keep_the_zero_path_and_bounded_prompt_surface(self) -> None:
        skill = (PLUGIN / "skills/context/SKILL.md").read_text(encoding="utf-8")
        skill_ko = (PLUGIN / "skills/context/SKILL.ko.md").read_text(encoding="utf-8")
        for contract in (
            "No durable signal means zero context tool calls",
            "skips AGENTS/guidance discovery",
            "excludes `context/`",
            "Named path",
            "infer one task subtree",
            "search once, then use the exact file",
            "Never use `.`, `--hidden`, repo-wide globs or root",
            "ask for the path instead of widening",
            "`context/` reads",
            "Session-only ledger",
            "actual bodies remain in context",
            "silent index check",
            "inspect implementation source only after an unexplained interface failure",
            "routing, claiming, drafting, validation",
        ):
            self.assertIn(contract, skill)
        for contract in (
            "context tool call 0",
            "`context/` artifact read 0",
            "AGENTS/guidance 탐색을 생략",
            "요청에 path가 있으면 그 target만 확인한다",
            "요청의 task noun으로 subtree 하나",
            "정해 한 번만 탐색",
            "`--hidden`",
            "범위를 넓히지 말고 path를 묻는다",
            "exact file만 쓴다",
            "repository root는 쓰지 않는다",
            "조용한 index 확인",
            "설명되지 않는 interface failure 뒤에만 구현을 읽는다",
        ):
            self.assertIn(contract, skill_ko)
        self.assertLessEqual(len(skill.encode("utf-8")), 3000)
        self.assertLessEqual(len(skill_ko.encode("utf-8")), 3000)

    def test_explicit_init_installs_active_host_policy_and_preserves_external_bytes(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            repo = Path(temp)
            target = repo / "CLAUDE.md"
            outside = "# Existing policy\n\nKeep this text.\n"
            target.write_text(outside, encoding="utf-8")
            target.chmod(0o644)

            first = context_cli.bootstrap_repository(repo, host="claude-code")

            self.assertTrue(first["policy"]["applied"])
            self.assertEqual("CLAUDE.md", first["policy"]["target"])
            self.assertTrue(target.read_text(encoding="utf-8").startswith(outside))
            self.assertIn(context_cli.POLICY_BODY, target.read_text(encoding="utf-8"))
            self.assertEqual(0o644, target.stat().st_mode & 0o777)
            self.assertFalse((repo / "AGENTS.md").exists())

            second = context_cli.bootstrap_repository(repo, host="claude-code")
            self.assertTrue(second["noop"])
            self.assertTrue(second["policy"]["noop"])

            original_build_init = context_cli.build_init_bundle

            def mutate_policy_during_core_phase(current_repo):
                target.write_text("# Concurrent replacement\n", encoding="utf-8")
                return original_build_init(current_repo)

            with mock.patch.object(context_cli, "build_init_bundle", side_effect=mutate_policy_during_core_phase):
                repaired = context_cli.bootstrap_repository(repo, host="claude-code")
            self.assertTrue(repaired["policy"]["applied"])
            self.assertIn(context_cli.POLICY_BODY, target.read_text(encoding="utf-8"))
            self.assertTrue(target.read_text(encoding="utf-8").startswith("# Concurrent replacement\n"))
            self.assertEqual(0o644, target.stat().st_mode & 0o777)

    def test_policy_create_uses_readable_mode(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            repo = Path(temp)

            context_cli.bootstrap_repository(repo, host="codex")

            target = repo / "AGENTS.md"
            self.assertEqual(0o644, target.stat().st_mode & 0o777)

    def test_acceptance_34_policy_install(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            repo = Path(temp)
            target = repo / "AGENTS.md"
            outside = b"# Existing policy\n\nKeep these bytes.\n"
            target.write_bytes(outside)
            before = tree_digest(repo)
            preview = context_cli.build_policy_bundle(repo, "AGENTS.md")
            self.assertEqual(before, tree_digest(repo))
            artifact = preview["approval_preview"]["artifacts"][0]
            self.assertTrue(artifact["content"].startswith(outside.decode()))
            self.assertIn(context_cli.POLICY_BEGIN, artifact["content"])
            self.assertEqual("policy_install", preview["bundle"]["approval_material"]["plan"]["transition"])

            tampered = copy.deepcopy(preview["bundle"])
            tampered["approval_material"]["plan"]["operations"][0]["path"] = "README.md"
            tampered["approval_digest"] = context_cli.canonical_digest(tampered["approval_material"])
            with self.assertRaises(context_cli.ContextError):
                context_cli.apply_bundle(repo, tampered, tampered["approval_digest"])
            self.assertEqual(before, tree_digest(repo))

            context_cli.apply_bundle(repo, preview["bundle"], preview["approval_digest"])
            installed = target.read_bytes()
            self.assertTrue(installed.startswith(outside))
            second = context_cli.build_policy_bundle(repo, "AGENTS.md")
            self.assertTrue(second["noop"])

    def test_policy_rejects_non_root_target_and_broken_markers(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            repo = Path(temp)
            with self.assertRaises(context_cli.ContextError):
                context_cli.build_policy_bundle(repo, "docs/AGENTS.md")
            (repo / "CLAUDE.md").write_text(context_cli.POLICY_BEGIN + "\n", encoding="utf-8")
            before = tree_digest(repo)
            with self.assertRaises(context_cli.ContextError):
                context_cli.bootstrap_repository(repo, host="claude-code")
            self.assertEqual(before, tree_digest(repo))
            self.assertFalse((repo / "context").exists())

            reversed_markers = context_cli.POLICY_END + "\n" + context_cli.POLICY_BEGIN + "\n"
            (repo / "CLAUDE.md").write_text(reversed_markers, encoding="utf-8")
            with self.assertRaises(context_cli.ContextError) as reversed_error:
                context_cli.build_policy_bundle(repo, "CLAUDE.md")
            self.assertEqual("policy_marker_invalid", reversed_error.exception.code)

    def test_init_rejects_non_utf8_policy_before_storage_write(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            repo = Path(temp)
            target = repo / "AGENTS.md"
            original = b"\xff\xfe\x00existing"
            target.write_bytes(original)

            with self.assertRaises(context_cli.ContextError) as failure:
                context_cli.bootstrap_repository(repo, host="codex")

            self.assertEqual("policy_file_unsupported", failure.exception.code)
            self.assertEqual("policy_preflight", failure.exception.details["phases"][0]["phase"])
            self.assertEqual(original, target.read_bytes())
            self.assertFalse((repo / "context").exists())


if __name__ == "__main__":
    unittest.main()
