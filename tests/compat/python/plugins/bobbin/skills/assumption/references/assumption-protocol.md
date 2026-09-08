# Assumption protocol

Bobbin 1.0.0 uses one package and one `$bobbin:init` entrypoint. User-approval instructions below describe explicit mode; auto/adaptive use the same validated path under the [shared recording policy](../../context/references/recording-policy.md). Semantic validity and genuine user commitment remain required independently of recording authorization. Owner and schema identifiers are compatibility contracts, not separate installation units.

## Artifact

`context-assumption/v1` follows common frontmatter and the descriptor v2 structural profile.

| Location | Field | Contract |
|---|---|---|
| frontmatter | `scope` | Required, 1..160 characters |
| frontmatter | `impacted_decisions` | Optional, at most 12 context IDs |
| H2 | `Assumption` | Required primary claim; legacy alias: `가정` |
| H2 | `Basis` | Required basis; legacy alias: `근거` |
| H2 | `Confirmation conditions` | Optional; legacy alias: `확정 조건` |
| H2 | `Refutation conditions` | Optional; legacy alias: `반증 조건` |

Current authority is `provisional`. History adds `retired_at`, `retired_reason`, and the evidence or reference payload required by the selected reason recipe. New artifacts use English headings. Existing Korean-heading artifacts remain readable, and meaning-preserving mutation retains their original heading style.

Artifact prose follows the user's active language and is never translated merely to match the English canonical structure.

## Claim boundary

Candidate transport IDs and artifact IDs are not semantic evidence. Semantic attestation binds to the candidate's canonical digest and exact RFC 6901 pointers. ASM declines:

- An already observed fact or evidence itself: OBS boundary.
- An accepted choice to follow now: DEC boundary.
- A question, idea, hope, or preference by itself.
- A claim that is not explicitly marked unverified.

## Owner result and persistence

The ASM CLI produces `context-owner-result/v1` and `context-owner-validation-receipt/v2`. The receipt binds descriptor, capability, owner-result, and physical area-index digests; same-area prior-bundle order; generic topology; and semantic-input digests.

Before issuing a receipt, ASM rereads live source path, ID, SHA, actual primary claim, exact candidate and attestation, and the transition-specific mutation request. It regenerates artifact drafts, effects, and operations from those inputs and fails closed unless the complete result matches the submitted owner result. It rejects absolute paths, `..`, targets outside `context/assumption`, and symlink components before receipt, search, or read.

Core revalidates target bytes against the descriptor and performs preview, apply, lock, and CAS checks. ASM never writes repository or index files.

## Init handshake

Only `schema` and `capabilities` run without core. Low-level compatibility operations require exact host inventory and a core doctor receipt. Ordinary operations require `ready`; `partial` and `invalid` fail closed. The canonical init adapter accepts no caller-created inventory or doctor data. It first verifies the supplied core CLI's absolute path suffix, matching adjacent manifests, and compatible major, binds the actual SHA-256 for the operation, then directly handshakes schema, protocol, features, required commands, and doctor state on that core. After bootstrap it verifies public doctor and registry, descriptor, and index bytes.

The common primary claim limit is 2,000 codepoints and ASM `assumption` is limited to 1,200 codepoints. Canonical byte limits are 8 KiB for owner input, 16 KiB for a candidate, and 32 KiB for actual public output. A candidate batch contains at most eight items, and the complete canonical UTF-8 `context-capture-batch/v1` envelope must not exceed 16 KiB.
