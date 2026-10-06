# E2B: a cloud computer without your own server

This optional launcher starts Agent Computer in one E2B sandbox and prints a
public HTTPS desktop/OAuth origin and its `/mcp` endpoint. The desktop still uses
**[Coding Tools MCP by xyTom](https://github.com/xyTom/coding-tools-mcp)** for files,
shell and code. ChatGPT supplies the model loop; no model API key is required.

**Integration preview, not a deployed template.** This repository includes the
image target, cold-template builder, launcher and opt-in acceptance test. It does
not yet advertise a published, live-verified template ID. A maintainer must build
and validate a template first. Unit tests and an image build are not evidence of
successful E2B deployment or real ChatGPT account compatibility.

[中文说明](e2b-deployment.zh-CN.md)

## Cost and prerequisites

- Python 3.11+ on Linux/macOS (Windows: use WSL). Local validation used Linux;
  native Windows Python is unsupported. No local Docker, server or domain needed
  **once you have an approved, built template ID**.
- Your E2B account and API key, and that template ID. Keep the key in your local
  terminal environment. Never put it in ChatGPT, image build arguments, the
  desktop workspace, template environment or source control.
- E2B currently advertises a $100 one-time credit with no credit card required,
  not unlimited free hosting. Hobby has a $0 subscription and usage-based compute;
  the continuous sandbox limit is one hour. Check your actual credits/pricing
  before launch. The template requests 2 CPUs and 4 GiB RAM.
- Check [ChatGPT custom MCP write-tool availability](quickstart.md) before spending
  credits. A reachable HTTPS endpoint is not proof your account can use write tools.

[Current E2B billing](https://docs.e2b.dev/billing) ·
[Pause/resume](https://docs.e2b.dev/sandbox/persistence)

## Launch and connect

From a checkout, set up an isolated Python environment:

```sh
python3 -m venv .venv
. .venv/bin/activate
python -m pip install -r requirements-e2b.txt
# In your trusted terminal, securely set E2B_API_KEY. Do not paste it into chat.
python scripts/e2b_sandbox.py create --template YOUR_APPROVED_TEMPLATE_ID
```

`create` allocates one sandbox. It installs a scoped guest firewall, starts the
desktop only after both address families pass verification, waits for readiness,
and prints URLs. Review the security section before allowing those guest network
changes. It does not change your computer's firewall.

The owner login token is freshly generated after sandbox creation and saved to
`~/.local/state/agent-computer/e2b.json`, a mode-0600 private local file. Open that
file locally to copy `owner_token` into the **desktop's own login page**. Do not
paste it into ChatGPT or use it as an MCP bearer token. This file also holds the
sandbox ID needed to resume it. It does not contain the E2B account API key.

In ChatGPT, create the **Agent Computer** custom app using the printed `/mcp` URL,
OAuth, and dynamic registration if requested. Sign in on the printed desktop
origin and approve the client callback. Keep the viewer open. No static OAuth
client secret is needed; all discovery, registration, login, MCP and viewer
WebSockets use the same HTTPS origin.

The launcher can exit after startup. Neither a laptop tunnel nor a local proxy
needs to keep running. App authentication protects the desktop and MCP; the owner
should still treat the complete sandbox as one trusted single-owner computer.

## Pause, resume and deletion

```sh
python scripts/e2b_sandbox.py status
python scripts/e2b_sandbox.py pause
python scripts/e2b_sandbox.py resume
```

- `status` reads the provider status without waking a paused computer.
- `pause` and its alias `stop` preserve RAM and files. `resume` connects to the
  existing ID, checks the firewall and public origin, and restores readiness. It
  does not silently allocate a replacement or rotate the owner token.
- The default 3,600-second runtime deadline auto-pauses with memory preserved.
  This is a deadline, not idle detection. `--timeout 600` requests ten minutes.
  A running sandbox's `connect` only extends an existing deadline; it does not
  reliably shorten it.
- Automatic wake on HTTP traffic is disabled. Public probes must not spend your
  credits by waking a paused computer. Run `resume` explicitly.
- Memory pause retains `/data/workspace`, Chromium's `/data/profile`, receipts and
  in-memory sessions. Existing network/WebSocket connections disconnect; refresh
  the viewer and reconnect the MCP client after resume. OAuth grants may expire
  while paused; reauthorize when asked. A profile snapshot cannot guarantee a
  third-party website keeps you signed in.
- Do not request filesystem-only pause or `on_resume="reboot"`: those lose the
  running firewall/application state and this launcher does not support cold boot.
- E2B documents paused sandbox retention, but it is not a backup service. Export
  important files. Deleting/killing the sandbox permanently loses its local data;
  creating a new sandbox from the template does not restore it.

To permanently destroy the sandbox after exporting anything important:

```sh
python scripts/e2b_sandbox.py delete --confirm-id EXACT_SANDBOX_ID
```

A custom state file is supported with `--state /PRIVATE/PATH/e2b-state.json`
**before** the command. Keep the same path for every operation. Never run a second
`create` to fix a resume problem; each allocation may consume credits.

If creation fails, its ID is saved when known and the launcher attempts to pause
it, not erase data. Keep the private state file and inspect E2B status. If an API
timeout leaves only `phase: allocating`, the allocation outcome is unknown: check
the E2B dashboard before retrying. Do not post raw state, provider exceptions,
environments, cookies or browser profiles in an issue.

## Maintainer: build a reusable cold template

This step needs Docker only on the maintainer/build runner. Obtain approval for
the registry destination, E2B account, image/template publication and associated
costs before executing provider operations. The commands below are instructions,
not actions performed by setup. Do not bake personal data or keys into the image.

```sh
# Build the explicit E2B target, not Fly or the default local target.
docker build --target e2b -t YOUR_APPROVED_REGISTRY/agent-computer:e2b .
# Publish that image using your approved registry workflow, then copy its digest.
python scripts/build_e2b_template.py \
  --image YOUR_APPROVED_REGISTRY/agent-computer@sha256:EXACT_IMAGE_DIGEST \
  --alias YOUR_TEMPLATE_NAME
```

The builder uses pinned `e2b==2.52.1` and imports a completed Docker image, avoiding
E2B's multistage-Dockerfile translation limitation. It requests 2 CPUs / 4,096 MiB.
Only `sleep infinity` starts at template build time. The ready command hardens
E2B's post-build `/usr/local` permissions. It does not start Chromium, the gateway,
owner authentication, OAuth or user profiles. Each sandbox receives its own owner
token and exact `get_host(8080)` HTTPS origin only after creation.

[Base image import](https://docs.e2b.dev/template/base-image) ·
[Build-time start/ready semantics](https://docs.e2b.dev/template/start-ready-command)

## Why a guest firewall is required

**E2B can publicly proxy localhost-bound listeners.** Binding VNC/CDP to
`127.0.0.1` is not sufficient. This integration deliberately exposes port 8080
without E2B's proprietary traffic header so ordinary browsers and OAuth clients
can work; the application still requires its existing owner/OAuth authentication.

Before starting any desktop process, the protected root bootstrap creates a new
INPUT chain for both IPv4 and IPv6. It preserves loopback and established return
traffic, permits TCP 8080 and E2B's authenticated control port 49983, and drops all
other inbound TCP. It leaves existing provider chains, non-TCP traffic and OUTPUT
untouched. Any missing executable, unsupported rule or verification failure stops
startup. It never starts the Code Interpreter service on port 49999.

E2B's finalizer can make `/usr/local` writable and provision passwordless sudo.
The template ready command removes group/world write from `/usr/local`; bootstrap
uses protected `/opt` code and Debian `/usr/bin/python3 -I -S`, without importing
site packages. Every root SDK command sets a protected root HOME/PATH and disables
shell environment startup files before the SDK starts its login shell. It then runs the desktop as `bridge`, with supplementary groups
and capabilities cleared and `no_new_privs` inherited by all children. The model's
shell cannot use sudo/setuid to disable the ingress firewall. The provider account
key remains outside the sandbox. This is still not isolation between the model's
shell, browser and app owner secrets inside the same single-owner computer.

[Official firewall requirements](https://docs.e2b.dev/network/restrict-public-access#running-a-firewall-inside-the-sandbox)

## Verification before sharing a template

Offline checks:

```sh
python -m pip install -c constraints.txt -e '.[test,e2b]'
ruff check .
pytest -q
node --test tests/viewer_state.test.cjs
```

The E2B image workflow builds and inspects the cold image with no provider key,
registry push or firewall execution. Launcher unit tests mock provider calls;
SDK-builder tests exercise the installed SDK without contacting E2B.

After the owner configures a key and explicitly approves one real test sandbox,
its guest firewall changes and the usage budget, launch a **disposable** sandbox
and run:

```sh
python -m pip install 'websockets==17.2'
python scripts/e2b_acceptance.py --run-live --timeout 600
```

This live test uses the saved sandbox. It checks auxiliary ingress with a loopback
canary and firewall evidence, full-origin OAuth/PKCE, real MCP desktop/browser/shell,
authenticated viewer WebSockets, and file/profile/browser-state persistence across
memory pause/resume. It changes synthetic test state and leaves the sandbox
running; pause it afterward. Do not run it against your valuable personal session.

Alternatively, manually dispatch `e2b-live.yml` with an **existing approved
matching template ID**, enable its explicit disposable-test checkbox and provide
`E2B_API_KEY` through the protected `e2b-live-validation` GitHub environment.
That job never publishes an image/template. It creates one sandbox, sets a
10-minute auto-pause deadline, tests, then deletes only that job's sandbox even
on failure. Job timeout is 15 minutes. If the runner is killed before cleanup,
check E2B yourself; the provider deadline pauses rather than deletes state.
No E2B cloud job runs on push or pull request.

A live provider pass still needs a real ChatGPT OAuth connection and small user
task before claiming account-specific compatibility. Keep Docker/Fly deployment
as independent supported paths; this optional route does not change their defaults.
