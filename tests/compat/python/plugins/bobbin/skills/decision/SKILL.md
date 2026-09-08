---
name: decision
description: Compare Current DEC bodies on a choice signal; prepare only explicit choices.
---

# Decision

Follow the [shared recording policy](../context/references/recording-policy.md). Resolve project settings on a durable signal. Below, user approval means `explicit` mode; `auto|adaptive` use policy authorization without weakening semantic validity.

Use Bobbin's embedded core only; Core alone owns final validation and writes. Apart from the shared recording policy, do not read `references/`, plugin manifests, or `context/*.index.md`: `check` is the index read. Never run `--help` and never read or grep plugin scripts; the commands below are complete, a usage error is explained (rerun the command as written), and inspect script source only after an unexplained interface failure. Use the active language for user text and English for machine fields; do not translate artifact prose.

## Recall and decide

1. Run only on core's choice signal: the user's own stated or changing choice. Carrying out a compatible request is not a choice; do not run `check` after finishing such a task. Reuse Current `{id,sha256}` only while its scope, anchor, and actual body remain in context. When exact `--scope` and `--decision-key` are known, run one exact-slot `decision_cli.py check`; otherwise run one discovery `check` with `--statement` only. `coverage:discovery_only` cannot prove global absence; before `record`, check the exact slot once.
2. Reuse sections returned by `check` in the same turn; do not call `read`, `spec-view`, or another context read unless a section is absent or the body changed. Compare actual `Decision`, `Rationale`, `Rejected alternatives`, and non-empty `Revisit conditions`. Classify `new|same|supporting|rationale_changed|conflict`; similarity, hashes, IDs, and metadata are not evidence. For the same choice and governing scope, reuse the returned `scope` and `decision_key`; do not invent an alias.
3. Reuse `same` silently; for `supporting`, keep the DEC and consider durable new evidence as OBS. Before the primary conclusion, report `rationale_changed|conflict` by quoting every returned non-empty actual section: Decision, Rationale, Rejected alternatives, and Revisit conditions. State the selected revisit token verbatim as `satisfied|no evidence|ambiguous` without invention. `satisfied` needs present facts establishing the condition; the requested conflicting action is not evidence. `no evidence`: no facts or facts about another concern; `ambiguous`: relevant facts incomplete/conflicting.
4. Hold the affected action: make no code, file, or command change that performs or advances it until the user answers. Ask one explicit binary question offering both choices. Keep means the action is not performed; supersede permits it only after that explicit choice. A satisfied revisit authorizes reassessment, not implementation. The explicit choice settles that decision payload and authorizes its capture without a second storage question. `new` covers returned results only.
5. Claim only a caller-provided explicit choice governing action, with canonical scope and commitment evidence. Finish the request before one grouped mature proposal; re-propose dismissed/deferred candidates only with new evidence. If payload, scope, or lifecycle effect remains unresolved, ask only about that semantic delta.


## Capture

Use the shared recording policy. Do not pre-run host inventory or core doctor. Run one `record --approved` for user approval, or `record --approval-source policy` for configured automation; adaptive also requires a record/ask assessment and reason. Internal preview and unchanged apply bind `approval_digest` privately; never expose or request transport details. The result confirms the write: do not re-run `check` afterwards.

Replace with `record --supersede <current-id>`, retire with `record --withdraw <current-id> --reason <text>`, and discard a pending low-level receipt with locator-free `reject --core-cli ...`. History is `do_not_follow`. Low-level orchestration: `decision_workflow.py preview` (frozen receipt; `preview stdout` carries `approval_digest`) then `apply --approved-digest`.

```bash
python3 /loaded/bobbin/skills/decision/scripts/decision_cli.py check \
  --statement '<forming or changing choice>' \
  --scope '<scope>' --decision-key '<key>' --json

python3 /loaded/bobbin/skills/decision/scripts/decision_workflow.py record \
  --host <codex|claude-code> --inline --approved \
  --title '<title>' --summary '<summary>' --scope '<scope>' \
  --decision-key '<key>' --commitment-evidence '<evidence>' \
  --sec-decision '<decision>' --sec-rationale '<rationale>' \
  --sec-alternatives '<rejected alternative>' --sec-revisit '<revisit condition>' \
  --attest-explicit-choice --attest-scope-identified --attest-commitment-present \
  --json
```

Resolve `/loaded/...` from this file's own path in the skill catalog; the embedded core is found automatically (add `--core-cli <path>` only after a `core_cli_required` error). Limits: decision 1,200 codepoints, claim 2,000, input 8 KiB, envelope 16 KiB.
