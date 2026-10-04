# Verification record

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
