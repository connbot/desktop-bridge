# Agent Workspace

A self-hosted workspace for a general personal AI. Work on everyday tasks and
keep the files your AI makes. Optional memory adds reusable preferences, goals,
and task continuity when you configure a provider. One MCP endpoint
connects your existing AI client to a headed Chromium desktop and your own
[Coding Tools MCP](https://github.com/xyTom/coding-tools-mcp) by xyTom for file,
editing, shell, and execution tools.

**Status: runnable self-hosted Hackathon preview.** The same implementation passed
53 unit/security tests and three clean Docker end-to-end runs, including real
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
accounts and supported connector access are required. The owner confirmed successful ChatGPT connection and live tool calls on
October 4, 2026. Claude account onboarding and broad autonomous task success
are not verified by our protocol tests.

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

## Optional personal context and task continuity

Memory is **off by default**. Configure a Postgres/Neon-compatible provider, or
explicitly choose the credential-free local backend for a demo or owner use.
The desktop and your own Coding Tools MCP work without it.
[Provider setup, security boundary, and verification](docs/context-providers.md).

When enabled, open **My preferences** to save the preferences, goals, and constraints you choose
to share. **My tasks & results** records the next step, progress, evidence, and
links to generated workspace files. It starts empty: no real personal data or
invented user profile is bundled.

The connected model reads `personal_context` before a task, uses the desktop,
browser, and **Coding Tools MCP** to do the work, then explicitly writes progress
with `personal_record_task`. `personal_update_context` lets it save information
you asked it to remember. These three small tools are a reusable context layer
for many different tasks, not a new model loop, scheduler, or account integration.
Copy an example or a saved task prompt into your connected AI chat to start.

Task status is **author-reported**, not independently verified completion.
A completed record needs evidence or a file; linked files are checked for actual
existence in the workspace. A plan is not a booking and a draft is not a sent
message. The client must still observe external outcomes and obtain appropriate
approval. Personal context is data, never authorization or trusted instructions.

Context uses a small pluggable provider: disabled, an explicitly chosen local
file, or optional async Postgres. Postgres stores bounded revision-checked JSON
in the configured database; it never silently falls back to local files.
Provider changes require a restart and do not automatically move data.
Concurrent edits reject stale revisions rather than silently overwriting them.
All model writes use the same agent-control lease and durable receipts as other
tools. Private takeover blocks model reads as well as writes; the owner can still
edit and download their context through authenticated, CSRF-protected controls.
Do not put passwords, access tokens, or payment credentials in personal context.

**Download context** exports the reusable profile and task records. **Import**
restores an export after explicitly confirming replacement. Artifact files are
separate: copy/download them too. Imported references without a file in the new
workspace are visibly marked unavailable. GitHub Actions runners remain
**disposable**: download context and results before the preview stops. Nothing is
automatically uploaded to GitHub artifacts or carried to another runner.

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

## Optional services and hosting

Add owner-selected external tools with the [optional MCP registry](docs/optional-mcp.md).
Services stay disabled until configured; provider/config changes require restart.
[Prepare Fly deployment](docs/fly-deployment.md) without an account or credentials.
The adapter/template is implemented; real vendor accounts and a Fly deployment
still need separate setup and verification. Your existing MCP client owns the model loop.
