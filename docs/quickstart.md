# Your first task with Agent Computer

For a cloud computer without running your own server, see the optional [E2B quickstart](e2b-deployment.md). It needs a prebuilt, validated template; the local Docker instructions below remain unchanged.

[English](quickstart.md) · [简体中文](quickstart.zh-CN.md) · [Project overview](../README.md)

This guide gets you from a fresh checkout to a file you can download. Agent
Computer supplies a Linux desktop, browser and file tools. Your MCP client supplies
the model and decides what to do next; there is no autonomous model loop here.

## Before you start

- **A suitable host:** Linux x86-64 is the tested target. Allow 3 GB of memory for
  the container, additional room for the host, and disk space for a browser image
  plus your files. Docker Desktop on macOS/Windows and ARM hosts are not certified.
- **Installed tools:** Git, Python 3.11+ and [Docker with the Compose plugin](https://docs.docker.com/compose/install/).
  Start Docker before continuing. The commands below use a POSIX shell, such as
  Bash; Windows users need an equivalent shell and a working Linux-container setup.
- **A compatible AI client:** the local MCP endpoint requires Streamable HTTP and
  OAuth authorization-code flow with S256 PKCE and dynamic client registration.
  An arbitrary stdio-only client configuration will not work.
- **For ChatGPT:** check [current custom-MCP access requirements](https://help.openai.com/en/articles/12584461-developer-mode-and-mcp-apps-in-chatgpt)
  before paying for hosting. Computer use requires write tools, not only read/fetch.
- **For the remote connection below:** a host/domain you control with trusted HTTPS.
  Your local viewer working does not make your laptop reachable from ChatGPT.

No OpenAI model API key is needed for this direct ChatGPT connection. You still
need a suitable client account and must cover any hosting costs yourself. Optional
context storage and external MCP services are separate, disabled-by-default features.

## 1. Start and check the computer

Run on the machine that will host Agent Computer:

```sh
git clone https://github.com/connbot/desktop-bridge.git
cd desktop-bridge
python3 scripts/setup.py
python3 scripts/doctor.py
docker compose up --build -d
```

The directory, Python package and command still use `desktop-bridge`; the product
name is Agent Computer. First build downloads the browser and system packages, so
allow it to finish before checking readiness.

```sh
docker compose ps
python3 scripts/doctor.py --running
```

`doctor.py` is a read-only preflight. It does not install Docker, change your
configuration, create a connection, or prove that the public endpoint works.
For startup failures, inspect `docker compose logs --tail=100 desktop` locally.
Review and redact logs before sharing them.

**Checkpoint:** visit **http://localhost:8080** on the hosting machine. Open `.env`
in your own editor and copy the value of `BRIDGE_OWNER_TOKEN` into the viewer's
owner login. Never paste it into chat, a GitHub issue, or an MCP bearer-token field.
The setup script creates a private `.env` and preserves it on reruns.

The screen should show a Linux desktop with Chromium. The viewer starts read-only.
If you want to try the desktop yourself, click **Take control**, wait for any
in-flight action, then use the screen. Click **Hand back to AI** before the next step.

## 2. Give the remote client a reachable endpoint

There are three different addresses to keep straight:

| Address | Who uses it? |
| --- | --- |
| `http://localhost:8080` | You, in a browser on the Docker host, before HTTPS setup |
| `https://computer.example.com/mcp` | Your remote MCP client, after HTTPS setup |
| `http://127.0.0.1:8080/static/demo.html` | Chromium **inside** Agent Computer, for the sample task |

`localhost` always means the machine making the request. ChatGPT cannot directly
reach the loopback address on your laptop. Follow the [HTTPS deployment walkthrough](deployment.md)
for a domain, reverse proxy, origin configuration and checks. Keep Docker's 8080
port on loopback; publish only the HTTPS gateway.

For a short disposable test, the repository also has a [GitHub Actions preview](actions-preview.zh-CN.md).
Its files and browser sessions disappear when the run ends; it is not permanent hosting.
OpenAI also documents a [Secure MCP Tunnel](https://developers.openai.com/api/docs/guides/secure-mcp-tunnels)
for private servers. That route has separate permissions and OAuth reachability
requirements and has not been verified with this project. This guide uses direct HTTPS.

**Checkpoint:** the public viewer loads with a valid certificate, and the deployment
checks show your actual HTTPS origin in OAuth discovery. Once configured for HTTPS,
use that exact origin for the viewer too, rather than switching back to HTTP localhost.

## 3. Connect ChatGPT

Availability checked **October 5, 2026**: OpenAI lists full MCP write access for
Business, Enterprise and Edu on the web; Pro custom MCP access is read/fetch-only.
Workspace permission is also required. Follow the [plan-specific setup instructions](https://help.openai.com/en/articles/12584461-developer-mode-and-mcp-apps-in-chatgpt)
for the current UI. This project cannot enable those permissions for your account.

Create a custom app with these values:

| Field | Value |
| --- | --- |
| Name | `Agent Computer` |
| MCP endpoint | `https://YOUR_HOST/mcp` |
| Authentication | OAuth |
| Client registration, if asked | Dynamic client registration; no static client secret |

Enable developer mode where your workspace allows it, create the app, and scan
its tools. Complete the owner login on **your Agent Computer domain**, review the
client and callback address, then approve. Return to ChatGPT to finish creating
and selecting the app. Keep the viewer open beside your chat.

Use a regular chat with the custom app selected. OpenAI's separate agent mode
does not use custom apps. You may need to select or mention the app again for
follow-up actions. Confirm actions when the client asks.

**Checkpoint:** ask “Use Agent Computer to report the session state and take a
screenshot. Do not sign in to any website.” You should see an actual tool call and
the same desktop as the viewer. A successful tool scan alone is not this check.

The server issues one-hour access tokens with no refresh tokens. Reauthorize after
expiry. Restarting the server also clears registrations and grants. If reauthorization
reports an unregistered client, recreate the app connection to register again.

## 4. Make one small, verifiable result

Send this in a chat with Agent Computer selected:

> Use Agent Computer. Check the session state and start agent control if it is
> READY; if I have paused or taken control, ask me to hand it back. Open
> http://127.0.0.1:8080/static/demo.html in the computer's browser. Fill Project note
> with “Hello from Agent Computer”, click Save note, and verify the saved message
> on the page. Create hello-agent-computer.txt in the workspace containing the same
> sentence, then read the file back. Tell me the filename. Use only this local
> sample page and do not sign in to any external account.

**Success looks like:**

1. The shared Chromium shows the acceptance lab and `Saved: Hello from Agent Computer`.
2. ChatGPT reads back the actual saved file.
3. In **Your files**, click **Refresh**, download `hello-agent-computer.txt`, and open it.

The sample page's **Save note** button only updates that page. The separate file
creation is what produces the downloadable result. This small task checks browser
interaction, coding tools and download without relying on an external website.
A screenshot or a model saying “done” is not enough on its own.

### Next: build a small tool and revise it

This optional exercise starts a local server. The default `safe` command policy
may deny that command with `PERMISSION_REQUIRED`. If so, keep the generated file
and stop that step; do not have the model change permission modes or disguise the
command. An owner can separately review [command permissions](deployment.md#command-permissions).
The first task above does not need a shell server.

> Create a single-file HTML budget calculator at budget/index.html in the workspace.
> It should have Transport, Food and Tickets number inputs, initially 20, 40 and 30,
> with a live total. Start a managed local web server from the workspace using
> `python3 -m http.server 8765 --bind 127.0.0.1 --directory budget`. Open
> http://127.0.0.1:8765 in the computer's Chromium. Verify the total is 90, change
> Tickets to 50, and verify it becomes 110. Read back the saved file and tell me
> where to download it. Use sample data only and do not install packages.

Then ask it to add a Miscellaneous input starting at 10 and check that the total
becomes 120 with the other inputs set to 20, 40 and 50. Browser navigation accepts
HTTP(S), so a local web server is needed instead of a `file://` URL. The server
must remain a managed command; do not daemonize it or expose another public port.
Human takeover can stop that managed server. Restart it from the client if needed.

These prompts are examples, not a promised model success rate. See the
[verification record](validation.md) for the integration tests actually run.

## When something does not work

| Symptom | Check or next step |
| --- | --- |
| Docker command is missing or daemon unreachable | Install/start Docker and rerun `python3 scripts/doctor.py`. Do not weaken socket permissions to silence an error. |
| Build or startup fails | Check free RAM/disk and the local `docker compose logs --tail=100 desktop`. The host must reach the dependency registries during build. |
| Viewer loads but the screen is blank | Try **Reconnect screen**. Verify `/healthz` readiness and that your proxy forwards WebSockets. |
| OAuth returns to localhost or the wrong domain | Correct `BRIDGE_PUBLIC_URL` to the exact HTTPS origin, with no `/mcp`, and recreate the container with `docker compose up -d`. |
| HTTP viewer login or screen fails after HTTPS setup | Open the configured HTTPS origin; cookies and WebSocket origin checks use it. |
| ChatGPT has no custom-app creation or write tools | Recheck account/workspace eligibility and permissions in OpenAI's linked guide. A reachable server cannot override them. |
| `PERMISSION_REQUIRED` from a coding tool | The `safe` command policy may deny this operation. Stop the denied step, keep any existing result, and ask the owner to review the policy. Do not automatically retry in `trusted`/`dangerous` mode or disguise the command. |
| `UNAUTHORIZED` or `INVALID_GRANT` | Reauthorize. Codes are short-lived and single-use; access tokens expire after one hour. Never substitute the owner token as a bearer token. |
| `Unregistered client` after restart | Recreate the connection so the client registers again. |
| `CONTROL_NOT_OWNED` | Click **Hand back to AI** in the viewer. Only the owner can release a pause/takeover; `session_start` cannot. Idle agent sessions pause after about 30 minutes. |
| `STALE_OBSERVATION` | Take a new screenshot or browser snapshot before the next action. |
| `OUTCOME_UNKNOWN` | Inspect the actual page/file/external state before deciding what to do. Do not blindly resend the side effect with a new ID. |
| `AMBIGUOUS_TARGET` | Refresh the browser snapshot and use an exact, unique accessible role/name, or inspect a screenshot. |
| File is missing or download returns 413 | Verify it is under `/data/workspace`. Viewer downloads are limited to 20 MiB per file; copy larger files out with your host's Docker tools. |
| Preferences say storage is disabled | Expected by default. Enable a [context provider](context-providers.md) only if you need it. It is not required for the first task. |

For help, use the [support checklist](../CONTRIBUTING.md#getting-help).

## Stop, resume and keep your files

```sh
docker compose stop
docker compose start
```

The named volume preserves workspace files, browser profile and receipts. Running
processes and OAuth sessions do not survive a restart; a model task does not resume
itself. Download important results and back up the volume privately. Do not run
`docker compose down -v` unless you intend to delete that data. Read the
[security boundaries](../SECURITY.md) before connecting personal accounts.
