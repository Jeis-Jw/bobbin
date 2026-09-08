---
name: observation
description: Preserve reusable facts, evidence, or lessons as non-authoritative OBS context, including its lifecycle operations.
---

# Observation

Follow the [shared recording policy](../context/references/recording-policy.md) first. On a durable signal, resolve project settings once; only enabled owners participate automatically. User-approval instructions below describe `explicit` mode. In `auto` and `adaptive`, use policy authorization on the same validated write path; semantic attestations must remain truthful. Disabled features still allow explicit historical reads.

OBS is an immutable semantic claim with `authority: evidence`; never phrase it as a decision that future work must follow.

1. Capture requires substantive Observation and Evidence content, using only the capability descriptor.
2. `annotate` changes metadata only. Changed claim or evidence meaning requires a successor OBS and `supersede`; prepare both actual claims and keep reciprocal lifecycle edges in one final bundle.
3. `invalidate` requires a substantive disproof reason; `reverify` requires fresh evidence. Age alone does not retire evidence.
4. Keep ID and path, backlink, vault identity, CAS, lock, and atomic-write guards. Preview, prepare, and attestation write nothing.

Follow the active-language contract in `../context/references/active-language.md`. Write user-facing responses, questions, previews, and explanatory errors in the active language. Preserve user-authored artifact prose in its original language; identifiers and machine-readable fields remain English.

Treat a direct, explicit, unconditional user statement that settles the observation, scope, and capture effect as semantic approval. If meaning is unresolved, ask one concise semantic question; acknowledgement, praise, a condition, edit request, or topic change is not approval. Do not show the rendered file body or ask a second storage question. After approval, run internal preview, verify it adds no semantic delta, and pass its receipt path and `approval_digest` unchanged to apply in the same response. Keep all transport details private. If a delta appears, hold the write and confirm only that delta. Never regenerate after approval. Receipt self-digests are damage checks, not approval evidence, and no directory scan is allowed. Successful apply removes the receipt; a cleanup-only warning means the write succeeded and must not be retried.

Use `../context/scripts/context_cli.py observation ...`; context-core remains the only writer.

```bash
python3 /loaded/bobbin/skills/context/scripts/context_cli.py observation preview --title '<title>' --summary '<summary>' --captured-from workspace --attest-reusable-observation --attest-evidence-present --sec-observation '<claim>' --sec-evidence '<evidence>' --json
python3 /loaded/bobbin/skills/context/scripts/context_cli.py transaction apply --receipt-file '<agent-retained result.receipt_file>' --approved-digest '<agent-retained result.approval_digest>' --json
```

`observation capture` remains a deprecated compatibility alias. Both preview names return `applied:false` and `state:"awaiting_approval"`; neither records an OBS before `transaction apply` succeeds.
