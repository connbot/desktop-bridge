# Security boundaries and reporting

Agent Computer is an early **single-trusted-owner** execution environment. It
contains a browser, arbitrary shell/code tools and a gateway in one container.
Use test accounts, sample files and minimal permissions. Do not treat it as a
sandbox for hostile code, a hardened personal-computer service or tenant isolation.

## What the controls do

- OAuth authorization-code flow with S256 PKCE requires owner approval. The owner
  bootstrap token is not accepted directly as an MCP bearer token.
- Refresh-capable clients get one-hour access tokens and single-use rotating
  refresh tokens, bound to the approved client, resource and `computer` scope.
  Only token hashes are retained. Grants expire after 30 days absolutely or
  7 days without refreshing; access expiry never exceeds the grant deadline.
  Reuse of an already consumed refresh token revokes all tokens in that grant.
  Clients must serialize refreshes and persist each returned replacement;
  retrying an old token after a lost response also requires a new authorization.
- Code-only clients receive no refresh token. Existing access tokens, owner
  passwords and refresh tokens cannot be substituted for one another.
  **Disconnect & revoke** clears every access/refresh grant, pending code and
  viewer session. The server supports one process; token state is not shared
  across workers and is never persisted to a database or workspace volume.
- The authenticated owner can use the shared desktop directly. Its pixel stream
  remains server-side read-only; a bounded parser routes actual input through the
  same lock as tool writes, with an input-server acknowledgement before release.
  Watching never transfers control. Held keys/buttons block agent input, and
  every human input invalidates previous GUI observations and queued tool writes.
- Private mode still blocks model observations and tool access; the owner's
  viewer remains available. Explicit Pause/Private/Stop drain the current action
  and attempt to stop tracked commands. Ordinary input does not kill commands.
  AI cannot override an explicit pause or silently retry an unknown input result.
- Tool writes retain durable action receipts. Unknown outcomes require inspection
  before another attempt. Failed key/button cleanup blocks AI until the owner
  resumes and a release-only recovery is acknowledged. Legacy read-only/control
  endpoints retain their prior authorization checks.
- Restart clears in-memory OAuth clients/grants and viewer sessions. Workspace,
  browser profile and action receipts persist on disk.

These controls cannot undo an external submission or purchase, defeat prompt
injection by themselves, or guarantee termination of daemonized/background work.
The connected AI client must still obtain the user's approval for consequential
actions. Inspect the session before entering any credential.

## Deployment rules

- Keep the gateway's host binding on loopback and put only that gateway behind
  trusted HTTPS. Never publish raw VNC/CDP or mount the Docker socket.
- Chromium runs without its inner sandbox. The container shares its host kernel,
  and outbound networking is not restricted.
- Arbitrary code can reach container-local services and affect the shared runtime.
  Environment scrubbing does not isolate valuable credentials from arbitrary code.
- Keep owner tokens, provider credentials and `.env` out of Git, chat, images,
  build arguments, screenshots and logs. Protect backups too: browser profiles and
  action receipts may contain private sessions or output.
- Do not publish one deployment to multiple independent users. All authorized
  clients reach the same owner's computer and data; OAuth is not per-tenant storage.
- Keep the host and dependencies updated. Review changes and run the relevant
  integration tests before upgrading a deployment with valuable data.

For suspected access compromise, stop new use, use **Disconnect & revoke** when
available, and stop the deployment. Privately rotate exposed credentials at their
providers. Rotating `BRIDGE_OWNER_TOKEN` also requires recreating the container
with the new runtime environment. Revocation cannot recover data already copied
or undo an external action. Preserve only the evidence needed for investigation,
without putting secrets in public reports.

## Report a concern

Do **not** publish exploit details, tokens, personal files or an active deployment
URL in a public issue. If this repository's GitHub **Security** tab offers
**Report a vulnerability**, use that private reporting flow. If it is unavailable,
open a minimal issue asking the maintainer for a private reporting channel, with
no vulnerability details, and wait for a verified response before sending them.
Response times are not guaranteed.

A private report should identify the affected commit, deployment setup, expected
boundary, minimal reproduction and impact, using test data. Ordinary setup and
compatibility problems can use the [public support checklist](CONTRIBUTING.md#getting-help).

This preview has no LTS support matrix or completed independent security audit.
See [architecture](docs/architecture.md) and [verification](docs/validation.md)
for implemented controls and the evidence behind them.
