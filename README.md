# Agent Computer

**Muse-style computer use, inside ChatGPT.**

Want the computer-use side of [Muse](https://introducing.muse.ai/) or [dots](https://chatgpt.com/features/dots/) in your existing ChatGPT?

Agent Computer is an open-source project that connects ChatGPT to a self-hosted Linux desktop. It gives ChatGPT a browser, terminal, and persistent files for concrete work: research a topic, run a script, or build and check a small web page. Watch the work happen and download the result. ChatGPT drives the tasks; this project provides the computer.

File editing, shell commands, and code execution are powered by **[Coding Tools MCP](https://github.com/xyTom/coding-tools-mcp), also built by xyTom**. One MCP connection brings those tools and the browser into the same workspace.

**Self-hosted preview · One trusted owner · Apache-2.0**

[English quickstart](docs/quickstart.md) · [简体中文上手](docs/quickstart.zh-CN.md) · [Deployment](docs/deployment.md) · [Contributing](CONTRIBUTING.md) · [Roadmap](ROADMAP.md) · [Changelog](CHANGELOG.md)

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

**Check your client before deploying.** Computer use needs custom MCP **write**
tools. OpenAI currently lists full MCP write access for Business, Enterprise and
Edu; Pro access is read/fetch-only. Account/workspace permissions vary. See
[OpenAI's current requirements](https://help.openai.com/en/articles/12584461-developer-mode-and-mcp-apps-in-chatgpt)
(checked October 5, 2026).

### 1. Start locally

You need Git, Python 3.11+, a running [Docker installation with Compose](https://docs.docker.com/compose/install/),
and at least 3 GB available for the container plus host overhead. Linux x86-64 is
the validated target; macOS/Windows Docker Desktop and ARM are not certified.

```sh
git clone https://github.com/connbot/desktop-bridge.git
cd desktop-bridge
python3 scripts/setup.py
python3 scripts/doctor.py
docker compose up --build -d
python3 scripts/doctor.py --running
```

The first build downloads Chromium and system packages. If the last check runs
before startup finishes, wait and rerun it. Open **http://localhost:8080** on the
Docker host. Sign in with `BRIDGE_OWNER_TOKEN` from your local `.env` file. Keep
that token out of chats, source control and logs; it is for owner login and OAuth
approval, not a bearer token to paste into the MCP client.

### 2. Connect ChatGPT over HTTPS

ChatGPT cannot directly reach your laptop's `localhost`. Follow the
[HTTPS deployment walkthrough](docs/deployment.md) to set `BRIDGE_PUBLIC_URL`,
configure a reverse proxy and check your domain. Keep Docker's port 8080 on
loopback. Your direct MCP endpoint is `https://YOUR_HOST/mcp`.

Create a custom app named **Agent Computer**, select **OAuth** and, if asked,
**dynamic client registration**. Scan tools, sign in on your computer's domain,
review the callback and approve access. No static client secret is required.
Select the app in a regular ChatGPT chat and keep the viewer open beside it.

The [step-by-step quickstart](docs/quickstart.md) covers account setup, local vs.
remote addresses, authorization expiry and troubleshooting. No model API key is
needed for this direct connection. The server does not include a model subscription.

### 3. Get a result you can check

Start with a small task that needs no external account:

> Use Agent Computer to open http://127.0.0.1:8080/static/demo.html in its browser.
> Fill Project note with “Hello from Agent Computer”, click Save note, and verify
> the message on the page. Create hello-agent-computer.txt in the workspace with
> the same sentence and read it back. Do not sign in to any external account.

In the viewer, click **Refresh** under **Your files**, download
`hello-agent-computer.txt`, and open it. You should see the browser interaction,
the saved file and its verified contents. Then try the
[build-and-revise tutorial](docs/quickstart.md#next-build-a-small-tool-and-revise-it)
to create a budget calculator and check it in Chromium. The default `safe` command
policy may deny shell operations; see the tutorial before attempting that extra step.

## Stay in control

- **Watch:** the default viewer is server-enforced read-only.
- **Take control:** block new AI actions and take keyboard/mouse control after any in-flight action finishes.
- **Private takeover:** also block model observations and tool access. Inspect the session before entering secrets; unmanaged background processes may still be running.
- **Hand back to AI:** return control and require a fresh model observation.
- **Pause AI / Stop:** block new AI actions and attempt to terminate tracked managed shell processes. Stop controls the session; it does not shut down Docker or undo external actions.
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

The default Coding Tools command policy is `safe`; some shell operations require a deliberate owner policy choice. Do not switch modes just to bypass a denied command. See [command permissions](docs/deployment.md#command-permissions).

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

[Architecture](docs/architecture.md) · [English / 中文 quickstarts](docs/quickstart.md) · [HTTPS deployment](docs/deployment.md) · [Optional Fly deployment](docs/fly-deployment.md)

Need help or want to improve the project? Start with [contributing and support](CONTRIBUTING.md). Report security concerns using [the security guide](SECURITY.md); do not put tokens or private workspace data in public issues.

### Credits and license

- **[Coding Tools MCP by xyTom](https://github.com/xyTom/coding-tools-mcp):** files, shell, processes, and code editing through private stdio.
- **Cua:** desktop screenshots, pointer, and keyboard control.
- **Playwright:** structured browser actions in the same Chromium session.
- **noVNC and x11vnc:** the live desktop viewer and server-enforced view-only channel.

The project's source is [Apache-2.0](LICENSE). Dependencies retain their own licenses; see [third-party notices](THIRD_PARTY_NOTICES.md). This is an independent project with no OpenAI, Muse or Cua affiliation. The Muse/dots comparison describes the computer-use experience, not feature parity or endorsement.

Dependencies are pinned where practical, including the reviewed Coding Tools commit identified in `pyproject.toml`. Base images and Debian packages can change, so rebuilds are not claimed to be byte-for-byte identical.

## Share the project

[Chinese and English community launch copy, title options, and demo storyboard](docs/community-launch.txt).
