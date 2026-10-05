# Agent Computer

**Muse-style computer use, inside ChatGPT.**

Want the computer-use side of [Muse](https://introducing.muse.ai/) or [dots](https://chatgpt.com/features/dots/) in your existing ChatGPT?

Agent Computer is an open-source project that connects ChatGPT to a self-hosted Linux desktop. It gives ChatGPT a browser, terminal, and persistent files for concrete work: research a topic, run a script, or build and check a small web page. Watch the work happen and download the result. ChatGPT drives the tasks; this project provides the computer.

File editing, shell commands, and code execution are powered by **[Coding Tools MCP](https://github.com/xyTom/coding-tools-mcp), also built by xyTom**. One MCP connection brings those tools and the browser into the same workspace.

**Self-hosted preview · One trusted owner**

## What can you use it for?

- **Research with a saved result.** Visit official sources, compare options, and save a short report with links.
- **Work with files.** Read workspace files, transform sample data with a script, and save the output for download.
- **Build something small.** Write an HTML page or a personal tool, open it in Chromium, and check how it looks.
- **Improve the result.** Ask for a change, check the revised file in the same environment, and download it when it is ready.

These are task ideas, not success-rate benchmarks. Start with public information and sample files.

## How it works

```text
ChatGPT → OAuth + MCP → Agent Computer
                       ├─ Linux desktop + Chromium
                       ├─ Coding Tools MCP → files, terminal, code
                       └─ Web viewer → watch the desktop, download files
```

You run the computer. ChatGPT supplies the model, plans the task, and calls its tools. The server provides the desktop and execution environment; it has no background model loop or scheduler. When the client stops calling tools, the server does not continue reasoning on its own.

Browser automation and desktop control use the same visible Chromium session. Files live in `/data/workspace` on a persistent Docker volume. Other MCP clients can connect if they support Streamable HTTP and the required OAuth flow; account-specific compatibility still needs testing.

## Get started

### 1. Start the computer

Requires Docker Compose, Python 3, and roughly 3 GB of RAM. Linux x86-64 is the validated target; macOS/Windows Docker Desktop and ARM have not been verified.

The source currently lives at [connbot/desktop-bridge](https://github.com/connbot/desktop-bridge):

```sh
git clone https://github.com/connbot/desktop-bridge.git
cd desktop-bridge
python3 scripts/setup.py
docker compose up --build -d
docker compose logs -f desktop
```

Open **http://localhost:8080**. Sign in with `BRIDGE_OWNER_TOKEN` from your local `.env` file. The setup script creates that file and keeps it private.

Keep the token out of chats, source control, and logs. It is for owner login and OAuth approval, not a bearer token to paste into the MCP client.

### 2. Make it reachable from ChatGPT

The local `/mcp` endpoint is ready for clients that can reach your machine. For the remote ChatGPT connection described here, deploy behind HTTPS on a host you control:

1. Set `BRIDGE_PUBLIC_URL` in `.env` to the exact public origin, such as `https://computer.example.com`.
2. Restart the service with `docker compose up -d`.
3. Reverse-proxy the gateway at `127.0.0.1:8080`, including WebSockets. See the [Caddy example](docs/Caddyfile.example).

Keep the container port bound to loopback. Never expose raw VNC or CDP ports, or mount the Docker socket into the container.

Your MCP URL will be `https://YOUR_HOST/mcp`.

### 3. Add the MCP app in ChatGPT

You need **custom MCP apps with write actions**. OpenAI's guidance, checked October 5, 2026, lists full MCP write access for Business, Enterprise, and Edu; Pro is currently read/fetch only. Verify your account and workspace permissions against [the current instructions](https://help.openai.com/en/articles/12584461-developer-mode-and-mcp-apps-in-chatgpt) as the rollout changes.

1. Enable developer mode where available, then open **Apps → Create** in the appropriate user or workspace settings.
2. Name the app **Agent Computer**, enter your HTTPS `/mcp` URL, and select **OAuth**. If asked, use **dynamic client registration**; no static client ID or secret is needed.
3. Click **Scan Tools**. Complete the computer's owner login, review the client and callback address, and approve access. Finish creating the app after the scan.
4. Select the app in a ChatGPT chat, keep the desktop viewer open, and try the task below. Confirm actions when prompted.

This setup uses a custom app in chat. ChatGPT's separate agent mode does not currently use custom apps. See the linked OpenAI instructions for current support.

Access grants expire after one hour, with no refresh token in this version. Reauthorize when needed. A server restart also invalidates sessions and grants; if the client retains an old registration, recreate the connection.

### 4. Try a complete task

> Use Agent Computer's browser to find three free things to do in San Francisco this weekend. Check official sources for dates and opening hours. Save a short plan with source links as `weekend-options.md` in the workspace. Read the saved file back to check it, then tell me where to download it.

In the viewer, click **Refresh** under **Your files** to find the output. This exercises the browser, file tools, and a result you can inspect.

## Stay in control

- **Watch:** the default viewer is server-enforced read-only.
- **Take control:** block new AI actions and take keyboard/mouse control after any in-flight action finishes.
- **Private takeover:** also block model observations and tool access. Inspect the session before entering secrets; unmanaged background processes may still be running.
- **Hand back to AI:** return control and require a fresh model observation.
- **Pause AI / Stop:** block new AI actions and terminate tracked managed shell processes. Stop controls the session; it does not shut down Docker or undo external actions.
- **Disconnect & revoke:** revoke all owner viewer sessions and OAuth grants.

Writes use action receipts to avoid blindly replaying uncertain operations. Takeover cannot undo a submitted form, a completed purchase, or another external side effect. The connected client remains responsible for user approval.

## Keep your work

The Docker volume preserves workspace files, the browser profile, and action receipts across container restarts. A restart does not restore running processes or resume a model task.

```sh
docker compose stop    # Stop the computer
docker compose start   # Start it again
```

Download or back up files you want to keep. Avoid `docker compose down -v` unless you intend to delete the volume and its data.

For temporary development sessions, the repository also includes an [on-demand GitHub Actions preview](docs/actions-preview.zh-CN.md). It is disposable, bounded hosting: download results before it ends. Temporary tunnel URLs change between runs.

## Optional memory and tools

The desktop works without these features. Both are **off by default**:

- **[Personal context](docs/context-providers.md):** save selected preferences, goals, task progress, and result references in an explicitly configured local or Postgres-compatible store. The client must read and update them; a saved task does not start itself. Task status is author-reported, and exporting context does not export the linked files.
- **[External MCP tools](docs/optional-mcp.md):** expose an explicit allowlist of tools from owner-configured remote MCP servers. Accounts, credentials, service compatibility, and permission for consequential actions require separate setup. Included examples do not mean a live service is connected.

Configuration changes require a restart. No model API key is needed for the ChatGPT connection above. Optional third-party services may require their own credentials.

## Security and current limits

Use this with one trusted owner and low-sensitivity data. The browser, gateway, and shell share one container. This is not a security boundary against malicious code, a hostile agent, or independent tenants.

- Chromium runs without its inner sandbox inside a non-root container. The container shares the host kernel, and network egress is not restricted.
- Shell commands can reach container-local services. Environment scrubbing and private ports do not isolate valuable credentials from arbitrary code in that container.
- The persistent volume includes browser sessions and tool receipts, which may contain sensitive output. Protect it like other private data.
- Use test accounts and minimal permissions. Avoid high-value personal accounts and untrusted code. The server cannot decide whether an action is a purchase or whether the user has approved it.
- Website compatibility, client permissions, task success, and behavior on unverified host platforms vary.

## Verification and implementation

The [verification record](docs/validation.md) documents Linux x86-64 Docker tests for real browser/desktop control, OAuth, Coding Tools operations, takeover, view-only enforcement, and restart persistence. The owner also reported successful ChatGPT connection and live tool calls on October 4, 2026. These are integration checks and a specific client report, not an autonomous-task benchmark or a guarantee for every account.

[Architecture](docs/architecture.md) · [Chinese setup guide](docs/quickstart.zh-CN.md) · [Optional Fly deployment](docs/fly-deployment.md)

### Credits and license

- **[Coding Tools MCP by xyTom](https://github.com/xyTom/coding-tools-mcp):** files, shell, processes, and code editing through private stdio.
- **Cua:** desktop screenshots, pointer, and keyboard control.
- **Playwright:** structured browser actions in the same Chromium session.
- **noVNC and x11vnc:** the live desktop viewer and server-enforced view-only channel.

The project's source is [Apache-2.0](LICENSE). Dependencies retain their own licenses; see [third-party notices](THIRD_PARTY_NOTICES.md). This is an independent project with no OpenAI or Cua affiliation.

Dependencies are pinned where practical, including the reviewed Coding Tools commit identified in `pyproject.toml`. Base images and Debian packages can change, so rebuilds are not claimed to be byte-for-byte identical.

## Share the project

[Chinese and English community launch copy, title options, and demo storyboard](docs/community-launch.txt).
