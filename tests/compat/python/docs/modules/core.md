# Bobbin — core

[한국어](./core.ko.md)

Storage, indexing, routing, lifecycle validation and the sole physical writer. Built-in SNAP resumes unfinished work, OBS preserves reusable evidence, and ARCHIVE preserves immutable source material. DEC authority is never inferred from an observation or snapshot.

This is an internal module of Bobbin 1.0.0, not a separately installed plugin. Use the [getting-started guide](../../README.md) and the single `$bobbin:init` entrypoint. SNAP, OBS and ARCHIVE are built in; other owners are selected per project.

All writes follow `explicit|auto|adaptive` in the [shared recording policy](../../plugins/bobbin/skills/context/references/recording-policy.md). Recording automation does not replace semantic validity or user commitment. Disabling an owner preserves data and explicit historical reads.

Artifact structure, schema IDs and `context-common/v2` remain compatible. Consult the [protocol](../../plugins/bobbin/skills/context/references/context-protocol.md) for fields, CLI and lifecycle details.
