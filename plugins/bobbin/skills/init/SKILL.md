---
name: init
description: Set up or reconfigure the installed Bobbin plugin for a project, choosing semantic features and a recording approval mode.
---

Runtime: Node.js 20.20.0+. Use the `.mjs` entrypoint in this package; no Python or global plugin lookup.

# Bobbin init

Init configures already-installed code; it never installs or uninstalls plugins.
Select project features (`decision`, `assumption`, `term`, `intent`, `document`)
and `explicit|auto|adaptive` recording. Recommend adaptive for everyday use, but
do not opt a project into automatic recording without the user's choice. Omitted
options preserve settings. First init imports existing registered semantic areas;
a fresh vault defaults to decision and explicit. SNAP, OBS and ARCHIVE are built in.

Use the loaded skill's own path to resolve this command:

```bash
node /loaded/bobbin/skills/init/scripts/bobbin_init.mjs --host codex --json
node /loaded/bobbin/skills/init/scripts/bobbin_init.mjs --host claude-code --features decision,intent,document --approval-mode adaptive --json
```

Use `--project DIR` for a specific project and `--vault DIR` for an existing shared
vault. Store settings in the project's `.bobbin/config.json`, and generated guidance
in its `AGENTS.md` or `CLAUDE.md`. Keep settings separate even when projects share
one vault. An empty `--features ''` selects built-ins only. Reinit adds missing
areas without deleting data; disabling stops new writes/implicit participation,
not explicit historical reads. Existing artifact schemas, IDs and lifecycle stay.

Before migrating, have the user disable the old `context-*` providers to avoid
duplicate audits. Do not change host installations yourself. Init changes only
selected settings, fixed seeds and managed guidance; it is not a content migration.
On a failed phase, report the error and retry the same init after its cause is
resolved. Existing completed seed phases are idempotent and preserved.

Follow the active-language contract in `../context/references/active-language.md`. Use the active language for all user-facing setup guidance and explanatory errors; keep commands, options, schema IDs, error codes, filenames, and metadata in English.

See [recording policy](../context/references/recording-policy.md) for mode semantics.
