#!/usr/bin/env python3
"""Install the single Bobbin package after checking for conflicting providers."""

from __future__ import annotations

import argparse
import json
import pathlib
import re
import shutil
import subprocess
import sys
from collections.abc import Sequence
from typing import Any


ROOT = pathlib.Path(__file__).resolve().parents[1]
PROFILE_PATH = ROOT / "profiles/bobbin.json"
PROFILE_SCHEMA = "context-plugin-profile/v3"
EXPECTED_PLUGINS = (
    "bobbin@bobbin",
)
HOSTS = ("codex", "claude-code")
CLAUDE_SCOPES = ("user", "project", "local")


class InstallProfileError(RuntimeError):
    pass


def _version_parts(value: Any) -> tuple[int, int, int]:
    if not isinstance(value, str):
        raise InstallProfileError("Plugin versions must use MAJOR.MINOR.PATCH.")
    match = re.fullmatch(r"(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)", value)
    if match is None:
        raise InstallProfileError(f"Invalid plugin version: {value!r}.")
    return tuple(int(part) for part in match.groups())


def _same_major(left: str, right: str) -> bool:
    return _version_parts(left)[0] == _version_parts(right)[0]


def _read_json(path: pathlib.Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise InstallProfileError(f"Could not read the profile: {path}") from error


def load_profile(path: pathlib.Path = PROFILE_PATH) -> dict[str, Any]:
    profile = _read_json(path)
    if not isinstance(profile, dict):
        raise InstallProfileError("The install profile must be a JSON object.")
    expected_keys = {
        "schema", "name", "version", "compatibility", "release_set", "minimum_versions",
        "marketplace", "plugins", "init",
    }
    if set(profile) != expected_keys:
        raise InstallProfileError("The install profile fields do not match context-plugin-profile/v3.")
    if profile["schema"] != PROFILE_SCHEMA or profile["name"] != "bobbin":
        raise InstallProfileError("The install profile identity is invalid.")
    if profile["marketplace"] != "bobbin":
        raise InstallProfileError("The install profile marketplace is invalid.")
    if profile["compatibility"] != "same-major":
        raise InstallProfileError("The install profile compatibility policy is invalid.")
    if profile["release_set"] != f"bobbin/{profile['version']}":
        raise InstallProfileError("The install profile release set is invalid.")
    plugins = profile["plugins"]
    if not isinstance(plugins, list) or not all(isinstance(selector, str) for selector in plugins):
        raise InstallProfileError("The install profile plugin list is invalid.")
    if tuple(plugins) != EXPECTED_PLUGINS:
        raise InstallProfileError("The supported profile must install Bobbin exactly once.")
    expected_names = {selector.split("@", 1)[0] for selector in plugins}
    minimum_versions = profile["minimum_versions"]
    if not isinstance(minimum_versions, dict) or set(minimum_versions) != expected_names:
        raise InstallProfileError("The install profile minimum-version set is invalid.")
    for name, minimum in minimum_versions.items():
        if _version_parts(minimum)[0] != _version_parts(profile["version"])[0]:
            raise InstallProfileError(f"{name} minimum version uses a different major.")
    if profile["init"] != "$bobbin:init":
        raise InstallProfileError("The install profile init selector is invalid.")
    _version_parts(profile["version"])
    return profile


def validate_release_surface(profile: dict[str, Any], root: pathlib.Path = ROOT) -> None:
    profile_version = profile["version"]
    marketplace = profile["marketplace"]
    plugin_versions: dict[str, str] = {}
    for selector in profile["plugins"]:
        name, selector_marketplace = selector.split("@", 1)
        if selector_marketplace != marketplace:
            raise InstallProfileError(f"{selector} uses a different marketplace.")
        versions: set[str] = set()
        for host_manifest in (".claude-plugin/plugin.json", ".codex-plugin/plugin.json"):
            manifest = _read_json(root / "plugins" / name / host_manifest)
            version = manifest.get("version") if isinstance(manifest, dict) else None
            if not isinstance(manifest, dict) or manifest.get("name") != name or not isinstance(version, str):
                raise InstallProfileError(f"{name} has an invalid host manifest.")
            _version_parts(version)
            versions.add(version)
        if len(versions) != 1:
            raise InstallProfileError(f"{name} host manifests use different versions.")
        version = versions.pop()
        if not _same_major(profile_version, version):
            raise InstallProfileError(
                f"{name} major version {version} is incompatible with profile {profile_version}."
            )
        if _version_parts(version) < _version_parts(profile["minimum_versions"][name]):
            raise InstallProfileError(
                f"{name} version {version} is below release-set minimum {profile['minimum_versions'][name]}."
            )
        plugin_versions[name] = version

    for catalog_path in (".claude-plugin/marketplace.json", ".agents/plugins/marketplace.json"):
        catalog = _read_json(root / catalog_path)
        entries = catalog.get("plugins") if isinstance(catalog, dict) else None
        if not isinstance(entries, list):
            raise InstallProfileError(f"{catalog_path} has no plugin catalog.")
        release_set = catalog.get("metadata", {}).get("release_set") if isinstance(catalog, dict) else None
        expected_release_set = {
            "schema": "context-plugin-release-set/v1",
            "version": profile_version,
            "runtime_compatibility": "single-package",
            "automatic_update": False,
        }
        if (
            not isinstance(release_set, dict)
            or any(release_set.get(key) != value for key, value in expected_release_set.items())
            or not isinstance(release_set.get("members"), dict)
        ):
            raise InstallProfileError(f"{catalog_path} has an invalid release-set declaration.")
        by_name = {entry.get("name"): entry for entry in entries if isinstance(entry, dict)}
        for selector in profile["plugins"]:
            name = selector.split("@", 1)[0]
            if by_name.get(name, {}).get("version") != plugin_versions[name]:
                raise InstallProfileError(f"{catalog_path} is not aligned to {name} version {plugin_versions[name]}.")
            if release_set["members"].get(name) != plugin_versions[name]:
                raise InstallProfileError(f"{catalog_path} release set is not aligned to {name} version {plugin_versions[name]}.")


def _decode_json_output(completed: subprocess.CompletedProcess[str], label: str) -> Any:
    if completed.returncode != 0:
        detail = completed.stderr.strip() or completed.stdout.strip() or "host command failed"
        raise InstallProfileError(f"Could not inspect {label}: {detail}")
    if not completed.stdout.strip():
        return []
    try:
        return json.loads(completed.stdout)
    except json.JSONDecodeError as error:
        raise InstallProfileError(f"{label} did not return JSON.") from error


def inspect_host(host: str) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    binary = "codex" if host == "codex" else "claude"
    if shutil.which(binary) is None:
        raise InstallProfileError(f"{binary} is not available on PATH.")
    marketplace_command = [binary, "plugin", "marketplace", "list", "--json"]
    plugin_command = [binary, "plugin", "list", "--json"]
    marketplace_result = subprocess.run(marketplace_command, text=True, capture_output=True, check=False)
    plugin_result = subprocess.run(plugin_command, text=True, capture_output=True, check=False)
    marketplace_data = _decode_json_output(marketplace_result, f"{host} marketplaces")
    plugin_data = _decode_json_output(plugin_result, f"{host} plugins")

    if isinstance(marketplace_data, dict):
        marketplace_data = marketplace_data.get("marketplaces", [])
    if isinstance(plugin_data, dict):
        plugin_data = plugin_data.get("installed", plugin_data.get("plugins", []))
    marketplaces = [item for item in marketplace_data if isinstance(item, dict)] if isinstance(marketplace_data, list) else []
    plugins = [item for item in plugin_data if isinstance(item, dict)] if isinstance(plugin_data, list) else []
    return marketplaces, plugins


def _entry_marketplace(entry: dict[str, Any]) -> str | None:
    value = entry.get("marketplaceName", entry.get("marketplace"))
    if isinstance(value, str):
        return value
    plugin_id = entry.get("pluginId", entry.get("id"))
    if isinstance(plugin_id, str) and "@" in plugin_id:
        return plugin_id.rsplit("@", 1)[1]
    return None


def _entry_name(entry: dict[str, Any]) -> str | None:
    value = entry.get("name")
    if isinstance(value, str):
        return value
    plugin_id = entry.get("pluginId", entry.get("id"))
    if isinstance(plugin_id, str):
        return plugin_id.split("@", 1)[0]
    return None


def _same_local_marketplace(entry: dict[str, Any], root: pathlib.Path) -> bool:
    candidates: list[str] = []
    for key in ("root", "path", "source"):
        value = entry.get(key)
        if isinstance(value, str):
            candidates.append(value)
        elif isinstance(value, dict):
            nested = value.get("source", value.get("path"))
            if isinstance(nested, str):
                candidates.append(nested)
    expected = root.resolve()
    for candidate in candidates:
        path = pathlib.Path(candidate).expanduser()
        if path.is_absolute() and path.resolve() == expected:
            return True
    return False


def build_install_plan(
    profile: dict[str, Any],
    host: str,
    scope: str,
    marketplaces: Sequence[dict[str, Any]],
    installed: Sequence[dict[str, Any]],
    root: pathlib.Path = ROOT,
) -> list[list[str]]:
    if host not in HOSTS:
        raise InstallProfileError(f"Unsupported host: {host}")
    if host == "codex" and scope != "user":
        raise InstallProfileError("Codex does not expose a plugin installation scope option; use user scope.")
    if host == "claude-code" and scope not in CLAUDE_SCOPES:
        raise InstallProfileError(f"Unsupported Claude Code scope: {scope}")

    marketplace = profile["marketplace"]
    version = profile["version"]
    expected_names = {selector.split("@", 1)[0] for selector in profile["plugins"]}
    conflicting = sorted(
        {
            f"{_entry_name(entry)}@{_entry_marketplace(entry)}"
            for entry in installed
            if (_entry_name(entry) in {"context-core", "context-decision", "context-assumption", "context-term", "context-intent", "context-document"}
                or (_entry_name(entry) in expected_names and _entry_marketplace(entry) not in (None, marketplace)))
            and entry.get("enabled", True) is not False
        }
    )
    if conflicting:
        raise InstallProfileError(
            "A legacy core/decision provider is still enabled. Disable or uninstall it explicitly before installing this profile: "
            + ", ".join(conflicting)
        )

    matching_marketplaces = [entry for entry in marketplaces if entry.get("name") == marketplace]
    if len(matching_marketplaces) > 1:
        raise InstallProfileError(f"Multiple {marketplace} marketplaces are configured.")
    if matching_marketplaces and not _same_local_marketplace(matching_marketplaces[0], root):
        raise InstallProfileError(
            f"{marketplace} already points to another directory. Remove or update it explicitly, then retry from the intended compatible distribution directory."
        )

    binary = "codex" if host == "codex" else "claude"
    commands: list[list[str]] = []
    if not matching_marketplaces:
        command = [binary, "plugin", "marketplace", "add", str(root.resolve())]
        if host == "codex":
            command.append("--json")
        else:
            command.extend(["--scope", scope])
        commands.append(command)

    by_selector: dict[str, dict[str, Any]] = {}
    for entry in installed:
        name = _entry_name(entry)
        entry_marketplace = _entry_marketplace(entry)
        if name and entry_marketplace:
            by_selector[f"{name}@{entry_marketplace}"] = entry

    for selector in profile["plugins"]:
        current = by_selector.get(selector)
        if current is not None:
            current_version = current.get("version")
            if current.get("enabled", True) is False:
                raise InstallProfileError(
                    f"{selector} is disabled. Enable it explicitly, then retry."
                )
            try:
                compatible = _same_major(version, current_version)
            except InstallProfileError as error:
                raise InstallProfileError(f"{selector} reports an invalid installed version.") from error
            if not compatible:
                raise InstallProfileError(
                    f"{selector} version {current_version} has an incompatible major version; this profile requires major {_version_parts(version)[0]}."
                )
            name = selector.split("@", 1)[0]
            minimum = profile["minimum_versions"][name]
            if _version_parts(current_version) < _version_parts(minimum):
                candidate = root / "plugins" / name
                raise InstallProfileError(
                    f"{selector} version {current_version} is below compatible release-set minimum {minimum}. "
                    f"Compatible candidate path: {candidate.resolve()}. Update it explicitly, reload the host, and retry; "
                    "no automatic update was attempted."
                )
            continue
        command = [binary, "plugin", "add" if host == "codex" else "install", selector]
        if host == "codex":
            command.append("--json")
        else:
            command.extend(["--scope", scope])
        commands.append(command)
    return commands


def run_plan(commands: Sequence[Sequence[str]], *, dry_run: bool = False) -> None:
    if dry_run:
        for command in commands:
            print(json.dumps(list(command), ensure_ascii=False))
        return
    for command in commands:
        completed = subprocess.run(list(command), check=False)
        if completed.returncode != 0:
            raise InstallProfileError(
                "Profile installation stopped after a host command failed. No automatic rollback was attempted."
            )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Install Bobbin once; refuse conflicting legacy providers or an incompatible version."
    )
    parser.add_argument("--host", choices=HOSTS, required=True)
    parser.add_argument("--scope", default="user", help="Claude Code scope: user, project, or local. Codex uses user.")
    parser.add_argument("--dry-run", action="store_true", help="Inspect the host and print the commands without changing it.")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        profile = load_profile()
        validate_release_surface(profile)
        marketplaces, installed = inspect_host(args.host)
        commands = build_install_plan(profile, args.host, args.scope, marketplaces, installed)
        run_plan(commands, dry_run=args.dry_run)
    except InstallProfileError as error:
        print(f"Install failed: {error}", file=sys.stderr)
        return 2
    if commands:
        if args.dry_run:
            print("Dry run complete; no host configuration was changed.")
        else:
            print(f"Installed the {profile['name']} profile. Reload the host, then run {profile['init']} once.")
    else:
        print(
            f"The {profile['name']} profile is already installed with compatible major "
            f"{_version_parts(profile['version'])[0]}."
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
