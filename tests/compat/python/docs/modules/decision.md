# Bobbin — decision

[한국어](./decision.ko.md)

An authoritative user choice with scope and commitment evidence. Compare actual `Decision`, `Rationale`, `Rejected alternatives`, and non-empty `Revisit conditions`, not hashes. Supersede creates an immutable successor and marks history `do_not_follow`; a revisit condition permits reassessment, not implementation. Canonical English headings preserve legacy Korean `결정`, `취지`, `반려대안` aliases on read/round-trip.

This is an internal module of Bobbin 1.0.0, not a separately installed plugin. Use the [getting-started guide](../../README.md) and the single `$bobbin:init` entrypoint. SNAP, OBS and ARCHIVE are built in; other owners are selected per project.

All writes follow `explicit|auto|adaptive` in the [shared recording policy](../../plugins/bobbin/skills/context/references/recording-policy.md). Recording automation does not replace semantic validity or user commitment. Disabling an owner preserves data and explicit historical reads.

Artifact structure, schema IDs and `context-common/v2` remain compatible. Consult the [protocol](../../plugins/bobbin/skills/decision/references/decision-protocol.md) for fields, CLI and lifecycle details.
