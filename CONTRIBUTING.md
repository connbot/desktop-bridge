# Contributing and getting help

Agent Computer is a self-hosted, single-owner preview. Useful contributions make
setup easier, fix reproducible failures, or strengthen the existing control and
authorization boundaries. For suspected vulnerabilities, use [SECURITY.md](SECURITY.md).

## Getting help

First try the [English quickstart](docs/quickstart.md) or
[中文上手](docs/quickstart.zh-CN.md), including its troubleshooting checklist.
Search [existing issues](https://github.com/connbot/desktop-bridge/issues) before
opening a new one. English and Chinese reports are welcome.

A useful report includes:

- Commit SHA (`git rev-parse HEAD`), host OS/CPU architecture and deployment route.
- Docker/Compose versions and the relevant sanitized `python3 scripts/doctor.py`
  output; add `--running` when diagnosing a running desktop.
- The exact step that failed, expected result, actual error and minimal sample data.
- Whether it fails locally, over public HTTPS, or only in the MCP client. If it is
  client-specific, give the client and account/workspace permission category.
- A small redacted log excerpt or screenshot, rather than a full private session.

Never post `.env`, owner tokens, OAuth codes/tokens, cookies, database URLs,
private browser profiles, or personal task files. `docker compose config` without
`--quiet` can print interpolated secrets; do not paste its output into an issue.
Review even diagnostic output before sharing. There is no promised support SLA.

## Local development

Use Python 3.11+ and Node.js 18+; CI uses Python 3.12. Node is used for the
viewer regression tests. From a fresh checkout:

```sh
python3 -m venv .venv
. .venv/bin/activate
python -m pip install -c constraints.txt -e '.[test]'
ruff check .
pytest -q
node --test tests/viewer_state.test.cjs
```

These checks cover Python logic, synthetic backends and viewer state regressions.
They do not start a real desktop. PostgreSQL integration tests are opt-in and may be skipped when a test
DSN is absent; see [context provider testing](docs/context-providers.md).

Real-browser/desktop changes also need the disposable Docker acceptance workflow
in [ci.yml](.github/workflows/ci.yml). Follow its build, start, client-dependency,
`scripts/e2e.py` and restart-check steps as a complete sequence. It uses fixed
**test-only credentials** and modifies a disposable workspace; never point it at
your personal deployment. The PostgreSQL, HTTPS-preview and Fly-image workflows
cover their own integration boundaries. See [verification](docs/validation.md)
for what each kind of pass establishes.

## Keep changes reviewable

- Describe the user-visible problem and include a focused regression test for a fix.
- Keep `BRIDGE_*`, `desktop-bridge`, existing tool names and request fields stable
  unless a change explicitly calls for a migration. Product branding is separate.
- Preserve owner-only control transitions, OAuth/CSRF checks, private takeover,
  fresh observations and action receipts. Do not solve failures by disabling them.
- Keep fixtures fictional and credentials test-only. Do not include `.env`, generated
  artifacts, caches or private workspace data in a patch.
- Update both quickstarts when changing a shared setup step. Label untested host,
  client and provider combinations rather than claiming general compatibility.
- Report the commands actually run and their results, including skips and blockers.
  Unit tests, Docker integration, a reachable URL and a real-client task are
  different checks.

For large protocol, deployment or isolation changes, describe the proposed scope
in an issue first. Keep new dependencies justified and update
[third-party notices](THIRD_PARTY_NOTICES.md) when necessary. Project source is
licensed under [Apache-2.0](LICENSE); dependencies keep their own licenses.
