# Prepared Fly Machines deployment

This directory contains an offline deployment template, not a deployed service.
No Fly account, token, app, volume, network resource or payment is created by the
configuration generator. A real Fly deployment, including its image startup and
volume permissions, must be tested after the owner configures access and approves
costs. The model loop remains in the existing MCP client.

## Generate configuration without an account

From the project root:

```sh
python scripts/configure_fly.py --app YOUR-LOWERCASE-APP-NAME --region sjc
```

Use an actual lowercase app name instead of the uppercase placeholder. The script
validates syntax, renders `fly.example.toml`, parses the result and creates
`fly.toml` without overwriting an existing file. It makes no network requests.
App-name availability and actual region support are not checked offline.

Review before deployment:

- One intended Machine, 2 shared CPUs, 4 GB memory, one initial 10 GB volume
- `/data` contains workspace/profile/receipts; it must be mounted persistently
- Only the gateway's port 8080 is proxied through HTTPS; no raw VNC/CDP ingress
- Public origin and health-check Host header match the selected app hostname
- Automatic stopping is off to avoid demo interruption; this incurs ongoing cost
- The `fly` Docker build target is explicit; normal Docker/Compose stays non-root
- `BRIDGE_PYTHON_EXTRAS=desktop,postgres` prepares the optional database driver;
  memory remains disabled until explicitly configured, and no DB is contacted

`min_machines_running` is not a scale-count setting. `--ha=false` avoids creating
the normal extra HA Machine on initial deployment, but does not shrink an existing
fleet. Check actual Machine count and volumes before and after deployment. This
is a single-owner demo topology, not a high-availability deployment.

## Credentials and authorization first

Before any cloud command that changes state, establish the correct Fly
organization, app/region, resources, all-in USD budget, desired runtime duration,
and accepted terms. Fly compute, storage, snapshots and egress can each incur
charges. Stopped compute does not imply zero storage cost; a billing alert is not
a hard cap. [Fly pricing](https://fly.io/pricing/)

The owner signs in and enters credentials through the trusted terminal/provider
flow. Keep a strong `BRIDGE_OWNER_TOKEN` in Fly runtime secrets. Never use image
build arguments, TOML, source, a public log, chat, or the coding workspace for real
secret values. The new `.dockerignore` allowlists image inputs so unrelated
workspace files and `.env` files are not sent to a remote image builder.

Only after the account, resources, data and spending are approved, an operator can
use the reviewed configuration with the normal Fly CLI flow:

```sh
fly apps create YOUR-APP-NAME --org YOUR-APPROVED-ORG
# The owner provides the token using Fly's secrets workflow before startup.
# If using a private secrets file, keep it outside this repository/workspace:
fly secrets import --app YOUR-APP-NAME < /PRIVATE/PATH/bridge-runtime-secrets.env
fly config validate --config fly.toml
fly deploy --config fly.toml --ha=false
fly status --app YOUR-APP-NAME
```

These are operator instructions, not commands the generator runs. Review the
current CLI prompts and quoted costs; do not blindly accept additional resources.
The initial-size setting allows the deployment flow to create a required volume.
For an existing app, explicitly inspect matching volumes, region and Machine count
rather than assuming this creates an isolated new deployment. No dedicated IPv4,
additional database, paid model gateway or separate agent runner is requested here.

## Fresh-volume startup

A new Fly volume may be owned by root while the ordinary image runs as `bridge`.
The `fly` target therefore starts a narrow Python bootstrap as root. It opens
`/data` without following symlinks, ensures only `workspace`, `state`, and `profile`
are directories, and changes ownership of those four directory inodes only. It
never recursively changes workspace contents. Non-directories/symlinks fail closed.
It then drops supplementary groups, GID and UID to the `bridge` account before
executing supervisor. The gateway, Chromium and Coding Tools do not run as root.

A custom `BRIDGE_DATA` location is rejected by this bootstrap. Existing files with
wrong ownership are not silently repaired; review those separately. The normal
`local` Docker target still starts as `bridge` without this root bootstrap.

The Fly target routes display/gateway child logs to private per-program files
(`/tmp/display.stdout.log`, `/tmp/display.stderr.log`, and matching `gateway`
files), capped at 1 MiB with two backups per stream. After a root-to-bridge UID
drop, reopening `/dev/stdout` can fail even though inherited descriptors remain
writable. Supervisor status still reaches container output; inspect the child log
files for application details. CI collects both kinds of logs. The local target's
logging is unchanged, and no pipe or filesystem permissions are broadened.

The browser already uses `--disable-dev-shm-usage`; Compose's `shm_size` is not
assumed to transfer to Fly. Test actual Chromium behavior, memory pressure and
filesystem capacity on the selected Machine before the live demo.

## Optional MCP configuration later

Follow [optional MCP setup](optional-mcp.md) first. The manifest contains only
approved endpoints, exact allowlists and environment-variable names, never keys.
A Fly `[[files]]` entry can copy that non-secret admin configuration at deployment:

```toml
# Merge this variable into the existing [env] table, not a second table:
# BRIDGE_MCP_CONFIG = "/app/plugins.json"

[[files]]
  guest_path = "/app/plugins.json"
  local_path = "config/plugins.json"
```

Add the commented variable to the existing `[env]` section when ready. The guest
file must be readable by `bridge` and not group/world writable. User-provided
credentials go through runtime secrets, separately. Changing the manifest or
keys requires restart/redeployment; it does not hot-swap an in-flight action.
No plugin is enabled merely by deploying this template.

[Fly files and configuration reference](https://docs.fly.io/reference/configuration)

## Required live checks

1. Image builds and starts; supervisor/application processes run as the non-root
   bridge user; a fresh volume initializes successfully.
2. HTTPS health succeeds, owner sign-in works, and the exact public origin matches
   OAuth metadata/redirect behavior. Health alone is not a real-client OAuth pass.
3. noVNC/WebSockets display the headed browser; Cua and Playwright operate the same
   screen; own Coding Tools can create/read/execute a harmless workspace file.
4. Private takeover, pause/revoke and fresh-observation requirements hold. Optional
   tools appear only when configured/allowlisted and their calls obey the lease.
5. Restart preserves test workspace/profile/receipts, revokes the expected sessions,
   and leaves uncertain operations unreplayed. A saved browser profile does not
   guarantee every website preserves login.
6. Review actual runtime/resource usage and stop compute after the approved demo
   window. Do not delete volumes or retained data without explicit approval.

Fly volumes are local to one host/region and are not automatically replicated.
Single-Machine downtime/data loss remains possible, and snapshots are not a full
backup strategy. [Volume limitations](https://docs.fly.io/volumes/overview)

## Isolation and verification limits

The Fly microVM protects the host boundary; it does not separate this application's
shell from its browser or gateway credentials. This is still a trusted-owner
preview with Chromium's inner sandbox disabled. Use demo accounts/low-sensitivity
data. Production handling of valuable DB keys and authenticated personal accounts
needs the trusted gateway, arbitrary-code sandbox and browser separated.

Unit tests validate rendering, safe defaults, file overwrite/symlink rejection,
initializer scope and privilege-drop ordering. Synthetic MCP tests do not call
vendors. A local pass is not a successful Fly CLI validation, image build, actual
mount startup, public deployment, or external-client connection. Report those
stages separately after they are run.
