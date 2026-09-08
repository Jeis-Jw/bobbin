#!/usr/bin/env python3
"""Generate both Bobbin catalogs and the install profile from one version source."""
from pathlib import Path
import argparse
import json

ROOT = Path(__file__).resolve().parents[1]


def surfaces():
    manifest = json.loads((ROOT / "plugins/bobbin/.codex-plugin/plugin.json").read_text())
    version = manifest["version"]
    assert manifest["name"] == "bobbin"
    release = {"schema": "context-plugin-release-set/v1", "version": version,
               "runtime_compatibility": "single-package", "automatic_update": False, "members": {"bobbin": version}}
    entry = {"name": "bobbin", "version": version, "description": manifest["description"]}
    return {
        "plugins/bobbin/.claude-plugin/plugin.json": {key: manifest[key] for key in ("name", "version", "description", "author")},
        ".agents/plugins/marketplace.json": {"name": "bobbin", "interface": {"displayName": "Bobbin"},
            "metadata": {"release_set": release}, "plugins": [{**entry,
                "source": {"source": "local", "path": "./plugins/bobbin"},
                "policy": {"installation": "AVAILABLE", "authentication": "ON_INSTALL"}, "category": "Productivity"}]},
        ".claude-plugin/marketplace.json": {"name": "bobbin", "owner": manifest["author"],
            "metadata": {"description": manifest["description"], "release_set": release},
            "plugins": [{**entry, "source": "./plugins/bobbin"}]},
        "profiles/bobbin.json": {"schema": "context-plugin-profile/v3", "name": "bobbin", "version": version,
            "compatibility": "same-major", "release_set": "bobbin/" + version, "minimum_versions": {"bobbin": version},
            "marketplace": "bobbin", "plugins": ["bobbin@bobbin"], "init": "$bobbin:init"},
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    mismatches = []
    for relative, value in surfaces().items():
        path = ROOT / relative
        content = json.dumps(value, ensure_ascii=False, indent=2) + "\n"
        if args.check:
            if not path.is_file() or path.read_text() != content:
                mismatches.append(relative)
        else:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content)
    if mismatches:
        raise SystemExit("Distribution drift: " + ", ".join(mismatches))


if __name__ == "__main__":
    main()
