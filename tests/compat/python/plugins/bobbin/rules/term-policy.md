---
description: Apply the authoritative project terminology boundary and keep all persistence in context-core.
alwaysApply: true
---

- Claim only a term with an explicit project-specific or project-special meaning. Decline generic dictionary meanings.
- Decline observed facts to OBS, committed choices to DEC, unverified premises to ASM, and any mixed-owner input.
- Bind claim attestation to the exact candidate digest and RFC 6901 pointers `/owner_inputs/term/term` and `/owner_inputs/term/definition`.
- Reject canonical-key overlap across actual Current term, aliases, and deprecated terms in exact, ancestor, or descendant scopes. Reject overlap within one artifact.
- `supersede` same-claim evidence must cite both actual term and Definition bodies. IDs, hashes, fingerprints, and index metadata are not semantic evidence.
- `deprecate` requires a reason and any replacement must be a different canonical key. `annotate` may not change meaning.
- `updated_at` and `retired_at` cannot precede source `created_at`; tag and search-term items retain the core 40-character common limit.
- Use search or read only after an exact `term-encountered` signal, never automatically for every term.
- Rebuild owner results from live Current source and the candidate or request before issuing a receipt. Read no canonical-area escape or symlink component.
- Run ordinary operations only with an exact ready doctor. `partial` is allowed only for explicit init; `invalid` blocks every operation.
- Follow context-core's active-language contract. Use the active language for responses, semantic confirmation questions, and explanatory errors. Keep machine-readable surfaces and internal previews in English and preserve artifact prose without semantic translation.
- The semantic owner never writes vault bytes. Follow the shared [recording policy](../skills/context/references/recording-policy.md): `explicit` uses user semantic approval, `auto` uses project policy, and `adaptive` requires a record/ask assessment. Only enabled owners participate automatically. Semantic evidence and lifecycle checks remain mandatory. All modes use internal preview and unchanged apply in the same response, with `approval_digest` and receipt paths private. Never regenerate authorized meaning.
