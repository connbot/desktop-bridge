# Deploy behind HTTPS

[Quickstart](quickstart.md) · [中文上手](quickstart.zh-CN.md) · [Optional Fly template](fly-deployment.md)

This is the direct-HTTPS route for one trusted owner. It assumes you already have
a Linux x86-64 host and domain. It does not provision a server, create an account
or manage billing. Read the [security boundaries](../SECURITY.md) first.

## The connection you are building

```text
Your browser ─── HTTPS ─┐
                       ├─ Caddy on your host → 127.0.0.1:8080 → Agent Computer
ChatGPT ─ OAuth + MCP ──┘                                      └─ private VNC/CDP
```

Only the gateway is public through HTTPS. Keep the Compose binding
`127.0.0.1:8080:8080`. Never expose ports 5900, 5901 or 9222, mount the Docker socket,
or publish a second MCP server from inside the container.

This walkthrough runs Caddy **on the same host as Docker**. If your proxy runs in
another container, its `127.0.0.1` is a different network namespace; this exact
upstream address will not work there. Do not fix that by publishing every internal
port. Use a deliberately configured private proxy network instead.

## 1. Prepare the host and domain

- Install Git, Python 3.11+, curl, Docker with Compose and [Caddy from its official source](https://caddyserver.com/docs/install).
- Point a domain you own, such as `computer.example.com`, to this host. Remove stale
  IPv6 records if the host does not serve that address.
- Arrange inbound access to the proxy's ports 80 and 443 and the outbound access
  needed for image builds, certificate issuance and the websites you use.
- Choose a backup plan and budget before running a paid host continuously.

See [Caddy's HTTPS prerequisites](https://caddyserver.com/docs/quick-starts/https)
for DNS and certificate requirements. Host firewall, router and DNS changes are
operator-specific; this guide does not silently change them.

Clone the repository on that host and run `python3 scripts/setup.py` as in the
[quickstart](quickstart.md#1-start-and-check-the-computer). If it is already running,
keep the existing checkout and `.env`.

## 2. Set one exact public origin

In your private `.env`, keep the generated owner token and change only this line:

```dotenv
BRIDGE_PUBLIC_URL=https://computer.example.com
```

Replace the example domain. Use the origin only: scheme and host, with a port only
if needed. Do not append `/mcp`, another path, credentials, a query or a fragment.
This origin is used in OAuth metadata, secure cookies and WebSocket origin checks.
Use a dedicated host name rather than a path prefix such as `/computer`.

```sh
python3 scripts/doctor.py
docker compose up --build -d
python3 scripts/doctor.py --running
```

`up -d` recreates the service when its configured environment changes. Merely
restarting an existing container does not load a changed Compose environment.
After switching to HTTPS, open the viewer at that HTTPS origin; HTTP localhost
is still useful for the unauthenticated readiness check, but not for normal login.

## 3. Configure the proxy

Merge this site block into your host's Caddy configuration, replacing the domain:

```caddyfile
computer.example.com {
    reverse_proxy 127.0.0.1:8080
}
```

The same block is in [Caddyfile.example](Caddyfile.example).
Caddy's [reverse proxy](https://caddyserver.com/docs/caddyfile/directives/reverse_proxy)
handles the viewer's WebSocket upgrade. Preserve the original host and paths.
Proxy the whole origin, including `/.well-known/`, `/register`, `/authorize`,
`/token`, `/mcp`, the viewer API, static files and `/desktop/` WebSockets.
Forwarding only `/mcp` leaves OAuth and the viewer broken.

For a standard Linux Caddy service using `/etc/caddy/Caddyfile`, review and validate
the edited configuration before reloading:

```sh
sudo caddy validate --config /etc/caddy/Caddyfile
sudo systemctl reload caddy
```

Use the equivalent service workflow for your installation. Do not overwrite an
existing configuration that serves other sites. Do not add an interactive login,
email-code wall or generic Basic Auth in front of the MCP/OAuth routes: remote
clients cannot complete an extra browser challenge on their server-side requests.
Agent Computer already requires its own OAuth grant.

## 4. Check from outside the host

Replace the example domain and run these without any owner token:

```sh
curl --fail --show-error https://computer.example.com/healthz
curl --fail --show-error https://computer.example.com/.well-known/oauth-authorization-server
curl --fail --show-error https://computer.example.com/.well-known/oauth-protected-resource/mcp
curl --silent --output /dev/null --write-out '%{http_code}\n' \
  https://computer.example.com/mcp
```

Expected results:

1. `/healthz` returns HTTP 200 with `{"ready":true}`. A 503 means it is not ready.
2. The authorization metadata's issuer and endpoints use your exact HTTPS origin.
3. The resource metadata identifies `https://computer.example.com/mcp`.
4. An unauthenticated `/mcp` request returns **401**, not tool access. Do not remove
   authentication to turn this into a 200.

Use a normal trusted certificate. Do not use `curl -k` or bypass browser certificate
warnings to make the checks pass. Open the HTTPS viewer in your browser, sign in
privately and confirm that the screen connects.

These checks establish reachability and configuration. They do not prove real
OAuth registration, tool execution, client permissions or task success. Finish
[the ChatGPT connection and first task](quickstart.md#3-connect-chatgpt) before
calling the deployment usable.

## Command permissions

Coding Tools starts in **`safe`** mode. Some shell commands, including a local
server or a readiness probe, can return `PERMISSION_REQUIRED`. Treat this as an
intentional policy boundary, not a reason for the client to keep trying variants.
Browser interaction and ordinary workspace file operations are a better first test.

Compose forwards `BRIDGE_CODING_PERMISSION_MODE` with `safe` as its default.
An owner who deliberately chooses a different policy can set it in the private
`.env` and run `docker compose up -d`. The supported values are `safe`, `trusted`
and `dangerous`; they are not interchangeable. In particular, **`dangerous` disables
Coding Tools filesystem confinement**. A client must not change this setting
without the owner's decision, and this guide does not require such a change.

Review [Coding Tools MCP's permission documentation](https://github.com/xyTom/coding-tools-mcp)
and the [shared-container security boundary](../SECURITY.md) before choosing.
Neither a tool permission mode nor the container is claimed to isolate hostile
code from the browser, gateway or valuable credentials.

## Operations and other routes

- **Restart:** workspace/profile/receipts persist in the named volume, but OAuth
  clients, grants and owner sessions are memory-only. Reauthorize; recreate the
  client connection if it holds an old registration.
- **Recovery:** stop compute and back up private data before risky maintenance.
  Never run `docker compose down -v` as a generic troubleshooting step.
- **Shutdown:** `docker compose stop` stops this computer. It does not stop a paid
  VM or remove provider storage charges.
- **Temporary preview:** [GitHub Actions + Cloudflare](actions-preview.zh-CN.md) is
  available for bounded development sessions. Download results before the run ends.
- **Fly Machines:** [the prepared Fly template](fly-deployment.md) includes an
  explicit volume/privilege-drop path. Its image tests are not a live Fly deployment.
- **Private network:** [OpenAI Secure MCP Tunnel](https://developers.openai.com/api/docs/guides/secure-mcp-tunnels)
  is an alternative transport documented by OpenAI, not yet validated here. It has
  separate tunnel permissions and a runtime credential; browser-facing OAuth still
  needs a reachable authorization server. It does not automatically tunnel this
  project's desktop viewer. Do not assume it is a drop-in replacement for these steps.

This guide is checked against the repository's configuration. A fresh public-host
Caddy deployment still needs the operator checks above; no domain or hosting
resource is created by following documentation in this repository.
