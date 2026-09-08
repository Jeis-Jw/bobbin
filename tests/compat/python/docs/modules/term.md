# Bobbin — term

[한국어](./term.ko.md)

A project-specific vocabulary definition and its canonical scope/key. Canonical English section `Definition` preserves legacy Korean `정의` as a read/round-trip alias. Vocabulary changes use the owner's lifecycle rules; ordinary words need no record unless their project-specific meaning matters.

This is an internal module of Bobbin 1.0.0, not a separately installed plugin. Use the [getting-started guide](../../README.md) and the single `$bobbin:init` entrypoint. SNAP, OBS and ARCHIVE are built in; other owners are selected per project.

All writes follow `explicit|auto|adaptive` in the [shared recording policy](../../plugins/bobbin/skills/context/references/recording-policy.md). Recording automation does not replace semantic validity or user commitment. Disabling an owner preserves data and explicit historical reads.

Artifact structure, schema IDs and `context-common/v2` remain compatible. Consult the [protocol](../../plugins/bobbin/skills/term/references/term-protocol.md) for fields, CLI and lifecycle details.
