# context-common/v2 storage kernel

Bobbin 1.0.0 uses one package and one `$bobbin:init` entrypoint. User-approval instructions below describe explicit mode; auto/adaptive use the same validated path under the [shared recording policy](recording-policy.md). Semantic validity and genuine user commitment remain required independently of recording authorization. Owner and schema identifiers are compatibility contracts, not separate installation units.

This is the public, host-independent storage, recall, and write contract implemented by `context-core`. Product composition and distribution identity live in the repository and plugin READMEs; executable schemas and tests verify runtime behavior.

## Canonical storage and identity

- A vault is an ordinary directory containing `context/`. No Git executable, metadata, repository, or worktree is required.
- All storage CLIs accept global `--vault DIR` before the subcommand; addon init adapters accept the same option. `DIR` must be an existing directory. Without it, use the project-configured vault when present, otherwise the nearest current/ancestor directory containing a `context` entry, or the current directory when none exists. An invalid or symlinked `context` entry is not skipped in favor of an ancestor. Explicit selection never falls back to another vault.
- Input file paths remain relative to the caller's working directory. Use the same vault for owner operations, preview, and apply. `schema` and `capabilities` remain independent of vault selection; core advertises `filesystem-vault/v1`, which addons require before invoking storage commands.
- Markdown artifacts are canonical. `context.index.md` and `<area>.index.md` are deterministic projections.
- Artifact IDs are `ctx_` plus 32 lowercase UUIDv4 hex characters. Filename, title, path, and lifecycle changes do not change the ID.
- Frontmatter is a one-line-per-field JSON-compatible YAML subset. Bodies use fixed ordered H2 sections for each artifact schema.
- `claim_fingerprint`, `source_claim_fingerprint`, and candidate `claim_key` are removed. Candidate IDs are transport references only. Legacy removed fields are readable with `schema_removed_field` warnings and are lazy-cleaned on the next approved rewrite; new input fails closed.

## Read boundary

- ARCHIVE stores immutable source material adopted as evidence. It is excluded from default recall and pack; `--include-archive` or the dedicated `archive read|search` surface is required.
- A healthy Stage-1 metadata hit reads only the root and selected area index. It performs zero artifact opens/lists/stats.
- A healthy non-empty query with zero metadata matches enumerates filenames only to detect unindexed artifacts. When all files are indexed, it opens zero bodies and is not marked as fallback.
- Missing bodies, a broken selected area index, and a missing selected link share a recovery budget of at most 20 artifact body opens per recall. Truncation returns `index_miss_fallback_truncated` or `index_fallback_truncated`.
- `--strict-index` disables fallback and fails with exit 6 `index_stale`.
- A missing root index is `context_root_missing`, not a plugin dependency error.

## Bounded-cost scope

Hard bounds apply to artifact body materialization/open, selected output, candidate envelopes, and model/owner invocation input; they prevent those costs from growing in proportion to corpus size, addon count, or accumulated turns.

- Healthy Stage 1: index-only and zero artifact opens/lists/stats.
- Recovery: at most 20 body opens.
- Stage-1 output: 4 KiB; selected body pack: 8 KiB; ordinary approval preview: 32 KiB.
- Common primary-claim protocol ceiling: 2,000 codepoints; built-in SNAP `current_context` and OBS `observation`: 1,200 codepoints each; canonical owner input: 8 KiB.
- `context-capture-batch/v1`: at most eight candidates and at most 16 KiB for the full canonical `{schema,audit_count,candidates}` envelope. `audit_count` is the non-bool integer `1`; a legacy bare list is charged against the same synthetic envelope budget.
- These limits are default-read budgets. Knowledge expands through more stable slots, not larger slots. Explicit ARCHIVE capture is the exception: immutable `Content` is at most 65,000 codepoints and its candidate, owner input, and approval preview use a 512 KiB envelope ceiling.
- DEC additionally limits its primary decision to 1,200 codepoints, brief to 8 KiB, comparison input to 24 KiB, result to 32 KiB, and spec-view stdout to 32 KiB.

Index bytes, metadata-row scoring, and directory enumeration can grow with the selected index or directory. The implementation avoids corpus-wide body materialization; it does not guarantee O(1) end-to-end recall computation or model tokens. `tests/context-v1/test_token_io_evidence.py` measures the enforceable artifact I/O, candidate, addon, and stdout budgets.

