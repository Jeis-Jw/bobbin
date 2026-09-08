---
name: assumption
description: When an unverified project-scoped premise can change later judgment, recall, compare, and propose an ASM lifecycle result without writing the repository.
---

# Context assumption

Follow the [shared recording policy](../context/references/recording-policy.md) first. On a durable signal, resolve project settings once; only enabled owners participate automatically. User-approval instructions below describe `explicit` mode. In `auto` and `adaptive`, use policy authorization on the same validated write path; semantic attestations must remain truthful. Disabled features still allow explicit historical reads.

This skill is the `context-assumption/v1` semantic owner and never writes repository bytes.

- Claim only an unverified premise with project scope and a substantive basis. It must not be an observed fact or committed choice. The actual claim must equal `owner_inputs.assumption.assumption` and require `unverified_ok=true`.
- Bind attestation only to `assumption_present -> /owner_inputs/assumption/assumption` and `unverified_ok -> /owner_inputs/assumption/unverified_ok` on the exact candidate. Decline OBS or DEC claims and requested-kind-only inputs.
- Use metadata-first `search --signal assumption-relevant`, then selected actual-body `read`, only when a new, confirmed, refuted, or changed assumption can affect the current answer.
- `confirm` requires evidence references. `refute` requires a reason, evidence references, and explicit impacted decisions. `supersede` requires both actual Assumption bodies to express the same semantic claim. `annotate` may change only meaning-preserving metadata.
- Keep at most eight candidates and a 16 KiB canonical batch. Validate through v2 `batch validate` before handing the result to context-core preview. Frozen receipt, vault identity, core SHA, CAS, lock, and atomic-write checks remain unchanged.

For capture, use sibling `scripts/assumption_workflow.py preview --host <host> --core-cli <loaded-core-cli> --inline ...`, then its `apply` command only after approval. It derives verified inventory and doctor state directly; do not hand-build preflight JSON. Keep its receipt path and approval digest in agent state only.

Follow context-core's active-language contract. An explicit user language choice wins; otherwise use the host preference, then the established conversation language, then English. OS locale is not authoritative, and code, filenames, quotations, or one foreign term do not switch language. Use the active language for user-facing responses, questions, previews, and explanatory errors. Keep machine-readable surfaces in English and preserve artifact prose without semantic translation.

A direct, explicit, unconditional user statement that settles the assumption payload, scope, and lifecycle effect is semantic approval. Ask one concise question only for unresolved meaning; acknowledgement, praise, a condition, edit request, or topic change is not approval. Do not show the rendered file body or ask a second storage question. After approval, run internal preview, verify no semantic delta, and pass stdout's `approval_digest` unchanged to apply in the same response. Keep the digest, receipt path, internal ID, and core path private. If a delta appears, hold the write and confirm only that delta. Never regenerate after approval.

The detailed contract is in [assumption-protocol.md](references/assumption-protocol.md).
