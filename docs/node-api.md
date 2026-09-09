# Node.js core and CLI

Bobbin 2.1.0 provides a reusable TypeScript core. One `@bobbin/context`
package contains the core, compiled CLI, type declarations and agent entrypoints.
It needs Node.js **20.20.0 or newer**. It has no runtime dependencies, native addons,
Python, Electron, Git, plugin-installation or network requirements.

## Install a local package

```sh
# In the Bobbin checkout; development dependencies are needed only here.
npm ci
npm test
npm run test:compat           # optional development comparison: Python 3.13
npm pack --pack-destination /path/to/packages

# In an independent consumer. This installs the tarball, not a checkout symlink.
npm install /path/to/packages/bobbin-context-2.1.0.tgz
npx --no-install bobbin init --vault /path/to/existing/vault --features decision,intent,document
npx --no-install bobbin recall --vault /path/to/existing/vault --query 'storage'
```

A global CLI install from the same tarball is also supported:
`npm install --global /path/to/packages/bobbin-context-2.1.0.tgz`.
This document does not imply a package has been published to npm.

The plugin checkout ships compiled `plugins/bobbin/dist/` so loading the plugin
requires Node, but does not require npm, TypeScript or Python. Its `.mjs` scripts
import that same compiled CLI. They resolve paths relative to their own package;
`/loaded/...` in skill examples means the actual loaded skill location.

## Library

Both CommonJS `require('@bobbin/context')` and ESM named imports are supported.
A library caller explicitly supplies `vault` and optionally `project`; the core
never changes `process.cwd()` or environment variables. Directories must exist.
`project` selects `.bobbin/config.json`; `vault` selects the record filesystem.
With only `project`, its configured vault is resolved relative to that directory.
Use writable local directories owned by the calling OS user. Reads also take the
vault lock so they never observe an interrupted record/index transaction. The
protocol coordinates processes of that user; it is not a multi-user access service.

```ts
import { createBobbin, createCandidate, createAttestation } from '@bobbin/context';

const bobbin = createBobbin({ vault: '/path/to/vault', project: '/path/to/project' });
// Run setup only when the user chooses or changes project configuration.
await bobbin.initialize({ features: ['decision'], approvalMode: 'explicit' });

const candidate = createCandidate({
  kind: 'snapshot',
  title: 'Continue the integration',
  summary: 'The package works; application wiring remains.',
  ownerInputs: {
    current_context: 'The user asked to preserve this unfinished handoff.',
    open_items: ['Connect the client approval screen'],
    next_steps: ['Load this snapshot before continuing'],
  },
});
// The caller must assess meaning. These assertions do not infer user approval.
const attestation = createAttestation(candidate, [
  { name: 'handoff_requested', value: true,
    evidence_pointers: ['/owner_inputs/snapshot/current_context'] },
  { name: 'unfinished_context_present', value: true,
    evidence_pointers: ['/owner_inputs/snapshot/open_items/0'] },
]);
const preview = await bobbin.preview({ action: 'capture', candidate, attestation });
// Keep this exact preview until the semantic payload is settled.
await bobbin.apply(preview, { source: 'user' });
if (preview.operation.action === 'capture') {
  const record = await bobbin.read(preview.operation.id!);
  console.log(record.sections['Next steps']);
}
```

[The runnable consumer](../examples/consumer.cjs) also proves library → CLI and
CLI → library interoperability with an empty executable search path.

| API | Contract |
| --- | --- |
| `initialize(options)` | Explicit project setup; omitted features/mode preserved; no artifact migration |
| `settings()` | Selected project/vault, configuration digest, enabled features and approval mode |
| `read(id, {sections?, maxBytes?})` | Actual stored body, frontmatter, authority, history warning; no truncation unless a byte limit is supplied |
| `inspect(id)` | Original content, path and byte SHA-256, without rewriting it |
| `recall(options)` | Index-first discovery; 4 KiB metadata / 8 KiB expanded defaults; selected sections only; fallback scans at most 20 files |
| `search(kind, options)` | Owner metadata search; ASM/TERM require their explicit signal |
| `checkDecision(options)` | Bounded exact-slot or discovery input containing actual DEC bodies, without a semantic verdict |
| `specView(scope)` / `revisitDecisions(options)` | Current decisions and review conditions; no mutation |
| `preview(operation)` / `validate(input)` | Validated frozen operation / validation without artifact writes |
| `apply(preview, authorization)` | Runtime/project/vault binding, reference and target CAS, lock, journal and atomic writes |
| `refresh(fix?)` | Diagnose record/index integrity; `true` rebuilds derived indexes without rewriting artifacts |
| `route(batch, results)` | One audited batch, caller-collected semantic claims/declines; no agent invocation |
| `previewOwnerResult(result)` | Validate a supported owner's capture result through the same core |
| `registerArea(descriptor, indexSeed)` | Explicit immutable v1/v2 owner registration; preserved structural profiles |
| `adoptLegacy(true)` / `recoverRuntime()` | Exclusive Python handover / prove a local writer is dead before recovery |

