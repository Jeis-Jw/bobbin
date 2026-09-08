# Changelog

Bobbin has one public package version. Historical 0.x entries describe Context Plugins release sets, whose component versions could differ. Source preparation, tags and publication remain separate states.


## 1.0.0 — Bobbin — 2026-09-05

- One Bobbin package and version for Codex and Claude Code; semantic owners remain
  internal modules around the same sole-writer core.
- Project-local feature selection and idempotent `$bobbin:init` with legacy-area
  import, shared-vault isolation and non-destructive feature toggles.
- `explicit`, `auto` and LLM-assessed `adaptive` recording policies, enforced at
  the common apply boundary with frozen project-policy bindings.
- Existing Markdown artifact schemas and lifecycle are preserved. The source
  repository is now `Jeis-Jw/bobbin` (formerly `Jeis-Jw/context-plugins`), with the
  same repository identity and retained history. The primary checkout, remote,
  source coordinates and current documentation use Bobbin. Host installation,
  tags and marketplace publication remain separate actions.
- Added reproducible before/after runtime measurements; see [BENCHMARKS.md](./BENCHMARKS.md).

## 0.15.0 - 2026-09-04

Release set `0.15.0` contains `context-core` 0.14.0 (unchanged), `context-decision` 0.14.0, `context-assumption` 0.12.0, `context-term` 0.12.0, `context-intent` 0.12.0, and `context-document` 0.13.0. Only the decision component changes; same-major compatibility is unchanged.

### Added

- W1: add deterministic Korean lexical discovery for short Hangul terms and common particles. English stems, query terms, and retrieval bounds are byte-identical to 0.14.0.
- W2: separate user guides, contributor guidance, release history, and reproducible evidence.
- W3: add issue and pull-request templates, security reporting guidance, and a community code of conduct.

### Fixed

- Discover record-created decisions for requests such as `로그인 붙이자`, without adding a tokenizer dependency or changing stored artifact bytes.
- Point the public-trust test at the maintained changelog and benchmark evidence after the old combined release-notes file was removed.

### Compatibility

- Existing `context-common/v2` artifacts are unchanged. Previously stored `search_terms` are not rewritten automatically; see [MIGRATION.md](./MIGRATION.md).
- Korean particle stripping is intentionally conservative. Some nouns ending in a particle-like syllable, such as `어린이`, can still normalize too aggressively. Actual-body comparison remains authoritative after lexical discovery.

## 0.14.0 - 2026-09-03

Release set `0.14.0` contains `context-core` 0.14.0, `context-decision` 0.13.0, `context-assumption` 0.12.0, `context-term` 0.12.0, `context-intent` 0.12.0, and `context-document` 0.13.0.

- Added one-call approved DEC recording and body-derived search terms with bounded inverse-frequency ranking.
- Treated generated indexes as write-time projections, added Git union-merge attributes and `refresh --check`, and kept artifact and slot conflicts fail-closed.
- Added one-call approved SNAP save/update and an explicit resume path.
- Preserved `context-common/v2`, stored artifact bytes, core-only physical writes, and separate semantic-owner packages.
- Published tag `v0.14.0`; central marketplace publication remained a separate owner gate.
- Moved measured evidence and its limitations to [BENCHMARKS.md](./BENCHMARKS.md).

## 0.13.0 - Developer preview

- Replaced the rendered-file approval screen with approval of settled semantic payload, canonical scope, and lifecycle effect in normal conversation.
- Kept frozen receipts, runtime and vault binding, CAS, locking, atomic writes, and unchanged apply as internal integrity controls.
- Preserved stored artifacts and `context-common/v2`; stale pending receipts require regeneration.

## 0.12.0 - Developer preview

- Added immutable ARCHIVE capture/read/search/discard, bounded OBS evidence references, and DOCUMENT freshness diagnostics.
- Kept ARCHIVE out of default recall unless explicitly requested and preserved existing artifact bytes.

## 0.11.0 - Developer preview

- Made OBS preview state explicit and added single-command inline preview wrappers for the optional semantic owners.
- Added a six-plugin release-set map and diagnostic same-major core candidates without automatic selection or installation.

## 0.10.0 - Developer preview

- Added typed relation validation and the optional INTENT and DOCUMENT owners.
- Kept decision, intent, and document independently usable and left `core-decision` as exactly two packages.

## 0.9.0 - Developer preview

- Made a regular filesystem directory the vault boundary and removed Git as a runtime requirement.
- Bound pending approvals to vault identity and kept copied saved context portable while preventing pending-approval replay.

## 0.8.0 - Developer preview

- Adopted same-major package compatibility, including the pre-1.0 line, while retaining runtime protocol and capability handshakes.
- Reduced static prompt material; this observation was not a token-savings measurement.

## 0.7.1 - Developer preview

- Made English canonical for runtime instructions while keeping Korean user documentation and legacy Korean artifact headings readable.
- Added natural-language approval, deterministic receipt lifecycle, discovery-only decision lookup, and the explicit root profile installer.

## 0.6.0 - Historical unreleased candidate

- Prepared a one-question natural-language approval contract over a complete rendered preview.
- Kept core and semantic-owner packages separate while using root distribution tooling to coordinate installation.
- This approval surface was later superseded by semantic approval in 0.13.0.

## 0.5.1 - Historical prepared patch

- Reduced static prompt material and bounded healthy misses, recovery reads, owner inputs, candidates, and approval previews.
- Bound approval material to vault/runtime identity and hardened exact semantic-input validation.

## 0.5.0 - Developer preview

- Added bounded DEC `spec-view`, generic `context-owner-descriptor/v2`, and optional ASM and TERM owners.
- Preserved the storage protocol and kept addon installation and artifact migration explicit.

## 0.4.1 - Developer preview

- Documented durable-context value and bounded recall, and synchronized the managed policy with its runtime-installed copy.

## 0.4.0 - Developer preview

- Moved distribution coordinates to marketplace `context-plugins` and source `Jeis-Jw/context-plugins` without changing `context-common/v2` artifacts.

## 0.3.0 - Developer preview

- Added same-pass incremental auditing, signal-gated metadata-first recall, selected actual-body reads, and a session-local read ledger.

## 0.2.1 - Developer preview

- Narrowed fail-closed checks to the actual write target and added bounded index-first recovery.

## 0.2.0 - Breaking developer preview

- Removed legacy semantic fingerprint fields and batch-local claim keys.
- Introduced the `context-common/v2` wire/storage handshake and lazy cleanup of removed fields on later approved rewrites.

## License

The repository is licensed under the [Apache License 2.0](./LICENSE). A source merge, tag, host installation, central marketplace publication, and community announcement are separate release states.
