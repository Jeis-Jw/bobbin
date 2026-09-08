# context-decision/v1 owner protocol

Bobbin 1.0.0 uses one package and one `$bobbin:init` entrypoint. User-approval instructions below describe explicit mode; auto/adaptive use the same validated path under the [shared recording policy](../../context/references/recording-policy.md). Semantic validity and genuine user commitment remain required independently of recording authorization. Owner and schema identifiers are compatibility contracts, not separate installation units.

`decision_cli.py` is the semantic owner of `context-decision/v1`. It builds complete DEC drafts, lifecycle effects, `context-owner-plan/v1`, `context-owner-validation-receipt/v1`, and bounded recall. It never creates directories, writes files or indexes, locks repositories, seals final approval digests, or applies bundles. Context-core is the only physical coordinator.

## Dependency and executable boundary

- marketplace: `bobbin`
- plugin: `bobbin`
- selector: `bobbin@bobbin`
- repository source: `Jeis-Jw/bobbin`
- protocol: `context-common/v2`
- core entrypoint suffix: `skills/context/scripts/context_cli.py`

`schema`, `capabilities`, `check`, `search`, `read`, `brief`, `spec-view`, `conflicts`, and `revisit` are core-free. Write-pipeline operations retain caller `--host`, `--core-inventory @file`, and `--core-doctor @file` as compatibility input.

Canonical init and workflow first validate the absolute core entrypoint path suffix, matching adjacent Claude/Codex manifests, and the compatible major, then bind the actual SHA-256 for the operation. Before executing further subprocess work they directly validate `context-core-schema/v1`, `context-common/v2`, required doctor/bootstrap/transaction commands, `context-owner-descriptor/v2`, `filesystem-vault/v1`, and the exact doctor shape/state. This executable handshake does not attest marketplace provenance, source, scope, or enabled state.

The decision owner uses the core embedded in the same Bobbin package. It does not install, enable, update, add a marketplace, probe plugin caches, or copy a separate runtime. `repository_state=absent` is bootstrap-required; partial/invalid diagnostics are not a global semantic-operation failure unless they overlap an actual target.

## Semantic claim gate

A DEC is an explicit choice governing current or future action. The exact candidate must bind all assertions:

- `explicit_choice` -> `/owner_inputs/decision/decision`
- `scope_identified` -> `/scope_hint`
- `commitment_present` -> `/evidence/*`

Ideas, questions, facts, preferences, and unaccepted proposals are `decline` or `needs_clarification`. `requested_kind:"decision"` selects an owner but never bypasses this gate. The CLI validates assertion names, exact input digest, and RFC 6901 pointers; it does not make semantic judgment for the agent.

Low-level `candidate prepare` normalizes caller-provided semantic fields, a caller-provided `cand_` plus 32 lowercase hex transport ID, commitment evidence, and bounded search terms. A transport ID may be generated mechanically but has no semantic weight. The owner never invents candidate meaning without caller input. Canonical workflow may generate only that UUID4 transport ID and default `captured_from` to `conversation`; it never invents semantic fields, evidence, or attestation. The owner then calls `capture --candidate @file --attestation @file`, or exits without an authoritative draft via a decline/clarification reason. Constant evidence and self-attestation are forbidden.

## DEC schema and slot

The canonical required body sections are `Decision`, `Rationale`, and `Rejected alternatives`. Missing, empty, or placeholder content fails. If no alternative was reviewed, record an explicit reason such as `Not reviewed: <reason>`. Optional canonical sections are `Evidence and constraints`, `Trade-offs`, and `Revisit conditions`. The Korean names `결정`, `취지`, `반려대안`, `근거와 제약`, `트레이드오프`, and `재평가 조건` are legacy read and round-trip aliases only; new artifacts use the canonical English headings. `verified_at` and common `status` are forbidden.

Scope canonicalization is trim -> NFKC/casefold -> remove outer slashes -> normalize each non-alphanumeric run to `-`. Empty/dot segments, over eight segments, a segment over 40 characters, and total scope over 160 characters fail. `decision_key` uses the same normalization, forbids `/`, and is at most 80 characters.

Current has at most one DEC per `(scope, decision_key)`. Same-key ancestor/descendant scope is an overlap conflict and requires acknowledgement plus exact `{id,path,sha256}` read preconditions. The agent compares actual bodies; fingerprints are never semantic identity.

## Owner results and lifecycle

Capture returns one Current draft/effect/create operation. Draft time fixes ID and `created_at` and binds embedded candidate, claim attestation, complete content, and semantic projection.

- `supersede`: successor repeats the predecessor's canonical slot; one result moves the predecessor to History and creates the successor with reciprocal `superseded_by`/`supersedes` edges.
- `withdraw`: retire the predecessor as withdrawn with no successor.
- `annotate`: preserve decision sections, slot, and ID; change only title, summary, tags, search terms, and source refs.
- `revisit`: return a warning/review proposal without mutation.

Ordinary OBS evidence remains active and is linked by `relations.informed_by`. A decision-like fallback OBS import requires exact source ID/path/SHA/actual claim, `same_claim`, and a cross-owner coordinator plan.

