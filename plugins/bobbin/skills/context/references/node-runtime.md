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