The managed policy requires one audit per conversation delta, silence when there is no signal, and no addon re-reading of the conversation. The CLI validates `audit_count:1` and bounded envelopes, but host/model compliance is not a hard runtime guarantee.

## Conversation boundary

- Audit only the new user-turn meaning in the same response pass; it is not a background daemon, extra model call, or per-turn CLI hook. For a behavior/contract-neutral mechanical edit, skip AGENTS/guidance discovery and exclude `context/`. Inspect a named path only. Without one, infer one conventional task subtree from the request and search it once, then use the exact file. Never use `.`, `--hidden`, repository-wide globs, or the repository root; if no safe subtree follows from the request, ask for the path instead of widening. Keep context tool calls, `context/` artifact reads, and context mentions at zero.
- Keep only scope/anchor, already read `{id,sha256}`, and pending/dismissed references in a bounded session-local ledger. Do not copy bodies or candidates into repository state.
- Recall metadata first and materialize selected bodies only. Reuse a body only while scope, evidence, anchor, indexes, SHA, and its session presence remain unchanged. Escalate only through the needed steps: silent index check, selected body read on a match, user mention for an action-changing finding, then a question only when an answer is required.
- Report conflict or rationale change before the primary conclusion, then hold the affected action until the user answers: keep means it is not performed; supersede permits it only after that explicit choice. A satisfied revisit condition authorizes reassessment, not implementation. The explicit choice settles that decision payload and authorizes its capture without a second storage question. Propose ordinary capture only after a mature milestone, and do not repeat dismissed/deferred candidates without new evidence.

## Write and approval boundary

- A semantic owner returns complete drafts/effects and a proposed `context-owner-plan/v1`; it performs no physical write.
- `transaction preview` seals target preconditions, material, derived index rebuild, and owner/area authorization into `context-mutation-bundle/v1`.
- `approval_digest` is the compatibility name for the canonical SHA-256 of the complete frozen material. It is an internal integrity binding, not evidence of user approval. Its `vault_identity` is exactly `{schema:"context-vault-identity/v1",root:{path,device,inode}}`, with the resolved absolute vault path and decimal device/inode strings. Extra or missing identity fields are invalid.
- Apply verifies the digest and current vault identity before any write. A copied, moved, or same-path recreated vault needs a new preview. Unrelated files and optional version-control metadata are not bound; exact target CAS still protects affected bytes. Previously generated approval bundles and receipts with the old identity must be discarded and previewed again; saved Markdown artifacts require no migration.
- Only context-core writes, under a vault-realpath lock, with atomic operations and deterministic index rebuild.
- Hidden operations, missing seeds, altered material, changed target preconditions, path escapes, and symlink segments fail before writes.
- OBS `Evidence` items that are exact `ctx_` IDs are internal references. Refresh validates their targets, while arbitrary evidence strings remain allowed. ARCHIVE has only capture, read, search, and discard; it has no update, rename, retirement, or supersede transition.
- Refresh emits non-blocking `document-stale-vs-decision` hygiene warnings when a Current DEC with `affects:document` is newer than the target Current DOCUMENT's last update time.

### Core workflow receipt