`Operation` is a discriminated TypeScript union. `capture`, `supersede`, `update`,
`annotate`, `retire`, `reverify`, `rename`, `discard` and a bounded `batch` all use
one writer. `OwnerInputs`, `ReadResult`, `ApplyResult`, `Preview` and
`BobbinError` are exported with declarations. `schema`/`capabilities` provide
record field bounds and semantic assertion names at runtime.

SNAP create and update share a 256 KiB (262,144-byte) limit on the compact JSON
UTF-8 representation of the complete logical input: title, summary, captured source,
references, tags, search terms, anchors and rendered sections. Partial updates check
the merged result. Content has no separate character or list-item limits; metadata
bounds remain. Markdown is preserved, with CRLF normalized to LF. CLI save/update
accept `--sec-context @/path/to/context.md` or `--sec-context -` for raw stdin;
multiline list items use JSON arrays inline or from `@/path/to/items.json`.
`snapshot_input_too_large` reports `actual_bytes`, `max_bytes` and
`measurement: snapshot_payload_utf8` without changing existing records or indexes.
No automatic shortening or splitting occurs. Explicit load/read returns full content unless a read limit is supplied;
search/recall budgets and other record kinds' limits are unchanged. Existing SNAPs
need no migration; older runtimes may not read newly framed or larger SNAPs reliably.

`supersede` requires a new capture ID and a caller-supplied same-claim attestation
bound to `prepareSameClaim(id, successor)`. The current record is retained in
History; reciprocal edges, scope and DEC/INTENT slot keys are checked. Changed
DEC reasoning is never treated as metadata annotation. Ordinary evidence remains
OBS; only `kind_hint: decision` fallback OBS supports cross-kind import to DEC.

A batch is atomic, contains 1–8 operations and cannot change one path twice.
References may target another capture in the same batch. Sequential changes to
one record require sequential previews. Record kinds are never used as client,
agent or task scopes; the caller supplies any project-specific scope convention.

## Authorization and errors

Explicit mode requires `{source:'user'}` after semantic approval. Auto mode permits
`{source:'policy'}`. Adaptive mode additionally requires
`{source:'policy', decision:'record', reason:'...'}`. `decision:'ask'` refuses the
write. Claims and attestations remain required in every mode; authorization is a
trusted caller assertion, not an authentication service or an LLM judgment.

A stale target, referenced record, registered descriptor, runtime or project
configuration rejects apply. Derived index drift alone can be rebuilt under the
same write lock. Retrying a fully applied, unchanged preview returns
`already_applied:true`; a partially different state fails closed.

CLI stdout contains one JSON result: `{"ok":true,"result":...}` or
`{"ok":false,"error":{"code","message","details"}}`. Errors do not print stack
traces into the JSON stream. `help`/`--help` are the explicit human-readable
exception. Exit codes are 0 success, 2 usage/schema, 3 missing input, 5 conflict or
approval, 6 integrity, 1 unexpected runtime failure. Consumers use `error.code`,
not message text. CLI transport changes are listed in [compatibility](compatibility.md).

Library errors are `BobbinError` with `code`, `details`, `exitCode` and `envelope()`.
Filesystem errors use `runtime_error` with the original Node error code in
`details.system_code`. `commit_sync_failed` reports `details.applied:true` when
all record/index writes completed but the final directory flush failed; inspect
the result before retrying. `rollback_sync_failed` means prior bytes were restored
but their final flush failed. Neither result establishes hardware durability.
Errors do not log or terminate the host process. Low-level input may be JSON text,
`@filename` or `-` for JSON stdin; `@@` escapes a leading `@` in body arguments.
Receipt apply requires the exact retained filename and `approval_digest`; no cache
or directory scan chooses a pending approval.

## Electron / Bureau

Install the tarball as a production dependency and bundle its installed package.
Use the library from Electron's main process, utility process or a Node worker.
A worker is preferable for large vaults because file operations and validation are
synchronous while waiting for locks is asynchronous. Renderer access should go
through the application's own IPC boundary. Store the writable vault outside the
read-only application ASAR. No `asarUnpack` or native-addon rebuild is needed for
Bobbin itself; an ASAR consumer has been exercised with Electron 44.2.0.

Bureau's remaining work is to select project/vault locations, map its settled
semantic payloads into these inputs, preserve previews until approval, handle
conflict/re-preview results, implement its own IPC/UI, and include the package in
its release build. A record's type does not define its applicability to a Bureau
agent or task. This port does not modify Bureau or choose those product semantics.
