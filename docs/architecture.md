# Architecture and scope

Agent Workspace implements **Mode A** of the OpenDots proposal: a computer that
an existing MCP client can operate. The client owns model reasoning and approvals.
No model keys, subscription credential forwarding, scheduler, or hosted agent
loop are included. This avoids pretending an MCP server thinks autonomously.

## Selected foundation

We inspected AIO Sandbox and Cua. AIO bundles multiple overlapping shell/file/web
surfaces; the narrower Cua VNC handler lets this implementation reuse Coding
Tools for files and processes while applying one control lease at the gateway.
AIO was not benchmarked or run in this implementation. Cua was actually installed,
and its real desktop operations are exercised in the Docker acceptance test.

The container runs Xvfb + Openbox + one headed Chromium. Two x11vnc listeners
serve the same X display: 5900 permits input; 5901 is server-enforced view-only.
Both listen on loopback and are only reached through authenticated WebSockets.
Cua connects internally to the control port. A small X11 adapter converts scroll
into RFB wheel buttons because the pinned upstream VNC backend uses arrow keys
for a macOS VNC compatibility case. The shared desktop and Unicode input are tested end-to-end; every possible
GUI action and third-party application is not exhaustively certified.
Chinese text uses the local UTF-8 X clipboard, followed by a Cua paste hotkey.

Playwright connects to that exact Chromium via loopback CDP. Coding Tools runs
on private stdio, with a scrubbed environment and telemetry disabled. It sees
/data/workspace. These are not three independently public MCP servers.

## Ownership

An initial READY state allows session_start. Human pause, stop, and takeover
cannot be overridden by session_start. Only the owner viewer can hand back.
Every tool write holds the same asynchronous lock, rechecks ownership after
queuing, and records an action receipt. Takeover revokes the lease immediately,
then drains any in-flight operation and terminates tracked managed commands.
No new human write connection is accepted until that drain completes. This
cannot undo external side effects or guarantee termination of daemonized work.

Private mode blocks model observations too. The owner viewer remains visible.
Untrusted browser text cannot authorize actions; the model/client still must
apply its own approval policies. Shell access makes this a trusted-owner
execution environment, not an adversarial multi-tenant security boundary.

## Reconnect and restart

The viewer reconnects to the appropriate stream after control changes. A durable
SQLite receipt marks in-flight requests as unknown after a crash, preventing
blind replay. File and profile persistence use one Docker volume. OAuth grants
and owner sessions are memory-only and are invalid after restart. Container
restart is not a memory snapshot and does not resume a model task.

## Acceptance evidence

The official MCP Python client tests OAuth, initialization, tools/list and calls
against the actual Docker service. It drives real Chromium, types Chinese via
Cua, creates/reads/executes/exports a file through Coding Tools, checks fresh
observations and idempotency, tests takeover/revocation, renders the real noVNC
viewer, and verifies persistence after a container restart.

These are deterministic integration tests, not autonomous task success-rate
benchmarks. Passing them does not prove compatibility with every ChatGPT or
Claude account, public tunnel, mobile device, or arbitrary website. Those
external-client onboarding steps require a reachable HTTPS deployment.
