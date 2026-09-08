---
name: term
description: When a project-specific term can change interpretation, recall, compare, and propose a TERM lifecycle result without writing the repository.
---

# Context term

Follow the [shared recording policy](../context/references/recording-policy.md) first. On a durable signal, resolve project settings once; only enabled owners participate automatically. User-approval instructions below describe `explicit` mode. In `auto` and `adaptive`, use policy authorization on the same validated write path; semantic attestations must remain truthful. Disabled features still allow explicit historical reads.

This skill is the `context-term/v1` semantic owner and never writes repository bytes.

- Claim only a term with an explicit project-specific or project-special definition. Decline generic dictionary meanings, observed facts, committed choices, unverified premises, and mixed-owner input.
- Bind claim attestation to the exact candidate and RFC 6901 pointers `/owner_inputs/term/term` and `/owner_inputs/term/definition`.
- Reject intersecting canonical keys across actual Current term, aliases, and deprecated terms in exact, ancestor, or descendant scopes. Reject overlap within one artifact as well.
- `supersede` requires direct citation of both actual term and Definition bodies as the same project terminology claim. IDs, hashes, fingerprints, and index metadata are not semantic evidence.
- `deprecate` requires a reason; an optional replacement must be a different canonical key. `annotate` may not change semantic fields.
- Use `search` or `read` only after an exact `term-encountered` signal, not automatically for every term.
- Rebuild owner results from live Current source and the candidate or request before issuing a receipt. Read no path outside the canonical area and no symlink component.

For capture, use sibling `scripts/term_workflow.py preview --host <host> --core-cli <loaded-core-cli> --inline ...`, then its `apply` command only after approval. It derives verified inventory and doctor state directly; do not hand-build preflight JSON. Keep its receipt path and approval digest in agent state only.

Follow context-core's active-language contract. An explicit user language choice wins; otherwise use the host preference, then the established conversation language, then English. OS locale is not authoritative, and code, filenames, quotations, or one foreign term do not switch language. Use the active language for user-facing responses, questions, previews, and explanatory errors. Keep machine-readable surfaces in English and preserve artifact prose without semantic translation.

A direct, explicit, unconditional user statement that settles the term payload, scope, and lifecycle effect is semantic approval. Ask one concise question only for unresolved meaning; acknowledgement, praise, a condition, edit request, or topic change is not approval. Do not show the rendered file body or ask a second storage question. After approval, run internal preview, verify no semantic delta, and pass stdout's `approval_digest` unchanged to apply in the same response. Keep the digest, receipt path, internal ID, and core path private. If a delta appears, hold the write and confirm only that delta. Never regenerate after approval.

The detailed contract is in [term-protocol.md](references/term-protocol.md).
