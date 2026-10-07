# Initializer review validation — 2026-10-06

## Scope and result

This review build was implemented from main commit
`ff47f53daa5c5c7802f632eea3c649afb498aa51` in an independent checkout. It was not
pushed, published, deployed or exercised against live user accounts. No GitHub
App, OAuth grant, repository, secret, Cloudflare resource or workflow run was
created during implementation.

### Passed on the final code

- `ruff check .`: passed.
- `python -m pytest -q`: **461 passed, 4 skipped**, 2 warnings. The four skips
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
  15 runner-report tests, 4 dispatch-reconciliation tests, 3 probe lifecycle tests
  and 3 stateless JSON
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

## CI follow-up after publication

The initial local limitations above describe the original review environment.
Subsequent GitHub CI for `1e6a0579487a90af1e068da47e06e54568647e26`
ran the real Chromium mock journey and produced nine screenshots in
[run 37542625665](https://github.com/connbot/desktop-bridge/actions/runs/37542625665).
This validates the browser path with fictional resources, not real provider OAuth
or a real ChatGPT account.

Review of those images found two mobile issues: the sticky mock banner could
cover the heading after a step change or reset, and an uncertain dispatch made
previously completed stages appear unstarted. The follow-up fixes use measured
banner clearance/reset scroll and retain progress from the recorded failure stage.
Browser assertions now cover heading geometry, viewport evidence, actual keyboard
Space/Tab and actual browser Back/Forward preserving the same server operation.
Wizard Back/Next is recorded separately from browser history. New screenshots
must be reviewed for the updated commit before calling these fixes visually passed.

A separate existing desktop acceptance run failed when Save was clicked before
the expected UTF-8 clipboard text appeared. The failure does not establish one
exclusive root cause. The Cua call returning only establishes that key events
were submitted; the next CDP click can move focus before the application consumes them.
The harness now types exactly once, then observes the full expected textarea value
with a five-second bound before clicking Save. It does not fill, retype or repaste,
and the original final text assertion remains. Permanently lost input still fails.
The backend and its input semantics were not changed.


A later, separate noVNC control probe failed in
[run 37544013225](https://github.com/connbot/desktop-bridge/actions/runs/37544013225)
with `VIEW_ONLY` still present where the single appended `z` was expected. This
was not the earlier clipboard assertion. The harness had disconnected the probe
after an arbitrary 300 ms and then read the application once. It now waits for the
main viewer's new control connection, submits `z` once, keeps the probe alive until
the exact application value is observed (up to 10 seconds), and closes in `finally`.
The read-only probe continuously checks the **entire unchanged value** during a
one-second observation window; it is not reduced to an absence-of-`z` check.
A failed probe records only disposable fixture values, focus/selection metadata,
connection event names and current screenshots, never credentials or raw headers.
There is no retyping, filling, key retry, backend modification or claim that the
underlying cause of all lost input has been established. Real CI remains the
acceptance boundary for these synchronization changes.
