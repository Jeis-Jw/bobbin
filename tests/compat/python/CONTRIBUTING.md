# Contributing to Bobbin

Thank you for helping improve Bobbin. Start with the user-facing [README](./README.md), then read [AGENTS.md](./AGENTS.md) before changing code or contracts.

## Development setup

The runtime supports Python 3.11 or newer and uses only the Python standard library. Tests require `pytest`.

```bash
python3.11 -m pip install "pytest>=8,<10"
python3.13 -m pip install "pytest>=8,<10"
```

Work on a topic branch or linked worktree. Do not add a repository-owned `context/` or `wiki/` directory: this public component keeps product code, tests, protocols, and reproducible release evidence only.

## Before opening a pull request

Run the complete suite on both supported interpreter lanes when they are available:

```bash
python3.11 -m pytest -q
python3.13 -m pytest -q
python3.11 -m compileall -q plugins tests
python3.13 -m compileall -q plugins tests
python3 scripts/sync_distribution.py --check
python3 scripts/sync_guidance.py --check
git diff --check
```

Keep production code standard-library-only. A focused test is useful while developing, but the complete suite is the release gate.

When changing a public behavior or contract:

- update English and Korean user documentation together;
- keep canonical runtime instructions, schemas, identifiers, commands, and machine fields in English;
- preserve semantic approval, actual-body comparison, core-only physical writes, and bounded recall unless the change explicitly redesigns those contracts;
- add a record-created regression for retrieval behavior and retain the model-free scale and token-I/O checks;
- use `plugins/bobbin/.codex-plugin/plugin.json` as the single package/version source; run `scripts/sync_distribution.py` to regenerate the Claude manifest, both catalogs and the profile;
- use core's `POLICY_BODY` as the managed-guidance source; run `scripts/sync_guidance.py` after changes;
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
