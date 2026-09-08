#!/usr/bin/env python3
from __future__ import annotations

import importlib.util
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock


ROOT = next(p for p in Path(__file__).resolve().parents if (p / "pytest.ini").is_file())
INSTALLER = ROOT / "scripts/install_profile.py"


def load_installer():
    spec = importlib.util.spec_from_file_location("context_plugins_profile_installer", INSTALLER)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


installer = load_installer()


class ProfileInstallerTests(unittest.TestCase):
    def test_acceptance_68_profile_installer_installs_one_bobbin_package(self) -> None:
        profile = installer.load_profile()
        self.assertEqual("context-plugin-profile/v3", profile["schema"])
        self.assertEqual("1.0.0", profile["version"])
        self.assertEqual("same-major", profile["compatibility"])
        self.assertEqual("bobbin/1.0.0", profile["release_set"])
        self.assertEqual(
            {"bobbin": "1.0.0"},
            profile["minimum_versions"],
        )
        self.assertEqual(
            ["bobbin@bobbin"],
            profile["plugins"],
        )
        installer.validate_release_surface(profile)
        self.assertTrue((ROOT / "plugins/bobbin/skills/context/scripts/context_cli.py").is_file())
        self.assertTrue((ROOT / "plugins/bobbin/skills/decision/scripts/decision_cli.py").is_file())
        self.assertTrue((ROOT / "plugins/bobbin/skills/decision").exists())
        with tempfile.TemporaryDirectory() as temp:
            malformed = Path(temp) / "profile.json"
            malformed.write_text(json.dumps({**profile, "plugins": None}), encoding="utf-8")
            with self.assertRaises(installer.InstallProfileError):
                installer.load_profile(malformed)

    def test_fresh_codex_plan_is_one_profile_action_with_ordered_separate_installs(self) -> None:
        profile = installer.load_profile()
        commands = installer.build_install_plan(profile, "codex", "user", [], [])
        self.assertEqual(
            [
                ["codex", "plugin", "marketplace", "add", str(ROOT.resolve()), "--json"],
                ["codex", "plugin", "add", "bobbin@bobbin", "--json"],
            ],
            commands,
        )

    def test_fresh_claude_plan_applies_one_explicit_scope_to_both_plugins(self) -> None:
        profile = installer.load_profile()
        commands = installer.build_install_plan(profile, "claude-code", "project", [], [])
        self.assertEqual(
            [
                ["claude", "plugin", "marketplace", "add", str(ROOT.resolve()), "--scope", "project"],
                ["claude", "plugin", "install", "bobbin@bobbin", "--scope", "project"],
            ],
            commands,
        )

    def test_matching_local_marketplace_and_plugins_are_idempotent(self) -> None:
        profile = installer.load_profile()
        marketplaces = [{"name": "bobbin", "root": str(ROOT.resolve())}]
        installed = [
            {"pluginId": "bobbin@bobbin", "version": "1.0.0", "enabled": True},
        ]
        self.assertEqual([], installer.build_install_plan(profile, "codex", "user", marketplaces, installed))

    def test_same_major_at_or_above_minimum_is_accepted_and_missing_plugin_is_installed(self) -> None:
        profile = installer.load_profile()
        marketplaces = [{"name": "bobbin", "root": str(ROOT.resolve())}]
        installed = [
            {"pluginId": "bobbin@bobbin", "version": "1.0.0", "enabled": True},
        ]
        self.assertEqual(
            [],
            installer.build_install_plan(profile, "codex", "user", marketplaces, installed),
        )

    def test_same_major_below_release_set_minimum_fails_with_candidate_path(self) -> None:
        profile = installer.load_profile()
        profile["minimum_versions"]["bobbin"] = "1.1.0"
        marketplaces = [{"name": "bobbin", "root": str(ROOT.resolve())}]
        installed = [
            {"pluginId": "bobbin@bobbin", "version": "1.0.0", "enabled": True},
        ]
        with self.assertRaisesRegex(
            installer.InstallProfileError,
            r"below compatible release-set minimum 1\.1\.0.*Compatible candidate path:.*no automatic update",
        ):
            installer.build_install_plan(profile, "codex", "user", marketplaces, installed)

    def test_different_major_or_disabled_plugin_is_rejected(self) -> None:
        profile = installer.load_profile()
        marketplaces = [{"name": "bobbin", "root": str(ROOT.resolve())}]
        for installed, message in (
            ([{"pluginId": "bobbin@bobbin", "version": "2.0.0", "enabled": True}], "incompatible major"),
            ([{"pluginId": "bobbin@bobbin", "version": "0.7.1", "enabled": False}], "disabled"),
        ):
            with self.subTest(message=message), self.assertRaisesRegex(installer.InstallProfileError, message):
                installer.build_install_plan(profile, "codex", "user", marketplaces, installed)

    def test_legacy_provider_or_other_directory_fails_before_mutation(self) -> None:
        profile = installer.load_profile()
        legacy = [{"pluginId": "context-core@jeis-ai-plugins", "version": "0.3.0", "enabled": True}]
        with self.assertRaisesRegex(installer.InstallProfileError, "legacy"):
            installer.build_install_plan(profile, "codex", "user", [], legacy)
        with tempfile.TemporaryDirectory() as temp:
            marketplaces = [{"name": "bobbin", "root": temp}]
            with self.assertRaisesRegex(installer.InstallProfileError, "another directory"):
                installer.build_install_plan(profile, "codex", "user", marketplaces, [])

    def test_dry_run_prints_commands_without_spawning_host_processes(self) -> None:
        commands = [["codex", "plugin", "add", "bobbin@bobbin", "--json"]]
        with mock.patch.object(installer.subprocess, "run") as run, mock.patch("builtins.print") as output:
            installer.run_plan(commands, dry_run=True)
        run.assert_not_called()
        self.assertEqual(commands[0], json.loads(output.call_args.args[0]))

    def test_host_failure_stops_without_automatic_rollback(self) -> None:
        commands = [
            ["codex", "plugin", "add", "bobbin@bobbin"],
            ["codex", "plugin", "add", "context-decision@context-plugins"],
        ]
        failed = mock.Mock(returncode=1)
        with mock.patch.object(installer.subprocess, "run", return_value=failed) as run:
            with self.assertRaisesRegex(installer.InstallProfileError, "No automatic rollback"):
                installer.run_plan(commands)
        self.assertEqual(1, run.call_count)

    def test_cli_installs_from_downloaded_files_without_git_in_order(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            temp_root = Path(temp)
            distribution = temp_root / "downloaded"
            for directory in ("scripts", "profiles", "plugins", ".claude-plugin", ".agents/plugins"):
                shutil.copytree(ROOT / directory, distribution / directory, ignore=shutil.ignore_patterns("tests", "__pycache__"))
            self.assertFalse((distribution / ".git").exists())
            fake_bin = temp_root / "bin"
            fake_bin.mkdir()
            log = temp_root / "commands.log"
            fake_codex = fake_bin / "codex"
            fake_codex.write_text(
                "#!/bin/sh\n"
                "if [ \"$*\" = \"plugin marketplace list --json\" ]; then\n"
                "  printf '%s\\n' '{\"marketplaces\":[]}'\n"
                "elif [ \"$*\" = \"plugin list --json\" ]; then\n"
                "  printf '%s\\n' '{\"installed\":[]}'\n"
                "else\n"
                "  printf '%s\\n' \"$*\" >> \"$PROFILE_INSTALL_LOG\"\n"
                "  printf '%s\\n' '{}'\n"
                "fi\n",
                encoding="utf-8",
            )
            fake_codex.chmod(0o755)
            environment = {
                **os.environ,
                "PATH": str(fake_bin),
                "PROFILE_INSTALL_LOG": str(log),
            }
            completed = subprocess.run(
                [
                    sys.executable,
                    str(distribution / "scripts/install_profile.py"),
                    "--host",
                    "codex",
                ],
                cwd=temp_root,
                env=environment,
                text=True,
                capture_output=True,
            )
            self.assertEqual(0, completed.returncode, completed.stdout + completed.stderr)
            self.assertIn("Installed the bobbin profile", completed.stdout)
            self.assertEqual(
                [
                    f"plugin marketplace add {distribution.resolve()} --json",
                    "plugin add bobbin@bobbin --json",
                ],
                log.read_text(encoding="utf-8").splitlines(),
            )


if __name__ == "__main__":
    unittest.main()
