---
name: archive
description: Preserve immutable long-form source material that has been adopted as evidence for durable context.
---

# Context archive

Follow the [shared recording policy](../context/references/recording-policy.md) first. On a durable signal, resolve project settings once; only enabled owners participate automatically. User-approval instructions below describe `explicit` mode. In `auto` and `adaptive`, use policy authorization on the same validated write path; semantic attestations must remain truthful. Disabled features still allow explicit historical reads.

ARCHIVE is `context-archive/v1` evidence owned by context-core.

- Capture only an immutable source original that supports a durable context record. It is not a deliverable store or a substitute for OBS, DEC, INTENT, or DOCUMENT.
- Require substantive `Content`, at least one `source_ref`, and explicit attestations that the source was adopted as evidence and that the immutable original is present.
- After an explicit archive request settles the source and scope, use internal `archive preview --content @file ...`, verify no semantic delta, then apply the unchanged receipt and `approval_digest` in the same response without showing the rendered body.
- ARCHIVE supports capture, read, search, and discard only. It has no update, rename, retire, or supersede operation.
- Keep it out of default recall and pack. Use `archive read|search` or `recall --include-archive` only when the original is actually needed.
- An OBS `Evidence` item may be the exact ARCHIVE `ctx_` ID. Free-form evidence strings remain valid; context-core refresh checks exact IDs.
- Do not discard an archive with inbound internal references.

Follow context-core's active language contract for user-facing text and keep machine fields English. A direct, explicit, unconditional archive request that settles source, scope, and capture effect is semantic approval; ask only about unresolved meaning. A generic acknowledgement, condition, edit request, or topic change is not approval. Never show the rendered file body or ask a second storage question. Keep the receipt path and `approval_digest` private and pass them unchanged to apply in the same response. If internal preview exposes a semantic delta, hold the write and confirm only that delta. Never regenerate after approval.

See [context-protocol.md](../context/references/context-protocol.md).
