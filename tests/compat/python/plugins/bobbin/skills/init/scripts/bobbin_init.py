#!/usr/bin/env python3
"""Configure the already-installed Bobbin package for one project."""
from __future__ import annotations

import argparse
import json
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2] / "context/scripts"))
import bobbin_config
import context_cli


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project", help="Project configuration root; defaults to the current project.")
    parser.add_argument("--vault", help="Optional existing shared vault directory.")
    parser.add_argument("--host", choices=("codex", "claude-code"), required=True)
    parser.add_argument("--features", help="Comma-separated features; empty selects built-ins only. Omit to preserve/import selection.")
    parser.add_argument("--approval-mode", choices=bobbin_config.MODES, help="Omit to preserve current mode (explicit for existing/unconfigured projects).")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    try:
        result = bobbin_config.initialize(context_cli, project=args.project, vault=args.vault,
            features=None if args.features is None else ([x.strip() for x in args.features.split(",")] if args.features else []),
            approval_mode=args.approval_mode, host=args.host)
        print(json.dumps({"ok": True, "result": result}, ensure_ascii=False, indent=None if args.json else 2))
        return 0
    except (bobbin_config.ConfigError, context_cli.ContextError) as error:
        print(json.dumps({"ok": False, "error": {"code": error.code, "message": str(error)}}, ensure_ascii=False))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
