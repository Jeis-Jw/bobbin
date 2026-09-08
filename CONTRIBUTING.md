# Contributing to Bobbin

Thank you for helping improve Bobbin. Start with the user-facing [README](./README.md), then read [AGENTS.md](./AGENTS.md) before changing code or contracts.

## Development setup

The product uses TypeScript and Node.js 20.20.0+. Install development dependencies
with `npm ci`. Runtime dependencies are limited to Node built-ins. Python 3.13 is
optional and used only for the frozen compatibility oracle.

Work on a topic branch or linked worktree. Do not add a repository-owned `context/` or `wiki/` directory: this public component keeps product code, tests, protocols, and reproducible release evidence only.

## Before opening a pull request

Run the product gates and the optional reference comparisons:

```sh
npm run check
npm test
npm run test:compat
npm run test:package
python3 scripts/sync_distribution.py --check
python3 scripts/sync_guidance.py --check
git diff --check
```

The build regenerates the packaged `plugins/bobbin/dist/` output from `src/`.
Keep that output with plugin releases. Never edit or develop the frozen Python
reference as a parallel product. `npm run test:reference` runs its historical
regression suite separately; it does not validate TypeScript behavior.

When changing a public behavior or contract:

- update English and Korean user documentation together;
- keep canonical runtime instructions, schemas, identifiers, commands, and machine fields in English;
- preserve semantic approval, actual-body comparison, core-only physical writes, and bounded recall unless the change explicitly redesigns those contracts;
- add a record-created regression for retrieval behavior and retain the model-free scale and token-I/O checks;
- use `plugins/bobbin/.codex-plugin/plugin.json` as the single package/version source; run `scripts/sync_distribution.py` to regenerate the npm package/lock versions, runtime release metadata, Claude manifest, both catalogs and the profile;
- use `src/contracts.json`'s `policy` as the managed-guidance source; run `scripts/sync_guidance.py` after changes;
- update both host catalogs, both plugin manifests, profiles, fixtures, and distribution tests together when source, marketplace, protocol, or version surfaces change.

## Pull requests and commits

Keep each pull request scoped to one coherent outcome. Explain the user-visible behavior, compatibility or migration impact, tests run, and any evidence that remains unverified.

Commit subjects use a conventional prefix with a concise Korean summary of intent and result, for example:

```text
fix: 한국어 결정 검색의 조사 변형을 안정적으로 찾는다
```

Authorship, co-authorship, DCO sign-off, and cryptographic signing are separate claims. Add a `Co-authored-by` trailer only for another person or agent who materially authored the change. A request, approval, review, or accountability role alone is not co-authorship. Follow repository policy for any required sign-off or signing identity.

## Reporting security issues

Do not open a public issue for a suspected vulnerability. Follow [SECURITY.md](./SECURITY.md) instead.

By participating, you agree to follow the project [Code of Conduct](./CODE_OF_CONDUCT.md).