DEC remains valid without INTENT, DOCUMENT, OBS, or ASM references. Optional owner inputs `serves_intents`, `informed_by_observations`, `informed_by_assumptions`, and `affects_documents` project to typed relation keys `serves:intent`, `informed_by:observation`, `informed_by:assumption`, and `affects:document`. Legacy top-level `informed_by` continues to project to the untyped `informed_by` key and keeps its prior parser/cardinality behavior and bytes. Core validates typed target existence and kind without rewriting existing artifacts or storing inverse edges. The workflow conditionally requires Core `typed-relations/v1` only when a typed input is non-empty; standalone and legacy-untyped DEC operations retain the prior same-major handshake.

## Same-batch validation

`batch validate` starts from the exact physical `decision.index.md` SHA and overlays prior same-area final bundles in proposal order. Each plan must bind the exact preceding digest sequence. Virtual Current enforces slot uniqueness, overlap acknowledgement/read preconditions, and lifecycle predecessor state.

A successful receipt binds owner-result digest, base area-index SHA, ordered same-area prior digests, canonical scope/key, actual primary claim, rationale, acknowledged conflicts, and its own digest rule. A missing or altered receipt fails final plan validation.

## Frozen workflow receipt

The ordinary capture path is `decision_workflow.py record --approved`; it executes the following frozen workflow internally. Low-level orchestration can still call `decision_workflow.py preview --inline` then receipt-locator-free `apply` after semantic approval. The caller supplies semantic fields and explicitly attests choice, scope, and commitment; candidate ID and `captured_from:conversation` are defaults. The agent verifies that the frozen result adds no semantic delta, retains stdout `result.approval_digest` outside the receipt, and passes it unchanged through internal `--approved-digest` without showing the rendered file body or asking a second storage question. The digest is an integrity binding and is never shown to or requested from the user. `preview --supersede <id>` and `preview --withdraw <id> --reason <text>` reuse the same path. Withdraw has no semantic candidate, so workflow generates a UUID4 transport ID only for the private receipt filename/envelope; it does not enter the owner result or approval preview. Preview verifies a same-major core from its adjacent manifests, binds its actual digest, directly handshakes schema and doctor, builds/revalidates the owner result, and calls core transaction preview in one process.

The finalized `context-decision-workflow-receipt/v1` envelope has exactly `schema`, `status`, `created_at`, `candidate_id`, `operation`, `approval_material`, `approval_digest`, and `receipt_digest`. `status` is immutable `pending`; `operation` is `capture|supersede|withdraw`; `created_at` is timezone-aware RFC 3339. Approval material has exactly `schema`, `vault_identity`, `core`, `operation`, `workflow_input_digest`, `owner_result_digest`, `core_approval_digest`, and `core_bundle`. The digest binds exact `context-vault-identity/v1`, core absolute path/pinned SHA-256, operation/input/result digests, nested core approval digest, and the complete nested bundle. `receipt_digest` detects damage, not approval, and the receipt's own `approval_digest` is not an independent approval channel. Even a fully rehashed receipt must equal the agent-retained preview stdout digest before any write.

Canonical preview validates the prospective default path outside the vault before creating an owner-only mode `0700` `tempfile.gettempdir()/context-decision` directory and one mode `0600` `<candidate_id>.json` regular file. An explicit `--receipt-file` does not create or inspect the default directory. Preview refuses an existing unsafe directory instead of migrating or repairing it. Path traversal, symlink, non-regular file, wrong mode, repository-local receipt, copied/moved/same-path-recreated vault replay, changed core bytes, wrong approval, changed precondition, and CAS/lock failure remain fail-closed.

Automatic apply/reject considers only default-name, regular mode-`0600`, valid v1 receipts whose mtime is at most 24 hours old, vault identity and core path/SHA match, and frozen plan preconditions still describe the pending repository state. Exactly one match is selected; zero and multiple matches fail closed, and mtime/newest never breaks a tie. Pending is derived from the frozen core plan without mutating or regenerating the receipt, so applied and stale kept receipts are ineligible. Canonical apply omits only the receipt locator and always carries the external preview digest through required `--approved-digest`; explicit `--receipt-file` retains the same receipt, binding, digest, and core apply validation.

Successful apply removes the receipt unless `--keep-receipt` is set. Cleanup failure preserves `applied:true`, appends `receipt_cleanup_failed`, and never retries the repository write. `reject` removes only the selected receipt and writes no repository bytes. Each preview first sweeps default-name regular mode-`0600` valid v1 receipts older than 24 hours; symlinks, directories, unexpected names, malformed JSON, and other schemas are untouched. A kept applied receipt stays byte-for-byte frozen, is excluded from automatic selection immediately, and becomes sweep-eligible after the same TTL.

