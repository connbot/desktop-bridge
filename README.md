# Desktop Bridge

A self-hosted external computer for your AI client. One MCP endpoint, one headed
Chromium desktop, a persistent workspace, and server-enforced human takeover.

**Status: runnable self-hosted Hackathon preview.** The same implementation passed
33 unit/security tests and three clean Docker end-to-end runs, including real
browser OAuth approval, desktop input, view-only enforcement, and restart
persistence. [Evidence and limits](docs/validation.md).

Single trusted owner only; this is not a production multi-tenant security boundary.
The model loop lives in your MCP client, not in this server.

## Run

Requires Docker Compose, Python 3, and roughly 3 GB RAM. Linux x86-64 is the first
validation target. macOS/Windows Docker Desktop support is not yet verified.

```sh
python3 scripts/setup.py
docker compose up --build -d
docker compose logs -f desktop
```

Open http://localhost:8080 and sign in using BRIDGE_OWNER_TOKEN from your local
.env. Never paste that owner token into a model conversation. The token is for
your viewer and OAuth approval, not direct MCP bearer authentication.

Connect a trusted MCP client to http://localhost:8080/mcp using Streamable HTTP
and OAuth (authorization code, S256 PKCE, dynamic client registration). Approval
shows the client and callback. Access grants expire after one hour; reconnect to
renew. Disconnect & revoke revokes all viewer sessions and OAuth grants.

For remote ChatGPT/Claude connectors, deploy on your own host behind HTTPS and
set BRIDGE_PUBLIC_URL to the exact public origin. Keep the container port bound
to loopback; proxy only the gateway port, including WebSockets. Existing client
accounts and supported connector access are required. Real ChatGPT/Claude UI
integration has not yet been verified, even when protocol tests pass.

## On-demand GitHub Actions preview

Use **Actions → Launch MCP preview** to run a bounded development/test desktop
behind a Cloudflare HTTPS tunnel. Quick Tunnel needs only your preconfigured
`BRIDGE_OWNER_TOKEN` repository secret; a named tunnel can use your own stable
hostname. The workflow prints the authenticated `/mcp` endpoint after public
OAuth/MCP/WebSocket checks. [Setup and ChatGPT connection guide (中文)](docs/actions-preview.zh-CN.md).

These runners are disposable: download files before stopping. Quick URLs change
on restart; named hostnames still require reauthorization after server restart.
This is not permanent hosting. The workflow never publishes the owner token or
uploads the browser profile/workspace as an artifact.

## Controls

- Observe: server-side read-only VNC stream, not only a browser viewOnly flag.
- Take control: immediately revoke new AI actions; human writes wait for an
  in-flight action to finish. The screen reconnects with input enabled.
- Private takeover: additionally block model screenshots, browser snapshots and
  tool calls. Managed shell processes are stopped on takeover. Detached/unmanaged processes
  cannot be guaranteed stopped: inspect the desktop before entering secrets.
- Hand back: revoke the human WebSocket and require a fresh model observation.
- Pause/Stop: block new AI actions. Stop is logical session control, not a Docker
  shutdown or a promise to undo already launched external work. Managed shell
  processes receive TERM/KILL when paused, stopped or taken over.

All GUI/browser writes carry fresh observation IDs and durable action receipts.
A lost-response action is never automatically replayed. A restart preserves
workspace/profile/receipts, revokes authentication and requires a fresh session.

## Reused components and credit

- Cua's pinned VNCAutomationHandler supplies desktop capture/pointer/key actions.
- Playwright connects to the same headed Chromium via loopback CDP.
- xyTom/coding-tools-mcp supplies file, shell, process and code editing tools over
  private stdio, all routed through the same control lease.
- noVNC provides the desktop viewer. x11vnc enforces the view-only channel.

This is an implementation of the external-computer architecture in the supplied
OpenDots proposal. It is not affiliated with diggerhq/opendots, OpenAI dots or Cua.
The new name avoids claiming the existing OpenDots name.

## Security limits

One trusted owner. The shell and browser can access container-local services;
this is **not** isolation against malicious agents, commands or multi-tenant
users. Chromium runs without its inner sandbox inside a non-root container.
Do not use personal high-value accounts or untrusted code. No Docker socket is
mounted; raw VNC/CDP are loopback only and not published. Network egress is not
restricted. A container shares its host kernel.

The persistent volume contains your browser profile and tool result receipts,
which can include sensitive file contents. Treat it as private data. The server
does not record screens or keystrokes in its activity list. It cannot infer
whether a click or shell command is a purchase: client approval policy and
least-privilege accounts are required.

GitHub Actions build/test repository code and can launch a bounded, manually
requested development preview. No automatic keep-alive/restart hosting loop is
provided. No external model credentials are needed for deterministic tests.

## Reproducibility

Python package versions are constrained in constraints.txt; GitHub Actions are
pinned to the resolved commit SHAs. Coding Tools is pinned to reviewed commit
[a2b8021](https://github.com/connbot/coding-tools-mcp/commit/a2b802171bee1f990effa55f955efa2eddde4c59),
including its non-blocking repeated-failure behavior and encoding fixes, rather
than a mutable branch. This is a reviewed pre-release commit, not a new published
package version. Debian system packages and the Python base tag still receive
upstream updates; a rebuild is not claimed to be byte-for-byte identical.

[中文上手与演示](docs/quickstart.zh-CN.md) · [Architecture](docs/architecture.md) ·
[Third-party credit](THIRD_PARTY_NOTICES.md)
