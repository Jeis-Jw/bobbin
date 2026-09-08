---
name: snapshot
description: Save, update, load, or discard a named SNAP handoff for unfinished work when requested or permitted by project recording policy; load it when a session resumes earlier work.
---

# Snapshot

Follow the [shared recording policy](../context/references/recording-policy.md) first. On a durable signal, resolve project settings once; only enabled owners participate automatically. User-approval instructions below describe `explicit` mode. In `auto` and `adaptive`, use policy authorization on the same validated write path; semantic attestations must remain truthful. Disabled features still allow explicit historical reads.

SNAP is mutable resume context with `authority: staging`; it is not authoritative decision or evidence history. Apart from the shared recording policy, do not read `references/`, plugin manifests, `context/*.index.md`, or plugin scripts, and never run `--help`; the commands below are complete. Use the active language for user text and English for machine fields; preserve user-authored artifact prose.

1. Resume: when the user asks to continue earlier work without restating it, run one `snapshot list`, then `snapshot load --id <id>` for the matching SNAP, and continue from its Next steps and Open items under its stated constraints. Do not ask the user to restate what the SNAP already says.
2. Save only with unfinished context and handoff intent authorized by the user or configured recording policy. `save` is create-only and fills Current context, Open items, and Next steps; state the exact next step and any constraint the user gave.
3. `update` is full replacement unless deliberate `--merge`; `load.freshness` is only a warning. `discard` targets one SNAP. SNAP has no archive, history, or retired state.

A direct, explicit, unconditional handoff request that settles the snapshot content and scope is semantic approval. If meaning is unresolved, ask one concise semantic question; acknowledgement, praise, a condition, an edit request, or a topic change is not approval. Do not show the rendered file body or ask a second storage question. Then run one `save --approved` (or `update --approved`) in the same response: it runs the internal preview, freezes the receipt, and applies it unchanged; keep every transport detail private and never regenerate after approval. If rendering adds or changes meaning, it stops: hold the write and confirm only that semantic delta. The command output is the confirmation; do not re-read the file afterwards. `approval_digest` stays internal either way. Low-level orchestration: `save` without `--approved` writes nothing and returns a frozen receipt; pass `result.receipt_file` and `result.approval_digest` unchanged to `transaction apply --receipt-file ... --approved-digest ...`. Receipt self-digests are damage checks, not approval evidence, and no directory scan is allowed.

Resolve `/loaded/...` from this file's own path in the skill catalog. Open items and next steps take one item per line (a `- ` bullet is optional), each at most 240 characters; the context is one paragraph.

```bash
python3 /loaded/bobbin/skills/context/scripts/context_cli.py snapshot list --json
python3 /loaded/bobbin/skills/context/scripts/context_cli.py snapshot load --id '<id>' --json
python3 /loaded/bobbin/skills/context/scripts/context_cli.py snapshot save --approved \
  --title '<title>' --summary '<summary>' --captured-from conversation \
  --attest-handoff-requested --attest-unfinished-context-present \
  --sec-context '<current context>' --sec-open-items '<open item>' --sec-next-steps '<next step>' --json
```
