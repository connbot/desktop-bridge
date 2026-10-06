# Initializer review validation — 2026-10-06

## Scope and result

This review build was implemented from main commit
`ff47f53daa5c5c7802f632eea3c649afb498aa51` in an independent checkout. It was not
pushed, published, deployed or exercised against live user accounts. No GitHub
App, OAuth grant, repository, secret, Cloudflare resource or workflow run was
created during implementation.

### Passed on the final code

- `ruff check .`: passed.
- `python -m pytest -q`: **458 passed, 4 skipped**, 2 warnings. The four skips
  are the existing opt-in PostgreSQL integration tests, with no test DSN supplied.
  Warnings concern Starlette/httpx deprecation and an existing Pydantic forward
  reference; neither was hidden or converted to a pass assertion.
- `node --test tests/viewer_state.test.cjs`: **6 passed**.
- `python tests/initializer_dom.py`: **18-check offline integration passed**,
  using the shipped HTML/JavaScript, real loopback HTTP service, Node Web Crypto
  adapter, bundled libsodium and actual Python SealedBox decryption. Covers
  download confirmation, retained password on back navigation, regeneration,
  duplicate clicks, owner isolation in the API tests, refresh, cancel, restart,
  no-zone fallback, unknown dispatch and explicit safe retry. This is not a
  visual browser or a test of real download-manager behavior.
- The Python suite includes **75 independent initializer security/race tests**,
  15 runner-report tests, 4 dispatch-reconciliation tests and 3 stateless JSON
  MCP transport tests. The transport tests include the real pinned MCP SDK:
  initialize, initialized notification (202), authorized GET (405), tools list
  and call (JSON), with no session ID or hanging SSE stream. Unauthorized GET
  remains 401.
- `git diff --check`, Python compile checks and both edited workflow YAML parses:
  passed.
- Rebuilt vendored browser crypto and verified identical SHA-256:
  `93e2c2048935b6f0ba656ad34a8aa1ca07419e5bfd6d624b2a1a8239b07b501c`.
- Live mode without operator credentials was explicitly checked: GitHub OAuth
  start returns `not_configured`; mock-only endpoints do not exist.
- Editable package build/install succeeded after including the independent
  initializer package and its static assets/licenses.

### Important regressions fixed during review

- Concurrent named-mode create clicks producing multiple operations/resources.
- Late readiness callbacks reviving a stopped/ending computer.
- Missing ending/connection-status terminal reconciliation and stale readiness.
- Oversized/chunked JSON handling; allocating locks for untrusted operation IDs.
- Cross-session OAuth state, expiry and per-owner resource checks.
- Named tunnel/DNS binding changes before restart and credential-bearing smoke.
- Reusing an old run during unknown-dispatch reconciliation. Every launch now has
  a persisted unique nonce in workflow dispatch, run name and ready/ending event.
- Expired session reauthorization, separate Cloudflare reauthorization, stale
  progress text and disabled/copy-success state leaking across new runs.
- The underlying stateless JSON server accidentally accepting a GET SSE stream
  that Quick Tunnel cannot support.

### Not passed / not run

- **Real Chromium/browser rendering, screenshots, keyboard and browser history:**
  blocked. The execution environment denied the Unix socket/IPC needed to launch
  Chromium. One reviewed environment escalation produced the same failure; no
  restriction was bypassed and no public preview tunnel was opened. The runnable
  Playwright test remains in the source for a supported development machine.
- **Live GitHub/Cloudflare OAuth, selected-installation template creation,
  repository secrets, Actions dispatch/OIDC callbacks, pinned public DNS/TLS
  probes, domain/tunnel creation/cleanup and real ChatGPT connection:** not run.
  Mock/API contract tests do not establish these live combinations.
- **New Docker desktop end-to-end run and long public tunnel/WebSocket sessions:**
  not run in this change. Existing unit/protocol tests are not a replacement.
- **Cross-run persistent DCR registration:** not implemented. Fixed-hostname
  users are explicitly told that a fresh computer may still need a new ChatGPT
  connection and authorization.
- **Automatic cleanup, password rotation and every partial-write recovery:** not
  implemented. Resources remain visible; uncertain writes stop for reconciliation.
  Stop cancels the exact run and waits for terminal status; it does not erase
  Cloudflare/GitHub resources or independently prove remote disk destruction.

## Release gates

See [the full operator checklist](initializer.md#operator-configuration-and-release-gates).
The source repository was read-only verified as **not a template**. A reviewed
published template version containing this code, actual App/OAuth credentials,
HTTPS service hosting, optional Cloudflare public-client verification, abuse
controls, and separately authorized real-account/real-client validation are
required before a public launch. Source delivery is not authorization to perform
any of those actions.
