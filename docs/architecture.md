# Architecture and scope

Agent Computer provides a computer that
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

An initial READY state allows session_start. The owner opens one interactive
shared desktop, without taking or handing back control. Watching and protocol
traffic do not change ownership. Actual input immediately invalidates previous
observations and queued tool writes, drains the current action, then uses the
same asynchronous lock. Held keys and mouse buttons block new agent actions and
observations until released. The agent must observe again before its next GUI
action; no timer replays a stale click. Ordinary input does not kill builds or
other managed commands.

The shared viewer's pixel stream stays on read-only port 5901. A bounded RFB
client parser routes only supported key, pointer and basic clipboard input to
an auxiliary connection on port 5900. A raw framebuffer round trip on that
connection acknowledges input processing before the lock is released. It does
not promise that every application has finished reacting. Unknown protocol
messages fail closed; extended clipboard paste and display resize are not
supported in this route. Agent Unicode typing is unchanged. Reconnection
releases held input, and an uncertain release blocks AI until an explicit resume
successfully retries only the release, never the original action.

Explicit Pause, Private and Stop retain the existing safety behavior: revoke
immediately, drain in-flight work and attempt to stop tracked managed commands.
Only the owner can resume them. Legacy human/control and read-only endpoints
remain compatible, but are not the default UI workflow. Durable action receipts
still prevent blind replay. Background shell programs can themselves affect the
display outside the gateway; shared input is cooperative scheduling, not process
isolation, and cannot undo external effects or terminate every daemonized task.

Private mode blocks model observations too. The owner viewer remains visible.
Untrusted browser text cannot authorize actions; the model/client still must
apply its own approval policies. Shell access makes this a trusted-owner
execution environment, not an adversarial multi-tenant security boundary.

## Reconnect and restart

The viewer keeps the same shared stream through control changes and reconnects
automatically after a transport disconnect. Authentication is still required. A durable
SQLite receipt marks in-flight requests as unknown after a crash, preventing
blind replay. File and profile persistence use one Docker volume. OAuth grants
and owner sessions are memory-only and are invalid after restart. Refresh-capable
clients receive rotating, hashed refresh tokens with a 30-day absolute grant limit
and 7-day refresh inactivity limit. Access tokens remain one hour, clipped to the
grant deadline. Rotation, family-wide replay revocation and token issuance are
atomic under a process-local lock; run one gateway process. Consumed refresh
hashes remain until the family expires/revokes so replay is still detectable after
many rotations. Active grants and stored refresh hashes are bounded; exhausted
capacity fails closed rather than evicting replay history. Container restart is
not a memory snapshot and does not resume a model task.

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
