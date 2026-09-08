---
name: intent
description: When a durable desired project direction emerges, compare, recall, and propose an INTENT lifecycle result without writing vault bytes.
---

# Context intent

Follow the [shared recording policy](../context/references/recording-policy.md) first. On a durable signal, resolve project settings once; only enabled owners participate automatically. User-approval instructions below describe `explicit` mode. In `auto` and `adaptive`, use policy authorization on the same validated write path; semantic attestations must remain truthful. Disabled features still allow explicit historical reads.

This skill is the `context-intent/v1` semantic owner and never writes vault bytes.

- Claim a desired project direction. Decline observed facts, unverified premises, chosen commitments, living-document content, and mixed-owner input.
- Bind claim attestation to the exact candidate and RFC 6901 pointer `/owner_inputs/intent/intent`.
- Treat `(scope, intent_key)` as the exact authoritative Current slot.
- Keep `Success criteria`, `Constraints`, and `Revisit conditions` optional.
- `supersede` requires direct citation of both actual `Intent` bodies, retains the slot, and creates reciprocal lifecycle references.
- Rebuild every complete owner result from live Current bytes and exact semantic input before issuing a receipt.
- Use `search` and `read` for relevant direction recall. Do not require DEC or DOCUMENT to be installed.

Only context-core may prepare, lock, CAS, or persist a result. Semantic approval happens in the conversation; never regenerate approved meaning.

The canonical capture path is sibling `scripts/intent_workflow.py preview --host <host> --core-cli <loaded-core-cli> --inline ...`, followed after approval by its `apply` command. The wrapper derives verified manifest inventory and doctor state directly; do not create candidate, attestation, inventory, or doctor JSON files manually. Retain its `receipt_file` and `approval_digest` only in agent state.

Follow context-core's active language contract for user-facing text and keep machine fields English. A direct, explicit, unconditional user statement that settles the intent payload, scope, and lifecycle effect is semantic approval. Ask only about unresolved meaning; a generic acknowledgement, condition, edit request, or topic change is not approval. Do not show the rendered file body or ask a second storage question. After approval, run internal preview, verify no semantic delta, and pass stdout's `approval_digest` unchanged to apply in the same response. Keep the digest, receipt path, internal ID, and core path private. If a delta appears, hold the write and confirm only that delta. Never regenerate after approval.

See [intent-protocol.md](references/intent-protocol.md).