- `context-core-workflow-receipt/v1` has exactly seven fields: `schema`, immutable `status:pending`, offset-aware `created_at`, `plan_id`, `core`, `plan_bundle`, and `receipt_digest`. `plan_id` is filename/projection metadata and must equal `plan_bundle.approval_material.plan.plan_id`; it is not semantic identity. `core` is exactly `{path,sha256}` and `plan_bundle` is the exact existing mutation bundle. Candidate IDs and a duplicate core bundle are forbidden.
- `receipt_digest` is canonical SHA-256 over the other six fields and detects receipt damage only. The workflow digest over exactly `{core,plan_bundle}` is returned by internal preview and retained outside the receipt in agent state. `transaction apply --receipt-file ... --approved-digest ...` requires that independent value, then verifies current core path/SHA and passes only the validated nested `plan_bundle.approval_digest` to core apply. Neither digest is user-approval evidence.
- Flag-based OBS/SNAP capture writes one frozen receipt to `tempdir/context-core/<plan_id>.json`; the directory is mode `0700`, the regular file is `0600`, and the prospective path is validated outside the vault before any directory creation. An explicit path never creates the default directory. Receipt targets, direct parents, traversal, symlinks, modes, and inode identity are checked fail-closed.
- Receipt selection is only the exact path retained by the agent from preview; core never scans a directory or chooses a newest file. Surface tri-state is: no receipt option with `--attestation @file` returns the legacy bundle, the complete built-in flag pair uses the private default receipt, and explicit `--receipt-file` uses only that path. Existing `--plan-bundle @file` apply remains available.
- OBS flags map to `reusable_observation → /owner_inputs/observation/observation` and `evidence_present → /owner_inputs/observation/evidence/0`. SNAP maps `handoff_requested → /owner_inputs/snapshot/current_context` and `unfinished_context_present → /owner_inputs/snapshot/open_items/0`. Partial, absent, or mixed file/flag attestation fails before receipt or repository writes.
- Successful receipt apply deletes the receipt. Cleanup-only failure returns `applied:true` plus `receipt_cleanup_failed`; a fully applied receipt is rejected on retry without writes. Core adds no receipt locator, keep, reject, or TTL lifecycle.

## Generic owner descriptor

`context-owner-descriptor/v1` bytes remain supported and v1/v2 areas may coexist. A v2-capable runtime advertises `context-owner-descriptor/v2` in root-independent `schema.features`. No automatic upgrade, downgrade, migration, deletion, or descriptor replacement exists.

- A v2 descriptor is canonical UTF-8 JSON of at most 8 KiB and contains identity plus a closed `context-structural-profile/v1`.
- Unknown/duplicate keys, non-canonical input, executable defaults, regex/expression/callback fields, and depth/node/field/item bound violations fail closed.
- Profiles allow at most 24 closed-type fields, 12 ordered H2 sections, and four scalar index projections. Lifecycle topology is limited to `create_current|replace_same_state|retire_current|supersede_current|delete_one`.
- A v2 semantic receipt binds descriptor digest, capability, owner result, base area index, ordered same-area prior bundles, topology, and semantic input digest. Core independently validates target envelopes, fields, sections, projections, lifecycle, relations, paths, indexes, CAS, and operations during preview and again under lock during apply.
- `typed-relations/v1` keeps the existing relation map bytes. A key shaped as `<predicate>:<target-kind>` requires every target ID to resolve to that live kind during preview and apply revalidation; refresh and doctor report the same mismatch. A key without `:` remains an untyped legacy relation. Core creates neither inverse relations nor a graph index.
- The root registry and area descriptor must carry the same immutable digest. A trust mismatch is a blocking doctor/refresh issue and `refresh --fix index` does not repair it.

## CLI envelope and init

- success: `{"ok":true,"result":{...}}`
- error: `{"ok":false,"error":{"code":"...","message":"...","details":{...}}}`
- exit 2: usage/schema/filename; exit 3: root/artifact/input missing; exit 5: owner/path/lifecycle conflict; exit 6: integrity/index failure

`doctor` preserves the four repository states (`absent|partial|invalid|ready`) and exact existing readiness meaning while adding `plugin_version`, resolved `entrypoint`, and `protocol` to its ten-field self-report.
- `schema` and `capabilities` work without a repository root.

`init --host codex|claude-code` applies an absent root's canonical root/SNAP/OBS/ARCHIVE seeds and the active host policy (`AGENTS.md` or `CLAUDE.md`). A healthy pre-ARCHIVE vault receives only the missing empty ARCHIVE area. It preflights policy targets/markers before storage writes and preserves bytes outside the managed block.

`bootstrap --descriptor @file --index-seed @file --host ...` is the public addon-init surface. In one call it completes core init, registers an exact empty area, and installs the same policy. Only fixed `core_init|area_register|policy_install` transitions receive explicit-init authority; all user-content mutation remains approval-gated.

Lifecycle semantic input includes actual predecessor/successor primary claims, bounded supporting context, artifact path/ID/SHA, and source candidate digest. A `same_claim` attestation must point to both actual claims; hash-derived identity cannot substitute.
