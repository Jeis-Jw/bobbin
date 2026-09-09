---
name: decision
description: Compare Current DEC bodies on a choice signal; prepare only explicit choices.
---

Runtime: Node.js 20.20.0+. Use the `.mjs` entrypoint in this package; no Python or global plugin lookup.

# Decision

Follow the [shared recording policy](../context/references/recording-policy.md). Resolve project settings on a durable signal. Below, user approval means `explicit` mode; `auto|adaptive` use policy authorization without weakening semantic validity.

Use Bobbin's embedded core only; Core alone owns final validation and writes. Apart from the shared recording policy, do not read `references/`, plugin manifests, or `context/*.index.md`: `check` is the index read. Never run `--help` and never read or grep plugin scripts; use the complete commands below, apply an actionable error's correction before retrying, and inspect script source only after an unexplained interface failure. Never add an attestation unless the required actual comparison supports it. Use the active language for user text and English for machine fields; do not translate artifact prose.

## Recall and decide

1. Run only on core's choice signal: the user's own stated or changing choice. Carrying out a compatible request is not a choice; do not run `check` after finishing such a task. Reuse Current `{id,sha256}` only while the same scope, anchor, and actual body remain in context and no relevant mutation is known; do not add a check merely to prove reuse. When exact `--scope` and `--decision-key` are known, run one exact-slot `decision_cli.mjs check`; otherwise run one discovery `check` with `--statement` only. `coverage:discovery_only` cannot prove global absence; before `record`, check the exact slot once, reusing a still-applicable result under these conditions.
2. Reuse sections returned by `check` in the same turn under those conditions; do not call `read`, `spec-view`, or another context read unless required actual content is absent or a relevant mutation is known. Compare actual `Decision`, `Rationale`, `Rejected alternatives`, and non-empty `Revisit conditions`. Classify `new|same|supporting|rationale_changed|conflict`; similarity, hashes, IDs, and metadata are not evidence. For the same choice and governing scope, reuse the returned `scope` and `decision_key`; do not invent an alias.
3. Reuse `same` silently; for `supporting`, keep the DEC and consider durable new evidence as OBS. Before the primary conclusion, report `rationale_changed|conflict` by quoting every returned non-empty actual section: Decision, Rationale, Rejected alternatives, and Revisit conditions. State the selected revisit token verbatim as `satisfied|no evidence|ambiguous` without invention. `satisfied` needs present facts establishing the condition; the requested conflicting action is not evidence. `no evidence`: no facts or facts about another concern; `ambiguous`: relevant facts incomplete/conflicting.
4. Hold the affected action: make no code, file, or command change that performs or advances it until the user answers. Ask one explicit binary question offering both choices. Keep means the action is not performed; supersede permits it only after that explicit choice. A satisfied revisit authorizes reassessment, not implementation. The explicit choice settles that decision payload and authorizes its capture without a second storage question. `new` covers returned results only.
5. Claim only a caller-provided explicit choice governing action, with canonical scope and commitment evidence. Finish the request before one grouped mature proposal; re-propose dismissed/deferred candidates only with new evidence. If payload, scope, or lifecycle effect remains unresolved, ask only about that semantic delta.


`check` returns each selected Current record's actual `path` and complete comparison sections in `comparison_input.current`; `current_links` adds the same selected IDs with `state:"current"` and `supersedes` predecessor IDs. These fields navigate records; they do not establish semantic identity. Do not reread Current just to discover its predecessor. If history is needed and its actual body is absent from context, read the predecessor by stable ID. A supersede moves its path; use the latest returned `path` instead of a guessed filename or an old Current path. History returns `state:"history"`, `do_not_follow:true`, and its lifecycle reason; never follow it as the active choice.

```bash
node /loaded/bobbin/skills/decision/scripts/decision_cli.mjs read '<predecessor-id>' --json
```

Read only missing sections when the question needs them, for example `read '<id>' --section 'Rationale' --json`. Do not omit Decision, Rationale, Rejected alternatives, or non-empty Revisit conditions when comparing governing choices. Select only sections known to exist; omitting `--section` reads all actual sections. A byte-limited result with `truncated:true` is incomplete and cannot support a full comparison.

## Capture

Use the shared recording policy. Do not pre-run host inventory or core doctor. Run one `record --approved` for user approval, or `record --approval-source policy` for configured automation; adaptive also requires a record/ask assessment and reason. Internal preview and unchanged apply bind `approval_digest` privately; never expose or request transport details. The result confirms the write: do not re-run `check` afterwards.

Replace with `record --supersede <current-id> --attest-same-claim` only after comparing the predecessor and successor actual bodies, scope, and rationale and confirming that they address the same governing choice. The flag attests that comparison; approval, matching IDs, and slot metadata do not establish it. Retain the canonical scope and decision key. Retire with `record --withdraw <current-id> --reason <text>`, and discard a pending low-level receipt with `reject --receipt-file <retained-path>`. History is `do_not_follow`. Low-level orchestration: `decision_workflow.mjs preview` (frozen receipt; `preview stdout` carries `approval_digest`) then `apply --approved-digest`.

```bash
node /loaded/bobbin/skills/decision/scripts/decision_cli.mjs check \
  --statement '<forming or changing choice>' \
  --scope '<scope>' --decision-key '<key>' --json

node /loaded/bobbin/skills/decision/scripts/decision_workflow.mjs record \
  --host <codex|claude-code> --inline --approved \
  --title '<title>' --summary '<summary>' --scope '<scope>' \
  --decision-key '<key>' --commitment-evidence '<evidence>' \
  --sec-decision '<decision>' --sec-rationale '<rationale>' \
  --sec-alternatives '<rejected alternative>' --sec-revisit '<revisit condition>' \
  --attest-explicit-choice --attest-scope-identified --attest-commitment-present \
  --json
```

For an approved replacement, after the actual comparison above:

```bash
node /loaded/bobbin/skills/decision/scripts/decision_workflow.mjs record \
  --host <codex|claude-code> --inline --approved \
  --supersede '<current-id>' --attest-same-claim \
  --title '<title>' --summary '<summary>' --scope '<scope>' \
  --decision-key '<key>' --commitment-evidence '<evidence>' \
  --sec-decision '<decision>' --sec-rationale '<rationale>' \
  --sec-alternatives '<rejected alternative>' --sec-revisit '<revisit condition>' \
  --attest-explicit-choice --attest-scope-identified --attest-commitment-present \
  --json
```

Resolve `/loaded/...` from this file's own path in the skill catalog; the embedded core is found automatically (add `--core-cli <path>` only after a `core_cli_required` error). Limits: decision 1,200 codepoints, claim 2,000, input 8 KiB, envelope 16 KiB.
