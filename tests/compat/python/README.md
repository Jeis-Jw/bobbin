# Bobbin

Keep the thread.

[한국어](README.ko.md)

## What is it?

Bobbin keeps durable project context across AI coding sessions: decisions and their
reasons, verified observations, unfinished work, assumptions, terminology, intent
and living documents. Records are local Markdown files in `context/`; Git is
optional. No hosted service, database or API key is required.

## Why use it?

Keep the reasoning behind a choice, recall it when it matters, and continue work
without repeating the whole conversation. One plugin contains all features; each
project chooses which semantic features participate.

## Install

Bobbin 1.0.0 requires Python 3.11+ and Codex or Claude Code. The source repository
is [Jeis-Jw/bobbin](https://github.com/Jeis-Jw/bobbin).

```bash
# Codex
codex plugin marketplace add https://github.com/Jeis-Jw/bobbin.git
codex plugin add bobbin@bobbin

# Claude Code
claude plugin marketplace add https://github.com/Jeis-Jw/bobbin.git --scope user
claude plugin install bobbin@bobbin --scope user
```

Disable old `context-*` providers first; do not run both generations together.
Reload the host or start a new session after installation.
For local development, substitute the actual Bobbin checkout path for the Git URL.

## How to use it

Run `$bobbin:init` in your project and choose features and a recording mode.
Init configures the already-installed plugin; it does not install anything.

| Setting | Choices |
|---|---|
| Semantic features | Decision, Assumption, Term, Intent, Document |
| Always available | Observation, Snapshot, Archive |
| Recording mode | `explicit`, `auto`, `adaptive` |

Fresh setup defaults to Decision and `explicit`. Existing projects retain
explicit authorization and import their registered features. Re-running init
preserves omitted choices. Disabling a feature preserves its records and
explicit historical reads, but stops automatic participation and new writes.

- **explicit**: Your clear decision or request to remember is already approval.
  Bobbin asks only when the meaning, scope or lifecycle is unresolved.
- **auto**: Eligible durable context is recorded without per-record questions.
- **adaptive**: The LLM records directly or asks when confirmation matters,
  considering ambiguity, evidence, scope, existing conflicts and consequences.

Auto is not a transcript recorder. In every mode, a proposal remains a proposal;
the model cannot invent a user commitment or label its own preference as a DEC.
The recording mode does not authorize unrelated code changes or external actions.

Project settings live in `.bobbin/config.json`, separate from generated
`AGENTS.md`/`CLAUDE.md` guidance and the record index. Projects may share a vault
while retaining independent feature and approval settings.

### Try one decision across two sessions

With a fresh setup (Decision + `explicit`), try these messages in your project.

1. In the first session:

   > I decided this trial will support CSV only and leave Excel for later.
   > I want to validate the first-use flow quickly. Remember this decision.

2. Start a new session in the same project:

   > Why did we postpone Excel support?

3. Then explore a possible change:

   > What about including Excel in this trial too? I haven't decided to change the decision yet.

Look for a record confirmation, recall of the stored reason, then a comparison
with the existing decision. The last message asks for consideration and does
not authorize a replacement; proposals remain proposals in `auto`/`adaptive` too.

Use natural requests such as “Why did we choose this?”, “Keep this decision”,
or “Save where we stopped.” Bobbin uses relevant records without loading the
entire vault. Exact-payload validation and write integrity remain active in
every approval mode.

See [migration](MIGRATION.md), [contributing](CONTRIBUTING.md) and
[security](SECURITY.md). Licensed under the Apache License 2.0.
