# TypeScript transition compatibility

## Preserved storage and semantics

The eight supported record kinds remain SNAP, OBS, ARCHIVE, DEC, ASM, TERM,
INTENT and DOCUMENT. `context-common/v2`, all artifact schema IDs, existing
frontmatter, body text, scopes, Current/History positions, immutable decisions,
reciprocal successor links, typed relations and project approval modes remain.
Loading or initializing an existing vault does not migrate records. The stored
v1/v2 owner descriptors and their hashes remain unchanged. The TypeScript core
has no Bureau-specific scopes or data model.

Writes validate actual records, scoped slots, exact target and referenced bytes,
project/vault identity and unchanged runtime/configuration before applying. One
vault lock and a persisted undo journal cover records and derived indexes. A
failed write restores the previous bytes; an interrupted process requires explicit
dead-owner recovery. A final directory-flush failure after commit reports
`commit_sync_failed` with `applied:true`, retaining the completed record/index set;
it never starts an unjournaled rollback. This is a local-filesystem protocol, not
distributed storage.

## Deliberate transport changes

- Python `.py` plugin commands become `node ... .mjs`; the standalone CLI is
  `bobbin`. They call one in-process TypeScript implementation. External core
  checkout substitution and plugin-cache discovery are removed.
- The reusable API uses the `Operation` union and `bobbin-preview/v1`. The old
  `context-mutation-bundle/v1`, owner mutation-plan/validation-receipt transport,
  and old Python pending receipts are not executable Node inputs. Existing
  low-level orchestrators must produce a new preview through this API. Supported
  owner capture results remain `context-owner-result/v1`; caller-collected routing
  remains explicit and does not invoke agents.
- Transient receipts are `bobbin-receipt/v1`, private, outside the vault, with a
  24-hour lifetime. Apply/reject always require an explicitly retained filename.
  The unified read result carries `frontmatter`, `sections`, authority, lifecycle
  and truncation information; scripts must consume that documented shape rather
  than the former owner-specific `artifact` wrapper.
- Setup and immutable area registration are explicit core calls. `initialize`
  handles project features, guidance and optional Git merge attributes. When
  project and vault differ, initialization has two idempotent transactions under
  both locks: vault indexes first, project settings/guidance second. It is not a
  transaction across two filesystems. Recovery checks both roots; rerunning setup
  completes a phase interrupted before the project write. Ordinary record writes
  remain a single vault transaction.
- ASM supersession preserves the new candidate's supplied `tags`, `search_terms`
  and `source_refs`; the Python builder omitted those optional fields. Existing
  predecessor bytes and lifecycle rules are unchanged. This difference is checked
  explicitly in the stateful comparison.
- Unicode normalization/casefold follows the current Python comparison baseline,
  **Python 3.13 / Unicode 15.1**, independent of newer Node/Electron character
  additions. Stored text keeps its original Unicode spelling. Canonical JSON
  uses NFC and scalar-ordered keys; file SHA-256 binds exact rendered bytes.
  Historical Python releases with other Unicode data were not exhaustively tested.

## Exclusive Python → Node handover

1. Stop every Python writer to the vault, including host sessions and other
   processes. Preserve the ordinary vault backup. Do not migrate pending receipts.
2. Use the Node CLI with that vault. If it reports `legacy_runtime_conflict`, run
   `bobbin runtime adopt --vault DIR --confirm-legacy-stopped` after the writers
   have actually stopped. Confirmation is an operational assertion; the Node
   runtime cannot inspect a live Python process's `fcntl` lock.
   If project and vault differ, pass both `--project` and `--vault`; handover and
   recovery cover both lock namespaces.
3. Reload the plugin host with the new `.mjs` entrypoints. Existing config is
   preserved; reinitialize only for explicitly requested setup changes.
4. If a Node writer is killed, `bobbin runtime recover --vault DIR` verifies that
   its local PID is dead, rolls back the journal and permits further calls. A live
   process is never evicted because of age. PID reuse or a missing/malformed owner
   record requires operator inspection; there is no unsafe timeout takeover.

Node callers coordinate through `VAULT/.bobbin-runtime/writer`, even with different
TMPDIR values. At the old Python lock path in the current temporary directory,
Node installs a directory guard: the unchanged Python `os.open(O_RDWR)` then fails.
This guard cannot stop an already open Python lock descriptor, a different user's
or host's temporary directory, or Python started after the temporary guard was
removed/rebooted. **Mixed Python/Node writes are unsupported.** Do not treat the
handshake as interoperable `fcntl` locking.

Rollback requires stopping all Node writers, resolving any journal, then removing
only the matching temporary directory guard after inspection before returning to
Python. Preserve artifact/config bytes. Never remove a live lock or restore a
backup over an active writer. The frozen development oracle is a comparison
fixture, not a supported second product runtime.

## Reproducible validation

- `npm test`: every kind, immutable/retirement rules, selective reads, owner
  routing, project/vault isolation, target/reference conflicts, concurrent
  processes (including distinct TMPDIR values), injected filesystem failure,
  SIGKILL recovery and safety boundaries.
- `npm run test:compat`: fixed-input Python/TS JSON and SHA-256 comparison,
  captures and legacy headings, exact index bytes, record updates and decision
  replacement; all 1,112,064 Unicode scalars compared for NFC and NFKC/casefold,
  plus combining-character sequences and Korean/English discovery terms.
- `npm run test:package`: make an actual tarball, install it offline into an
  independent directory, type-check its declarations, use CommonJS/ESM imports,
  run library ↔ CLI with PATH empty and exercise packaged plugin wrappers.
  `BOBBIN_TEST_NODE` and `BOBBIN_TEST_ELECTRON` optionally select absolute runtime
  binaries. `BOBBIN_TEST_ASAR` selects an installed `@electron/asar` module to
  exercise a temporary ASAR app; native app execution needs a desktop session.
- `tests/compat/python/REFERENCE.json` records the frozen pre-port source and test
  hashes, including pre-existing working changes. Its 436-test baseline was run
  before porting. These are Python reference tests, not claimed TS coverage.

Locally exercised: macOS arm64, Node 20.20.0 and 24.11.1, Electron 44.2.0 with
embedded Node 24.20.0, and an ASAR consumer. CLI and library run without Python,
Git, plugin installation or a development checkout dependency.

Not verified: Windows/Linux execution, distributed/network filesystems, actual
power loss/device failure, and live Codex/Claude model behavior using the installed
plugin. Process-kill and injected error tests establish the stated local recovery
behavior, not hardware durability on every filesystem. Bureau's full application
integration, platform packaging and user flows remain separate work. No remote
push, npm publication or marketplace submission is part of these checks.
