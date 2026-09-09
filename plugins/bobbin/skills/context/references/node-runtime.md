# Packaged Node runtime contract

Node.js 20.20.0+ runs all `.mjs` CLI/workflow/init adapters. Each imports the same
`plugins/bobbin/dist/cli.js` and reusable core. No Python subprocess, global
installation, cache search, external core substitution or Electron dependency is
part of the product. `--core-cli`, when present, must identify this package's own
core entrypoint. The exact loaded runtime is bound into every preview.

The supported high-level commands in the skills use unchanged semantic rules and
artifact schemas. `record --approved` and builtin `save --approved` privately freeze
and apply the same preview in one process. Low-level preview returns
`result.receipt_file`, `result.approval_digest` and `result.approval_preview`.
Apply forwards the exact retained path and digest. Reject also requires that path;
no receipt discovery or automatic selection is supported.

The in-memory wire schema is `bobbin-preview/v1`; the private disk wrapper is
`bobbin-receipt/v1`. The former Python mutation bundle, owner mutation-plan/validation
receipt, workflow receipt and runtime handshake are not executable Node inputs.
Low-level callers use the public `Operation` union and `preview` / `apply`, or
`previewOwnerResult` for supported capture results. Never translate an already
approved old receipt; reassess and produce a new preview after the handover.

`read` returns `id`, `kind`, `path`, `state`, `frontmatter`, actual `sections`,
`authority`, `do_not_follow`, lifecycle information, warnings and `truncated`.
History is never current authority. `read` has no implicit truncation;
`--max-bytes` requests a bounded result. `recall` keeps its own bounded metadata
and selective-section budgets. Specialized ASM/TERM search/read retain their
explicit signal requirements. `search` metadata results are normalized by the
shared API; callers must not depend on the former owner-specific envelope.

Success/error is one JSON object on stdout. Exit codes are 0 success, 2 input,
3 missing, 5 conflict/approval, 6 integrity, 1 unexpected runtime error. `--help`
is the explicit human-text exception. Core alone changes record files. Configured
explicit/auto/adaptive modes and semantic attestations are independent gates.

Stop all old Python writers before `runtime adopt --confirm-legacy-stopped`.
The local Node lock is inside the vault; old temporary fcntl guard locations are
blocked only in the current shared temp namespace. Mixed runtimes, network
filesystems and multi-host writers are unsupported. `runtime recover` proves a
local writer PID is dead before restoring an interrupted journal. It never steals
a paused live process's lock.

## Scoped decision comparison

Prefer `search('decision', {query, scope})` when coordinates are unknown, then
`compareDecision({statement, scope, decisionKey, rationale?, query?, limit?, knownCurrent?})`.
CLI: `decision search --query <term> --scope <scope>` then
`decision compare --statement <choice> --scope <scope> --decision-key <key>`.
Skip search when coordinates are known, and reuse a still-valid comparison instead of adding a call.
Search is metadata-only; an empty result is not proof of absence and must not automatically widen the search.

`DecisionCompareOptions` requires scope and decisionKey. Compare selects only exact/ancestor/descendant scopes.
Same-key exact and overlapping slots are mandatory; other bodies need distinctive metadata matches
within that scoped corpus. Scope alone never admits an optional body. Limits remain 8 records by default,
12 maximum, 24 KiB hydrated comparison and 32 KiB response; mandatory overflow fails rather than truncating.

The single `context-decision-compare/v1` envelope has `coverage:exact_slot`, `comparison`
(schema `context-decision-comparison-delta/v1`, proposal and current), `deterministic` mandatory IDs,
`current_links`, `retrieval`, `warnings`, `assessment_contract_ref`, `hydrated_input_digest`,
`transport_digest` and `physical_write:false`. `retrieval.total_current` is vault-wide;
`scoped_current`, `outside_scope`, `omitted` and `full_scoped_set` distinguish scoped coverage from global absence.
`body_reads` counts actual selected file reads, including optional bodies excluded by the byte budget.
The assessment contract is available in `decision schema` as `comparison_contract`; load it once if the skill
is not already available. Never infer semantic identity or approval from metadata, references or digests.

`knownCurrent` uses the existing at-most-12 ID/full-file-SHA hints. Only held complete actual sections may be
referenced. Changed/new records return bodies. Omit hints after partial reads, context loss or handoff.
Hydrate references and change the comparison schema to `context-decision-comparison-input/v1` before
checking `hydrated_input_digest`; `transport_digest` hashes the literal `comparison`. Legacy `checkDecision`
and `decision check` retain their selection policy and full/delta schemas through the shared implementation.
