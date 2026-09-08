#!/usr/bin/env python3
from __future__ import annotations

import ast
import copy
import hashlib
import importlib.util
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = next(p for p in Path(__file__).resolve().parents if (p / "pytest.ini").is_file())
PHASE0 = ROOT / "tests/context-v1/phase0/phase0_contract.py"
PLUGIN_NAMES = (
    "context-core",
    "context-decision",
    "context-assumption",
    "context-term",
    "context-intent",
    "context-document",
)
RELEASE_SET_VERSION = "0.15.0"
PLUGIN_VERSIONS = {
    "context-core": "0.14.0",
    "context-decision": "0.14.0",
    "context-assumption": "0.12.0",
    "context-term": "0.12.0",
    "context-intent": "0.12.0",
    "context-document": "0.13.0",
}
OWNER_SKILLS = {
    "context-core": "context",
    "context-decision": "decision",
    "context-assumption": "assumption",
    "context-term": "term",
    "context-intent": "intent",
    "context-document": "document",
}
OWNER_CLIS = {
    name: f"skills/{skill}/scripts/{skill}_cli.py"
    for name, skill in OWNER_SKILLS.items()
}
INIT_ENTRYPOINTS = {
    "context-core": "skills/context/scripts/context_cli.py",
    "context-decision": "skills/init/scripts/decision_init.py",
    "context-assumption": "skills/init/scripts/assumption_init.py",
    "context-term": "skills/init/scripts/term_init.py",
    "context-intent": "skills/init/scripts/intent_init.py",
    "context-document": "skills/init/scripts/document_init.py",
}
SCHEMA_NAMES = {
    "context-core": "context-core-schema/v1",
    "context-decision": "context-decision-schema/v1",
    "context-assumption": "context-assumption-schema/v1",
    "context-term": "context-term-schema/v1",
    "context-intent": "context-intent-schema/v1",
    "context-document": "context-document-schema/v1",
}
FORBIDDEN_KEYS = {
    "dependencies",
    "dependency",
    "requires",
    "implicit_install",
    "implicitInstall",
    "default_install",
    "defaultInstall",
    "installed_by_default",
    "installedByDefault",
}
def load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


phase0 = load("phase0_distribution_proof", PHASE0)


def read_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def recursive_keys(value) -> set[str]:
    if isinstance(value, dict):
        return set(value) | set().union(*(recursive_keys(item) for item in value.values()), set())
    if isinstance(value, list):
        return set().union(*(recursive_keys(item) for item in value), set())
    return set()


def digest_tree(root: Path) -> str:
    digest = hashlib.sha256()
    for path in sorted(item for item in root.rglob("*") if item.is_file()):
        digest.update(path.relative_to(root).as_posix().encode("utf-8"))
        digest.update(path.read_bytes())
    return digest.hexdigest()