Inline `--sec-*` values are literal by default. `@file` reads a named regular UTF-8 file; `@@literal` preserves one leading `@`; path-like plain text remains literal. Missing, symlinked, and oversized files fail before receipt or repository writes. The common primary-claim protocol ceiling is 2,000 codepoints; built-in SNAP `current_context`, OBS `observation`, and DEC `decision` each use an owner-specific 1,200-codepoint ceiling. Canonical owner input is at most 8 KiB and the complete candidate envelope at most 16 KiB.

## Recall, spec view, and init

`search` reads decision index metadata. `read` and `brief` open selected DEC bodies only. Brief includes the three required sections and is at most 8 KiB. History always carries `do_not_follow:true` and a lifecycle reason.

`check` accepts either both `--scope` and `--decision-key` or neither; a partial pair is `usage_invalid`. When coordinates are known, make one exact check. The exact pair yields `coverage:exact_slot`, always includes exact-slot and scope-overlap candidates, then adds only distinctive metadata matches. Omitting both yields lexical-only `coverage:discovery_only` plus the exact caveat `no-conflict cannot be concluded; re-run with exact scope/decision_key before preview`. It never opens arbitrary score-zero bodies. Comparison input is at most 24 KiB and the complete result at most 32 KiB. Each selected item returns actual `Decision`, `Rationale`, `Rejected alternatives`, plus non-empty `Revisit conditions` under `sections`; there is no top-level revisit duplicate. Reuse these sections in the turn without another read. The agent returns `new|same|supporting|rationale_changed|conflict`; `new` is bounded to the returned set and discovery-only cannot establish no conflict.

For `rationale_changed|conflict`, quote every returned non-empty actual Decision, Rationale, Rejected alternatives, and Revisit conditions section before the primary conclusion. Hold the affected action and ask one explicit binary question offering both choices: keep means it is not performed; supersede permits it only after that explicit choice. State the selected Revisit token verbatim as `satisfied|no evidence|ambiguous` without invented evidence. `satisfied` requires user-supplied present facts that directly establish the stored condition; the requested conflicting action itself is not evidence. Facts that are absent or concern something other than the stored condition mean `no evidence`. Use `ambiguous` only when user-supplied facts about the stored condition are relevant but incomplete or conflicting. A satisfied condition authorizes reassessment, not implementation. The explicit choice settles that decision payload and authorizes capture without a second storage question.

`spec-view --scope` selects exact/strict-ancestor/strict-descendant Current DEC metadata and materializes only the canonical `Decision` and `Rationale` fields, ordered by `(created_at,id)`. Existing documents headed `결정` and `취지` are accepted as legacy read aliases and projected under the canonical English JSON keys; their stored headings are not silently rewritten. It excludes History and `do_not_follow`. Complete JSON stdout including the final newline is at most 32 KiB; trailing entries are omitted whole and counted.

Init verifies a same-major, v2-capable core and doctor while holding its actual digest constant, then passes the DEC area's exact legacy-compatible `context-owner-descriptor/v1` and empty DEC index seed to core bootstrap. One call may complete core root setup, DEC registration, and managed policy installation. The decision CLI itself writes none of those bytes.

## Output

Success is `{"ok":true,"result":...}`. Error is `{"ok":false,"error":{"code":...,"message":...,"details":...}}`. Exit 2 covers usage/schema, 3 not-found, 5 conflict, and 6 integrity/index failures. Every semantic operation must leave repository filesystem bytes unchanged.

When `--search-term` is omitted, direct candidates derive at most twelve metadata terms from Decision, Rejected alternatives, Rationale, and Revisit conditions. Title/summary terms are excluded from this derived list because they are already indexed. Discovery compares deterministic word stems and ranks distinctive hits; `frequency_cutoff` still removes common noise. Stored bodies and explicit search terms remain authoritative input, and retrieval scores never decide semantic conflict.

Discovery keeps the existing frequency cutoff (at most one quarter of Current, rounded up) to discard corpus-wide noise. Remaining hits use inverse document frequency with additional title and decision-key weight. Silent-e and consonant-y normalization also matches preserve/preserving and boundary/boundaries. The first result has the highest lexical score; later discovery candidates discount overlap with already selected metadata so repeated near-topic records do not consume the entire body-read budget. This metadata-only diversity step never applies to exact-slot or scope-overlap coverage, changes no stored bytes, and does not establish semantic identity or conflict. For the same choice in the same governing scope, reuse the returned scope and decision_key instead of minting an alias.

## One-call record

`decision_workflow.py record` runs the same `preview` and then the same `apply` on the unchanged frozen receipt inside one process. The caller passes `--approved` only after the user's direct, explicit, unconditional semantic approval in conversation; `approval_digest`, the pinned core SHA, vault identity, CAS, lock, and atomic write are bound internally exactly as in the two-phase path, and the receipt is removed on success unless `--keep-receipt` is set. `--supersede` and `--withdraw` work with `record` as with `preview`. `--core-cli` is optional for `preview`, `apply`, and `record`: exactly one same-major, manifest-validated sibling `context-core` is resolved deterministically; zero or several candidates fail closed with `core_cli_required` and the candidate list. The two-phase `preview` and `apply` commands remain for orchestration.
