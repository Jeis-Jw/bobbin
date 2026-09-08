# Bobbin — intent

[한국어](./intent.ko.md)

A durable desired outcome and its success criteria, not a chosen implementation or inferred user commitment. Intent is independently meaningful; a decision can optionally serve it through `serves:intent` without requiring an inverse relationship.

This is an internal module of Bobbin 1.0.0, not a separately installed plugin. Use the [getting-started guide](../../README.md) and the single `$bobbin:init` entrypoint. SNAP, OBS and ARCHIVE are built in; other owners are selected per project.

All writes follow `explicit|auto|adaptive` in the [shared recording policy](../../plugins/bobbin/skills/context/references/recording-policy.md). Recording automation does not replace semantic validity or user commitment. Disabling an owner preserves data and explicit historical reads.

Artifact structure, schema IDs and `context-common/v2` remain compatible. Consult the [protocol](../../plugins/bobbin/skills/intent/references/intent-protocol.md) for fields, CLI and lifecycle details.
