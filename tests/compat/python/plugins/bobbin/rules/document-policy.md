---
description: Apply the living-document boundary and leave all durable writes to context-core.
alwaysApply: true
---

- Claim current-state Content consumed by an agent or person through recall/envelopes; decline external deliverables and route evidence, premises, direction, and commitments to their owners.
- Preserve the exact `(scope, document_key)` Current slot.
- Update only Content under the same ID, path, slot, and state.
- Re-derive drafts from live source and bound inputs before receipt validation.
- Never add taxonomy, inverse relations, or another lifecycle. Delegate persistence to context-core.
- Treat each slot as one default-read budget; split larger knowledge into stable chapter slots instead of enlarging a slot.
- Follow context-core's active language contract for user-facing text and keep machine fields English.
- The semantic owner never writes vault bytes. Follow the shared [recording policy](../skills/context/references/recording-policy.md): `explicit` uses user semantic approval, `auto` uses project policy, and `adaptive` requires a record/ask assessment. Only enabled owners participate automatically. Semantic evidence and lifecycle checks remain mandatory. All modes use internal preview and unchanged apply in the same response, with `approval_digest` and receipt paths private. Never regenerate authorized meaning.
