# Term protocol

Bobbin 1.0.0 uses one package and one `$bobbin:init` entrypoint. User-approval instructions below describe explicit mode; auto/adaptive use the same validated path under the [shared recording policy](../../context/references/recording-policy.md). Semantic validity and genuine user commitment remain required independently of recording authorization. Owner and schema identifiers are compatibility contracts, not separate installation units.

## Artifact

`context-term/v1` follows common frontmatter and the descriptor v2 structural profile.

| Location | Field | Contract |
|---|---|---|
| frontmatter | `scope` | Required, 1..160 characters |
| frontmatter | `term` | Required, 1..120 characters |
| frontmatter | `term_key` | Required and deterministically derived from `term` |
| frontmatter | `aliases` | Optional, at most 12 items |
| frontmatter | `deprecated_terms` | Optional, at most 12 items |
| frontmatter | `related` | Optional, at most 12 items |
| H2 | `Definition` | Required primary claim; legacy alias: `정의` |

Current authority is `authoritative`. `term_key` applies NFKC and case-folding, then normalizes whitespace and punctuation to one `-`. The actual canonical-key set of `{term, aliases, deprecated_terms}` for each Current artifact cannot intersect with another Current artifact in exact, ancestor, or descendant scope. The same fields cannot overlap within one artifact.

History adds `retired_at`, `retired_reason`, and the deprecation or reciprocal-supersession payload required by the selected reason recipe. New artifacts use the English `Definition` heading. Existing Korean-heading artifacts remain readable, and meaning-preserving mutation retains their original heading style. Artifact prose follows the user's active language and is never translated merely to match the English canonical structure.

## Claim boundary

Candidate transport IDs and artifact IDs are not semantic evidence. Semantic attestation binds to the candidate's canonical digest and exact RFC 6901 pointers. TERM declines:

- An already observed fact or evidence itself: OBS boundary.
- An accepted choice to follow now: DEC boundary.
- An unverified premise: ASM boundary.
- A generic dictionary definition or a word without an explicit project-specific signal.

Claim assertions are `term_identified -> /owner_inputs/term/term` and `definition_present -> /owner_inputs/term/definition`. Mixed owner kinds and structured foreign input are declined.

## Lifecycle and recall

- `supersede` requires the same scope and term key plus both predecessor and successor actual `{term, definition}` primary claims.
- `deprecate` requires a reason; an optional replacement term must occupy a different canonical key.
- `annotate` cannot change term, definition, term key, aliases, deprecated terms, or related terms.
- `updated_at` and `retired_at` cannot precede source `created_at`.
- Search and read require an exact `term-encountered` signal for an actually encountered ambiguous or project-specific term. Automatic lookup of every word is forbidden.

## Owner result and persistence

The TERM CLI produces `context-owner-result/v1` and `context-owner-validation-receipt/v2`. The receipt binds descriptor, capability, owner-result, and physical area-index digests; same-area prior-bundle order; generic topology; and semantic-input digests.

Before issuing a receipt, TERM rereads live source path, ID, SHA, actual primary claim, exact candidate and attestation, and the transition-specific mutation request. It regenerates artifact drafts, effects, and operations from those inputs and fails closed unless the complete result matches the submitted owner result. It rejects absolute paths, `..`, targets outside `context/term`, and symlink components before receipt, search, or read.

Core revalidates target bytes against the descriptor and performs preview, apply, lock, and CAS checks. TERM never writes repository or index files.

## Init handshake

Only `schema` and `capabilities` run without core. Low-level compatibility operations require exact host inventory and a core doctor receipt. Ordinary operations require `ready`; `partial` and `invalid` fail closed. The canonical init adapter accepts no caller-created inventory or doctor data. It first verifies the supplied core CLI's absolute path suffix, matching adjacent manifests, and compatible major, binds the actual SHA-256 for the operation, then directly handshakes schema, protocol, features, required commands, and doctor state on that core. After bootstrap it verifies public doctor and registry, descriptor, and index bytes.

The common primary claim and TERM `definition` limits are 2,000 codepoints. Canonical byte limits are 8 KiB for owner input, 16 KiB for both a candidate and the complete candidate-batch envelope, and 32 KiB for actual public output. Common tag and search-term items are limited to 40 characters.
