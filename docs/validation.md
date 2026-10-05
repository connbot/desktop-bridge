# Verification record

## Latest verified runtime (before the Agent Computer relaunch)

October 4, 2026, commit [c3ce5ae](https://github.com/connbot/desktop-bridge/commit/c3ce5ae5b886d10b4d347d8e138a42d7cd4e7e8c):

- [288 tests with real TLS PostgreSQL transactions](https://github.com/connbot/desktop-bridge/actions/runs/37238470708) passed. The standard no-database job runs 284 and explicitly skips the four opt-in database tests.
- [Three independent Docker desktop/browser/OAuth/restart runs](https://github.com/connbot/desktop-bridge/actions/runs/37238470727) passed.
- [Prepared Fly-target image](https://github.com/connbot/desktop-bridge/actions/runs/37238470639) passed fresh root-owned volume startup, non-root app processes, bounded logging, restart persistence, and unsafe-symlink rejection. This is not an actual Fly account deployment.
- [Real branded capability capture](https://github.com/connbot/desktop-bridge/actions/runs/37238534974) passed; screenshots and encoded video frames were inspected. It is a rehearsed scripted sequence with sample data, not an autonomous-model benchmark.
- [Optional-memory UI acceptance](https://github.com/connbot/desktop-bridge/actions/runs/37236979629) passed at preceding runtime `29aa122`: disabled/default behavior, explicit local storage, profile/task forms, cancelled/repeated saves, export/import, fresh-client continuity, and restart. Later branding and MCP hardening were separately covered above.
- Generic optional MCP tests use real in-process protocol sessions with synthetic services. No AgentMail, Vapi, Neon account, external call/email, or sponsor credential was used. Authenticated vendor interoperability remains to be verified after owner setup.

Memory and MCP plugins are optional and require explicit configuration. Changes currently take effect after restart; this is not zero-downtime hot replacement. The client supplies the model loop; inbound email events need a separate event/runner integration. See [optional MCP](optional-mcp.md), [context providers](context-providers.md), and [Fly setup](fly-deployment.md).

## Earlier verification history

Date: 2026-10-04. Runtime implementation at
[22bf7e3](https://github.com/connbot/desktop-bridge/commit/22bf7e3a8fd9deda3e7d1e58383a2d46f4cba32c).

[Full GitHub Actions run](https://github.com/connbot/desktop-bridge/actions/runs/37214730745):
all four jobs passed: unit + three independent clean Docker builds/runs.
Artifacts contain real desktop and viewer screenshots, assertion summaries,
and container logs. Temporary CI artifacts expire after seven days.

## Passed

- 33 unit/security tests; Ruff; Python compilation; JavaScript syntax checks.
- Real Docker image builds and runs as non-root on hosted Ubuntu x86-64.
- Official MCP SDK initializes, discovers tools, and calls the live HTTP service.
- OAuth authorization-code/S256 PKCE, registration, audience/redirect binding,
  single-use codes, owner login, CSRF, revocation, and the actual browser approval
  journey with a real loopback callback listener.
- Real headed Chromium and Cua refer to the same desktop. Chinese/multiline
  clipboard input and accessible browser actions work together.
- Pixel actions enforce fresh observations, and retries use durable receipts.
- Coding Tools creates, reads, executes against, and exports a workspace file.
- Repeated failed reads do not trigger the old three-failure hard block.
- Human takeover blocks new AI writes; private takeover blocks AI observations.
- A managed asynchronous shell process is stopped during takeover.
- A maliciously enabled noVNC client cannot write through the server's view-only
  channel; the corresponding human-control channel accepts input after takeover.
- Viewer frames contain real desktop pixels, rather than merely an empty canvas.
- UI pause, takeover, resume, artifact refresh and reconnect paths execute.
- Container restart preserves files and successful action receipts.
- Logout/revocation rejects a previously valid MCP token.

## Findings addressed while testing

The first screenshot check accepted an empty canvas before the first frame. It
was replaced with pixel-level evidence. A later harness used a JavaScript string
predicate that conflicted with CSP; the test was fixed without allowing unsafe
script evaluation. Browser OAuth testing initially stubbed a redirect target;
we replaced that with a real callback HTTP listener. These failures are retained
in the public CI history, rather than represented as successful runs.

## Not claimed

- No autonomous model task benchmark or 20-task/three-repeat success-rate study.
  The three repetitions are deterministic integration suites.
- No live ChatGPT/Claude account connector or public HTTPS/tunnel onboarding has
  been certified. A reachable deployment and the user's account are required.
- No native macOS/Windows/ARM host certification, cross-browser mobile matrix,
  exhaustive GUI-action certification, penetration test, or multi-tenant isolation.
- No hard guarantee against hostile code, prompt injection, detached processes,
  already completed external effects, or arbitrary third-party websites.
- Container restart preserves disk, not process memory or an ongoing model loop.

The deliverable is a working self-hosted execution layer with reproducible
acceptance tests, not a managed hosted service or a model-subscription proxy.


## October 4 afternoon updates

At commit `6c29d2487a6017a35dd457743898751aefdf64a1`, 53 unit/security
tests and all three clean Docker integration jobs passed:
https://github.com/connbot/desktop-bridge/actions/runs/37226260176 .
The separate real Cloudflare HTTPS/OAuth/JSON-MCP/WebSocket smoke passed:
https://github.com/connbot/desktop-bridge/actions/runs/37226260097 .
The owner subsequently confirmed successful ChatGPT connection and live tool
calls. This is owner-reported client validation, not an autonomous task-success
benchmark. Personal task examples remain examples until their actual outputs
and execution evidence are demonstrated.