class DistributionProofTests(unittest.TestCase):
    def test_acceptance_41_dependency_matrix(self) -> None:
        fixtures = ROOT / "tests/context-v1/fixtures/host-inventory"
        required = read_json(fixtures / "required-plugin.json")
        corpus = read_json(fixtures / "preflight-cases.json")
        expected = {
            "core_missing",
            "core_source_mismatch",
            "core_disabled",
            "core_incompatible",
            "core_uninitialized",
            "ready",
        }
        observed: set[str] = set()
        for case in corpus["cases"]:
            inventory = copy.deepcopy(case["inventory"])
            doctor = copy.deepcopy(case["doctor"])
            before = json.dumps({"inventory": inventory, "doctor": doctor}, sort_keys=True)
            with tempfile.TemporaryDirectory() as temp:
                repo = Path(temp) / "repository"
                host = Path(temp) / "host-config"
                repo.mkdir()
                host.mkdir()
                repo.mkdir(parents=True, exist_ok=True)
                (repo / "keep.txt").write_text("repository bytes\n", encoding="utf-8")
                (host / "config.json").write_text('{"keep":true}\n', encoding="utf-8")
                byte_before = (digest_tree(repo), digest_tree(host))
                result = phase0.classify_preflight(inventory, doctor, required)
                rendered = phase0.render_preflight(result, case["host"], required)
                self.assertEqual(byte_before, (digest_tree(repo), digest_tree(host)))
            observed.add(rendered["code"])
            self.assertEqual(required, rendered["required_plugin"])
            self.assertEqual({"repository": "none", "host_configuration": "none"}, rendered["write_policy"])
            self.assertEqual(before, json.dumps({"inventory": inventory, "doctor": doctor}, sort_keys=True))
        self.assertEqual(expected, observed)

        self.assertEqual("bobbin@bobbin", required["selector"])
        self.assertEqual("bobbin", required["plugin"])

    def test_acceptance_42_repository_absent(self) -> None:
        fixtures = ROOT / "tests/context-v1/fixtures/host-inventory"
        required = read_json(fixtures / "required-plugin.json")
        corpus = read_json(fixtures / "preflight-cases.json")["cases"]
        case = next(item for item in corpus if item["expected_code"] == "core_uninitialized")
        with tempfile.TemporaryDirectory() as temp:
            repo = Path(temp) / "repository"
            host = Path(temp) / "host"
            repo.mkdir()
            host.mkdir()
            repo.mkdir(parents=True, exist_ok=True)
            (repo / "keep.txt").write_text("keep\n", encoding="utf-8")
            (host / "config.json").write_text("{}\n", encoding="utf-8")
            before = (digest_tree(repo), digest_tree(host))
            rendered = phase0.render_preflight(
                phase0.classify_preflight(case["inventory"], case["doctor"], required),
                case["host"],
                required,
            )
            self.assertEqual("core_uninitialized", rendered["code"])
            self.assertNotEqual("core_missing", rendered["code"])
            self.assertIn("public bootstrap surface", " ".join(rendered["manual_actions"]))
            self.assertEqual(before, (digest_tree(repo), digest_tree(host)))

            context_cli = load(
                "context_cli_distribution_direct_init",
                ROOT / "plugins/bobbin/skills/context/scripts/context_cli.py",
            )
            initialized = context_cli.bootstrap_repository(repo, host=case["host"])
            self.assertEqual(
                [("core_init", "applied"), ("policy_install", "applied")],
                [(phase["phase"], phase["status"]) for phase in initialized["phases"]],
            )
            self.assertEqual("ready", initialized["doctor"]["repository_state"])
            self.assertEqual("AGENTS.md", initialized["policy"]["target"])
            self.assertTrue(initialized["policy"]["applied"])
            self.assertIn(context_cli.POLICY_BODY, (repo / "AGENTS.md").read_text(encoding="utf-8"))
            self.assertEqual("keep\n", (repo / "keep.txt").read_text(encoding="utf-8"))
            self.assertEqual(before[1], digest_tree(host))

        context_cli = load(
            "context_cli_distribution_storage_error",
            ROOT / "plugins/bobbin/skills/context/scripts/context_cli.py",
        )
        with tempfile.TemporaryDirectory() as temp:
            repo = Path(temp)
            with self.assertRaises(context_cli.ContextError) as caught:
                context_cli.recall_repository(repo)
            self.assertEqual("context_root_missing", caught.exception.code)

    def test_acceptance_43_manifests(self) -> None:
        codex = read_json(ROOT / ".agents/plugins/marketplace.json")
        claude = read_json(ROOT / ".claude-plugin/marketplace.json")
        for catalog in (codex, claude):
            self.assertEqual("bobbin", catalog["name"])
            self.assertEqual(["bobbin"], [entry["name"] for entry in catalog["plugins"]])
            self.assertEqual("1.0.0", catalog["plugins"][0]["version"])
            self.assertEqual({"bobbin": "1.0.0"}, catalog["metadata"]["release_set"]["members"])
        self.assertEqual("./plugins/bobbin", claude["plugins"][0]["source"])
        self.assertEqual({"source": "local", "path": "./plugins/bobbin"}, codex["plugins"][0]["source"])
        self.assertEqual({"installation": "AVAILABLE", "authentication": "ON_INSTALL"}, codex["plugins"][0]["policy"])
        package = ROOT / "plugins/bobbin"
        self.assertEqual(2, len(list(package.rglob("plugin.json"))))
        for host in (".codex-plugin", ".claude-plugin"):
            manifest = read_json(package / host / "plugin.json")
            self.assertEqual(("bobbin", "1.0.0"), (manifest["name"], manifest["version"]))
            self.assertFalse(FORBIDDEN_KEYS & recursive_keys(manifest))
        self.assertEqual({"init", "context", "decision", "assumption", "term", "intent", "document", "snapshot", "observation", "archive"},
                         {path.parent.name for path in (package / "skills").glob("*/SKILL.md")})
        for skill in OWNER_SKILLS.values():
            completed = subprocess.run([sys.executable, str(package / f"skills/{skill}/scripts/{skill}_cli.py"), "schema", "--json"],
                                       capture_output=True, text=True)
            self.assertEqual(0, completed.returncode, completed.stdout + completed.stderr)
        for prompt in read_json(package / ".codex-plugin/plugin.json")["interface"]["defaultPrompt"]:
            self.assertTrue(prompt.strip())
            self.assertLessEqual(len(prompt), 128)
        self.assertFalse((ROOT / "context").exists())
        self.assertFalse((ROOT / "wiki").exists())


    def test_forbidden_install_and_host_mutation_calls_are_absent(self) -> None:
        targets = [ROOT / ".claude-plugin/marketplace.json", ROOT / ".agents/plugins/marketplace.json"]
        targets.extend(
            ROOT / "plugins/bobbin" / host / "plugin.json"
            for name in PLUGIN_NAMES
            for host in (".claude-plugin", ".codex-plugin")
        )
        text = "\n".join(path.read_text(encoding="utf-8") for path in targets).casefold()
        for forbidden in (
            "installed_by_default",
            "installedbydefault",
            "implicit_install",
            "implicitinstall",
            "auto_install",
            "auto_enable",
            "auto_update",
        ):
            self.assertNotIn(forbidden, text)

        scripts = tuple(ROOT / "plugins/bobbin" / OWNER_CLIS[name] for name in PLUGIN_NAMES)
        for script in scripts:
            tree = ast.parse(script.read_text(encoding="utf-8"), filename=str(script))
            imported = {
                node.names[0].name.split(".")[0]
                for node in ast.walk(tree)
                if isinstance(node, ast.Import)
            } | {
                node.module.split(".")[0]
                for node in ast.walk(tree)
                if isinstance(node, ast.ImportFrom) and node.module
            }
            self.assertLessEqual(imported - {"__future__", "typing"}, sys.stdlib_module_names)
            for node in ast.walk(tree):
                if not isinstance(node, ast.Call):
                    continue
                call = ast.unparse(node.func)
                self.assertNotIn(call, {"os.system", "subprocess.Popen", "subprocess.run"})

        for script in scripts[1:]:
            semantic_source = script.read_text(encoding="utf-8")
            for physical_write in (".write_text(", ".write_bytes(", ".mkdir(", ".unlink(", "os.replace("):
                self.assertNotIn(physical_write, semantic_source)

        all_python = "\n".join(
            path.read_text(encoding="utf-8").casefold()
            for name in PLUGIN_NAMES
            for path in (ROOT / "plugins/bobbin").rglob("*.py")
        )
        for forbidden_command in (
            "codex plugin add",
            "claude plugin install",
            "plugin marketplace add",
            "auto_install(",
            "auto_enable(",
            "auto_update(",
        ):
            self.assertNotIn(forbidden_command, all_python)

    def test_semantic_approval_and_active_language_contract_are_consistent(self) -> None:
        package = ROOT / "plugins/bobbin"
        skills = list((package / "skills").glob("*/SKILL.md"))
        self.assertEqual(10, len(skills))
        for path in skills:
            source = path.read_text()
            self.assertIn("recording-policy.md", source, path)
            self.assertIsNone(re.search(r"[가-힣]", source), path)
        policy = (package / "skills/context/references/recording-policy.md").read_text()
        for mode in ("explicit", "auto", "adaptive"):
            self.assertIn(mode, policy)
        self.assertIn("semantic attestation", policy.lower())
        self.assertIn("project settings", policy)
        self.assertIn("Do not call a", policy)
        core = load("bobbin_policy_projection", package / "skills/context/scripts/context_cli.py")
        self.assertEqual(core.POLICY_BODY, (package / "rules/context-policy.md").read_text().strip())
        self.assertIn(core.POLICY_BODY, (ROOT / "AGENTS.md").read_text())


    def test_agents_policy_tracks_current_owners_and_filesystem_vault_contract(self) -> None:
        policy = (ROOT / "AGENTS.md").read_text(encoding="utf-8")
        for owner in PLUGIN_NAMES:
            self.assertIn(f"`{owner}`", policy)
        self.assertIn("선택된 filesystem vault root의 `context/`", policy)
        self.assertIn("Git은 공유와 버전 관리를 위한 선택 사항", policy)
        self.assertIn("context runtime의 전제가 아니다", policy)
        self.assertIn("모든 semantic owner", policy)
        self.assertIn("`plugins/bobbin`", policy)
        self.assertIn("별도 plugin dependency를 설치하지 않으며", policy)
        self.assertNotIn("repository root의 `context/`", policy)

    def test_public_component_keeps_internal_context_out_of_release_repository(self) -> None:
        policy = (ROOT / "AGENTS.md").read_text(encoding="utf-8")
        ignore_rules = (ROOT / ".gitignore").read_text(encoding="utf-8").splitlines()
        self.assertFalse((ROOT / "context").exists())
        self.assertIn("/context/", ignore_rules)
        self.assertIn("public component 예외", policy)
        self.assertIn("상위 `context-manager` vault", policy)
        self.assertIn("공개 repository에 `context/`를 만들거나 commit하지 않는다", policy)
        self.assertIn("consumer vault는 이 경계와 무관하다", policy)

    def test_semantic_plugins_accept_same_major_core_and_reject_other_major(self) -> None:
        expected = read_json(ROOT / "tests/context-v1/fixtures/host-inventory/required-plugin.json")
        self.assertEqual("skills/context/scripts/context_cli.py", expected["entrypoint"])
        self.assertEqual(1, expected["compatible_major"])
        self.assertNotIn("entrypoint_sha256", expected)

        modules = []
        for name in PLUGIN_NAMES[1:]:
            owner_cli = ROOT / "plugins/bobbin" / OWNER_CLIS[name]
            module = load(f"{name.replace('-', '_')}_distribution_compatibility", owner_cli)
            modules.append(module)
            self.assertEqual(expected, module.REQUIRED_PLUGIN)
            init_source = (ROOT / "plugins/bobbin" / INIT_ENTRYPOINTS[name]).read_text(encoding="utf-8")
            self.assertIn(".required_core_surface", init_source)
            self.assertIn("expected_sha256=core_cli_sha256", init_source)

        workflow_source = (
            ROOT / "plugins/bobbin/skills/decision/scripts/decision_workflow.py"
        ).read_text(encoding="utf-8")
        self.assertIn("decision_cli.required_core_surface", workflow_source)
        self.assertIn("expected_sha256=core_cli_sha256", workflow_source)

        with tempfile.TemporaryDirectory() as temp:
            copied_core = Path(temp) / "context-core"
            shutil.copytree(ROOT / "plugins/bobbin", copied_core)
            copied_cli = copied_core / "skills/context/scripts/context_cli.py"

            def set_core_version(version: str) -> None:
                for relative in (".claude-plugin/plugin.json", ".codex-plugin/plugin.json"):
                    manifest_path = copied_core / relative
                    manifest = read_json(manifest_path)
                    manifest["version"] = version
                    manifest_path.write_text(
                        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
                        encoding="utf-8",
                    )

            set_core_version("1.0.2")
            for module in modules:
                self.assertEqual(copied_cli.resolve(), module.required_core_surface(str(copied_cli.resolve())))

            set_core_version("2.0.0")
            for module in modules:
                with self.assertRaises(Exception) as caught:
                    module.required_core_surface(str(copied_cli.resolve()))
                self.assertEqual("core_surface_mismatch", caught.exception.code)

    def test_public_trust_contract_matches_the_release_surface(self) -> None:
        for path in (ROOT / "README.md", ROOT / "README.ko.md"):
            source = path.read_text()
            for token in ("Bobbin", "$bobbin:init", "bobbin@bobbin", "explicit", "auto", "adaptive", ".bobbin/config.json"):
                self.assertIn(token, source)
            self.assertNotIn("approval_digest", source)
            self.assertNotIn("context-decision@context-plugins", source)
        policy = (ROOT / "plugins/bobbin/skills/context/references/recording-policy.md").read_text()
        for token in ("explicit", "auto", "adaptive", "scope", "uncertainty", "policy-decision", "policy-reason", "Disabled"):
            self.assertIn(token, policy)
        self.assertIn("1.0.0", (ROOT / "MIGRATION.md").read_text())


    def test_community_files_and_links_have_the_expected_public_shape(self) -> None:
        expected = (
            ROOT / ".github/ISSUE_TEMPLATE/bug_report.yml",
            ROOT / ".github/ISSUE_TEMPLATE/feature_request.yml",
            ROOT / ".github/PULL_REQUEST_TEMPLATE.md",
            ROOT / "SECURITY.md",
            ROOT / "CODE_OF_CONDUCT.md",
            ROOT / "CONTRIBUTING.md",
            ROOT / "CHANGELOG.md",
            ROOT / "BENCHMARKS.md",
        )
        for path in expected:
            self.assertTrue(path.is_file(), path)
            self.assertTrue(path.read_text(encoding="utf-8").strip(), path)

        issue_forms = expected[:2]
        for path in issue_forms:
            text = path.read_text(encoding="utf-8")
            self.assertRegex(text, r"(?m)^name: .+$")
            self.assertRegex(text, r"(?m)^description: .+$")
            self.assertRegex(text, r"(?m)^body:$")
            self.assertNotIn("\t", text)
            item_types = re.findall(r"(?m)^  - type: ([a-z_]+)$", text)
            self.assertTrue(item_types, path)
            self.assertLessEqual(set(item_types), {"markdown", "dropdown", "input", "textarea", "checkboxes"})
            ids = re.findall(r"(?m)^    id: ([a-z_]+)$", text)
            self.assertEqual(len(ids), len(set(ids)), path)

        bug_template = issue_forms[0].read_text(encoding="utf-8")
        for token in ("Host", "Plugin versions", "Doctor output", "Remove secrets", "home-directory paths"):
            self.assertIn(token, bug_template)
        security = (ROOT / "SECURITY.md").read_text(encoding="utf-8")
        self.assertIn("https://github.com/Jeis-Jw/bobbin/security/advisories/new", security)
        self.assertIn("approval and lifecycle binding", security)
        conduct = (ROOT / "CODE_OF_CONDUCT.md").read_text(encoding="utf-8")
        self.assertIn("Contributor Covenant, version 2.1", conduct)

        markdown_paths = (
            ROOT / "README.md",
            ROOT / "README.ko.md",
            ROOT / "CONTRIBUTING.md",
            ROOT / "SECURITY.md",
            ROOT / "CODE_OF_CONDUCT.md",
            ROOT / "CHANGELOG.md",
            *tuple(ROOT.glob("plugins/*/README.md")),
            *tuple(ROOT.glob("plugins/*/README.ko.md")),
        )
        for path in markdown_paths:
            for target in re.findall(r"\[[^]]+\]\(([^)]+)\)", path.read_text(encoding="utf-8")):
                if target.startswith(("https://", "http://", "#")):
                    continue
                relative = target.split("#", 1)[0]
                self.assertTrue((path.parent / relative).resolve().exists(), f"{path}: {target}")

    def test_public_help_exposes_capture_limits_and_core_trust(self) -> None:
        environment = {**os.environ, "PYTHONDONTWRITEBYTECODE": "1"}
        workflow = ROOT / "plugins/bobbin/skills/decision/scripts/decision_workflow.py"
        for command in ("preview", "apply"):
            completed = subprocess.run(
                [sys.executable, str(workflow), command, "--help"],
                cwd=ROOT,
                env=environment,
                text=True,
                capture_output=True,
            )
            self.assertEqual(0, completed.returncode, completed.stdout + completed.stderr)
            for token in ("outside the vault", "0600", "approval_digest", "receipt_digest"):
                self.assertIn(token, completed.stdout)

        preview_help = subprocess.run(
            [sys.executable, str(workflow), "preview", "--help"],
            cwd=ROOT,
            env=environment,
            text=True,
            capture_output=True,
            check=True,
        ).stdout
        for token in ("@file", "@@literal", "1,200", "2,000", "8 KiB", "16 KiB"):
            self.assertIn(token, preview_help)
        for token in ("[--candidate-id CANDIDATE_ID]", "[--receipt-file RECEIPT_FILE]", "--supersede", "--withdraw"):
            self.assertIn(token, preview_help)

        apply_help = subprocess.run(
            [sys.executable, str(workflow), "apply", "--help"],
            cwd=ROOT,
            env=environment,
            text=True,
            capture_output=True,
            check=True,
        ).stdout
        for token in ("[--receipt-file RECEIPT_FILE]", "--approved-digest APPROVED_DIGEST", "--keep-receipt"):
            self.assertIn(token, apply_help)
        self.assertNotIn("[--approved-digest APPROVED_DIGEST]", apply_help)

        reject_help = subprocess.run(
            [sys.executable, str(workflow), "reject", "--help"],
            cwd=ROOT,
            env=environment,
            text=True,
            capture_output=True,
            check=True,
        ).stdout
        for token in ("--receipt-file", "--candidate-id", "--core-cli"):
            self.assertIn(token, reject_help)

        decision_init = subprocess.run(
            [sys.executable, str(ROOT / "plugins/bobbin" / INIT_ENTRYPOINTS["context-decision"]), "--help"],
            cwd=ROOT,
            env=environment,
            text=True,
            capture_output=True,
            check=True,
        ).stdout
        for token in (
            "entrypoint suffix", "same-major", "actual core digest", "context-common/v2", "required commands",
            "owner-descriptor feature", "doctor state", "@file", "@@literal", "1,200", "2,000", "8 KiB",
            "16 KiB", "0600", "outside the vault", "approval_digest",
        ):
            self.assertIn(token, decision_init)
        self.assertIn("successful apply or reject removes the default receipt", decision_init)
        self.assertIn("users are never asked to locate, enter, or delete", decision_init)
        self.assertNotIn("must be deleted manually", decision_init)

        for name, label in (("context-assumption", "ASM"), ("context-term", "TERM")):
            init_help = subprocess.run(
                [sys.executable, str(ROOT / "plugins/bobbin" / INIT_ENTRYPOINTS[name]), "--help"],
                cwd=ROOT,
                env=environment,
                text=True,
                capture_output=True,
                check=True,
            ).stdout
            for token in (
                "entrypoint suffix", "same-major", "actual core digest", "context-common/v2", "required commands",
                "owner-descriptor feature", "doctor state", f"{label} claim and decline", "--candidate @file",
                "2,000", "8 KiB", "16 KiB",
            ):
                self.assertIn(token, init_help)
            self.assertNotIn("--sec-", init_help)
            self.assertNotIn("@@literal", init_help)

            claim_help = subprocess.run(
                [sys.executable, str(ROOT / "plugins/bobbin" / OWNER_CLIS[name]), "claim", "--help"],
                cwd=ROOT,
                env=environment,
                text=True,
                capture_output=True,
                check=True,
            ).stdout
            self.assertIn("--candidate @FILE", claim_help)
            self.assertIn("structured candidate JSON input via @file", claim_help)
            self.assertNotIn("--sec-", claim_help)
            self.assertNotIn("@@literal", claim_help)

    def test_core_obs_snapshot_two_command_receipt_surface_is_distributed(self) -> None:
        core_cli = ROOT / "plugins/bobbin/skills/context/scripts/context_cli.py"
        environment = {**os.environ, "PYTHONDONTWRITEBYTECODE": "1"}
        commands = {
            "observation": ("observation", "capture", "--help"),
            "snapshot": ("snapshot", "save", "--help"),
            "apply": ("transaction", "apply", "--help"),
        }
        help_text = {
            name: subprocess.run(
                [sys.executable, str(core_cli), *arguments],
                cwd=ROOT,
                env=environment,
                text=True,
                capture_output=True,
                check=True,
            ).stdout
            for name, arguments in commands.items()
        }
        for token in ("--attest-reusable-observation", "--attest-evidence-present", "--receipt-file"):
            self.assertIn(token, help_text["observation"])
        for token in ("--attest-handoff-requested", "--attest-unfinished-context-present", "--receipt-file"):
            self.assertIn(token, help_text["snapshot"])
        for token in ("--plan-bundle", "--receipt-file", "--approved-digest"):
            self.assertIn(token, help_text["apply"])

        for kind in ("observation", "snapshot"):
            for language in ("", ".ko"):
                skill = ROOT / f"plugins/bobbin/skills/{kind}/SKILL{language}.md"
                text = skill.read_text(encoding="utf-8")
                self.assertIn("transaction apply --receipt-file", text, skill)
                self.assertIn("result.approval_digest", text, skill)
                self.assertTrue("no directory scan" in text or "directory scan도 하지 않는다" in text, skill)

        protocol = (ROOT / "plugins/bobbin/skills/context/references/context-protocol.md").read_text(encoding="utf-8")
        for token in (
            "exactly seven fields", "workflow digest over exactly `{core,plan_bundle}`",
            "damage only", "agent from preview", "no receipt locator, keep, reject, or TTL lifecycle",
        ):
            self.assertIn(token, protocol)
        source = core_cli.read_text(encoding="utf-8")
        for forbidden in ("--keep-receipt", "RECEIPT_TTL", "receipt locator"):
            self.assertNotIn(forbidden, source)

    def test_addon_test_helpers_use_unique_import_namespaces(self) -> None:
        contracts = {
            "context-assumption": "assumption_test_support",
            "context-term": "term_test_support",
            "context-intent": "intent_test_support",
            "context-document": "document_test_support",
        }
        for plugin, support in contracts.items():
            tests_root = ROOT / "tests/owners" / plugin.removeprefix("context-")
            self.assertTrue((tests_root / f"{support}.py").is_file())
            for test_path in tests_root.glob("test_*.py"):
                source = test_path.read_text(encoding="utf-8")
                self.assertNotIn("import helpers", source, test_path)
                self.assertIn(f"import {support} as helpers", source, test_path)


if __name__ == "__main__":
    unittest.main()
