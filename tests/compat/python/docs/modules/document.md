# Bobbin — document

[한국어](./document.ko.md)

A living project document representing current synthesized state. A DEC may link with `affects:document`; refresh warns `document-stale-vs-decision` when a newer affecting Current DEC outdates the document. Update the document without rewriting the original decision.

This is an internal module of Bobbin 1.0.0, not a separately installed plugin. Use the [getting-started guide](../../README.md) and the single `$bobbin:init` entrypoint. SNAP, OBS and ARCHIVE are built in; other owners are selected per project.

All writes follow `explicit|auto|adaptive` in the [shared recording policy](../../plugins/bobbin/skills/context/references/recording-policy.md). Recording automation does not replace semantic validity or user commitment. Disabling an owner preserves data and explicit historical reads.

Artifact structure, schema IDs and `context-common/v2` remain compatible. Consult the [protocol](../../plugins/bobbin/skills/document/references/document-protocol.md) for fields, CLI and lifecycle details.
