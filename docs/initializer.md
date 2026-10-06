# Web initializer: local review build

This is a working, **not deployed or production-accepted**, initialization service
for single-user development/testing of this project. It is separate from the
computer runtime. It does not provide a platform domain or permanent computer.

## What users do

1. Confirm their ChatGPT web account/workspace can create custom apps with action
   tools, and accept that preview files disappear when the run ends.
2. Connect a personal GitHub account and install the configured GitHub App.
3. Choose a temporary Quick Tunnel address, or optionally connect Cloudflare and
   choose an already active domain in their own account.
4. Download the browser-generated owner password, open the file and confirm they
   saved it. The website can observe a download request, not prove safe storage.
5. Explicitly create the dedicated public repository and start one 60-minute
   preview. The frontend sends a libsodium sealed box, never the password plaintext.
6. Wait for signed runner evidence and public checks, then manually add the MCP
   URL to ChatGPT using OAuth / dynamic client registration.

The GitHub repository is generated **from a template**, not automatically forked.
The default does not demand access to every repository, a broad OAuth `repo`
scope, workflow-content write permission or repository deletion permission.
The initial release supports personal accounts, not organization administration.

### Address choices, without hidden requirements

- **Quick:** no Cloudflare account or domain needed. The `trycloudflare.com`
  hostname changes between runs, stops working when the process ends, has no
  uptime guarantee, supports up to 200 concurrent in-flight requests, and does not
  support SSE. The project's stateless MCP endpoint explicitly returns 405 for
  authenticated GET/SSE requests (and still 401 without authentication); POST tool
  calls use JSON responses and initialized notifications return 202. This is not a
  compatibility guarantee for every MCP client or ChatGPT account.
- **Named:** recommended when the user already has a Cloudflare account **and an
  active domain**. An account alone does not provide a domain. The service creates
  a dedicated tunnel and one first-level subdomain in that user's zone. It never
  replaces existing DNS or silently switches to a platform-owned hostname.
- A fixed hostname preserves an address, **not files, a running computer or OAuth
  registration state**. This change does not implement persistent DCR identities.
  A new run can require recreating the ChatGPT app/connection and authorizing again.
- Actions is for developing/testing this application, not a free general-purpose
  personal desktop or production hosting service. Repository owners remain
  responsible for GitHub quotas, billing and applicable terms.

## Run locally without any credentials

Python 3.11+; use one process/worker. A normal browser can open the loopback page.

```sh
python3 -m venv .venv
. .venv/bin/activate
python -m pip install -c constraints.txt -r requirements-initializer.txt
INITIALIZER_MODE=mock \
INITIALIZER_ORIGIN=http://127.0.0.1:8765 \
INITIALIZER_DATABASE=/tmp/desktop-bridge-initializer-mock.sqlite3 \
python -m uvicorn initializer.app:app --host 127.0.0.1 --port 8765 --no-access-log
```

Open `http://127.0.0.1:8765`. A permanent yellow label identifies the mock. All
accounts, resources, addresses and runs are fictional; these URLs cannot be used
to connect ChatGPT. Mock mode is explicit, uses a separate cookie, rejects
non-loopback traffic and never falls back from live mode. The mock reset button
only exists in mock mode and only clears fictional test state.

Without `INITIALIZER_MODE=mock` or complete live configuration, the UI says the
GitHub service is not configured and refuses real authorization. It never claims
to be a published setup service.

### Offline tests

```sh
python -m pip install -c constraints.txt -e '.[test,initializer]'
npm --prefix initializer/ui ci --ignore-scripts
ruff check .
PYTHONPATH=src:. python -m pytest -q
node --test tests/viewer_state.test.cjs
python tests/initializer_dom.py
```

The DOM integration starts a loopback mock server and exercises the **shipped
frontend scripts, real HTTP API, Web Crypto via a Node test adapter, actual
libsodium encryption and Python sealed-box decryption**. It checks download
confirmation, double clicks, refresh, back navigation, password regeneration,
Cloudflare with no domain, cancellation, explicit restart/retry and uncertain
writes. It is not a visual browser, accessibility or real-provider test.

A real Chromium/Playwright test is also provided:

```sh
python -m playwright install chromium
python tests/initializer_browser.py --output /tmp/initializer-ui-review
```

An existing Chromium executable can be selected with
`INITIALIZER_TEST_CHROMIUM=/path/to/chromium`. In the implementation environment,
Chromium could not start because required Unix sockets/IPC were forbidden, even
with one reviewed environment escalation. Therefore real rendering, screenshots,
keyboard interaction and browser history were **not validated there**. Do not
turn a DOM pass into a visual QA claim. Run the script on a supported development
machine before public release. No external tunnel is needed for this test.

The bundled crypto JavaScript is self-hosted and built from the committed npm
lockfile. To reproduce it:

```sh
npm --prefix initializer/ui ci --ignore-scripts
npm --prefix initializer/ui run build
```

## Architecture and trust boundaries

- `initializer/app.py`: authenticated sessions, OAuth, CSRF, per-owner/per-operation
  serialization, lifecycle API, scoped retry/restart and OIDC callback verification.
- `initializer/providers.py`: real GitHub App, Cloudflare and signed-JWT adapters.
  Fixed API hosts; no provider response/exception dumping or environment proxy use.
- `initializer/mock.py`: separate, explicit offline adapter. It is never selected
  because real credentials are absent or a provider request fails.
- `initializer/network.py`: credential-free public readiness checks. Exact allowed
  hostname, HTTPS/443, no redirects, every resolved IP public, connection pinned to
  a vetted IP with original TLS SNI/Host to avoid DNS rebinding to internal targets.
- `initializer/store.py`: SQLite allowlisted **non-secret** operation ledger,
  numeric owner/repo/installation IDs, exact run, commit, resource IDs and phases.
- `initializer/static/`: Chinese beginner-oriented UI, local password download,
  CSPRNG and libsodium sealed-box encryption. No analytics, CDN, passwords in URLs,
  browser persistent storage or service-worker caching.
- `scripts/initializer_events.py`: GitHub OIDC-authenticated, audience-bound
  `ready`/`ending` events. No password or Cloudflare/GitHub management credential is
  sent to the control plane callback. Runtime token goes only to GitHub's fixed
  OIDC endpoint family.
- `scripts/preview.py` and `.github/workflows/preview.yml`: correlate the operation,
  run the existing authenticated public smoke, report readiness/30-second
  heartbeats, and report ending. Manual preview remains possible with empty
  initializer fields.

GitHub/Cloudflare management tokens and App signing material stay server-side.
Provider access tokens and cached repo-scoped installation tokens live only in
process memory. They never enter SQLite or the computer/container. The desktop
receives only its owner secret and its own tunnel connector token.

The browser still trusts the initializer's supplied JavaScript and GitHub public
key. An operator with Secrets write could replace an owner secret; browser-side
encryption is not a claim that a malicious control-plane operator cannot take over.

### State, interruptions and cleanup

- Template metadata and approved source SHA are checked before creation. The
  generated repository's complete Git tree is compared before any secret write;
  owner numeric ID and repository numeric ID are checked, then each installation
  token is scoped to that single repository.
- The reviewed branch is rechecked before dispatch. GitHub dispatch accepts a
  branch/tag rather than an immutable SHA; signed run claims and run metadata must
  match the approved generated commit. A changed run never becomes ready. This
  does not eliminate every mutable-ref race before GitHub releases a secret;
  keep the dedicated repo private to its owner in access terms and review the
  staging race tests before release. Public visibility does not grant others write.
- Named mode rechecks active zone, account, exact DNS record ID/name/CNAME/proxy
  setting and dedicated tunnel ingress before every launch, including restart.
- Ready requires GitHub OIDC signature/issuer/audience/time validation, exact
  repository/owner/run/attempt/SHA/event/ref/workflow binding, a monotonic event
  sequence, a currently running exact GitHub run, runner smoke success and an
  independent public metadata check. An Actions `in_progress` status alone cannot
  mark a computer ready.
- Failed public checks remove readiness. Heartbeat freshness expires after 90
  seconds. GET polling reconciles stopping, ending and unknown connection states
  with GitHub terminal status. Stop is serialized with ready callbacks, so a late
  callback cannot revive a stopped run.
- Double clicks reuse one operation. Explicit retries are limited to known safe
  failure classes and reuse existing resource IDs. Network-uncertain mutations
  stop. Unknown dispatch is resolved only by a unique matching workflow run with the
  exact per-launch random nonce (not just the long-lived deployment ID); zero
  or ambiguous matches never trigger another dispatch.
- Unknown repository/tunnel/DNS writes require operator read-only reconciliation
  against the recorded names/IDs. This release does not silently adopt or delete
  resources in that situation.
- One installed deployment per GitHub numeric user ID is supported. Explicit
  restart is available only after the previous exact run is terminal and reuses
  the repository and owner secret. Quick mode receives a new temporary address.
- This is a **single-worker** service. On server restart, in-flight operations are
  marked interrupted; management sessions are lost and require reauthorization.
  Known exact runs can be reconciled. Durable queues, multi-worker locking and
  automatic recovery of every partial provider mutation are not implemented.
- Stop cancels and checks the current workflow. It does **not** delete repositories,
  secrets, Cloudflare tunnels or DNS. Completed Actions status is not a separately
  measured proof of remote disk erasure.
- Password loss is not recoverable from GitHub Secrets. A user must stop any live
  preview, explicitly rotate the repository secret and reauthorize clients. An
  automated password-rotation/uninstall UI is not part of this release.

### Manual resource cleanup

Use the operation resource list. Stop the exact Actions run and wait until it ends.
If permanently uninstalling, remove `BRIDGE_OWNER_TOKEN` and, for named mode,
`CLOUDFLARE_TUNNEL_TOKEN` from this dedicated repo's Actions Secrets. Delete only the
listed DNS record and listed tunnel in the verified Cloudflare account. Revoke the
initializer's OAuth authorization and/or GitHub App installation as appropriate.
Revoking OAuth does not delete already-created resources or already-copied
connector tokens. Never post passwords, provider tokens or full debug logs in issues.

## Operator configuration and release gates

**None of the following real account/resource changes were performed by this
implementation.** They require the maintainer's explicit authorization and secure
credential entry. The source repository was read-only verified on 2026-10-06:
`connbot/desktop-bridge` was `is_template=false`; the starting main commit was
`ff47f53daa5c5c7802f632eea3c649afb498aa51`.

1. **Choose a public HTTPS origin for this web service.** This is the initializer's
   own origin for OAuth callbacks and runner events, not a domain supplied to
   users' computers. Deploy a dedicated backend, private persistent SQLite volume,
   HTTPS termination and one worker. Validate public DNS/SNI probe behavior with
   the actual host/network. Use a trusted proxy policy, not arbitrary forwarded
   headers. Add edge rate limits per IP/session and quotas per tenant; the built-in
   session limits are a backstop, not Internet-scale abuse protection.
2. **Publish/review this code and enable a source template.** Review and merge the
   workflow/runner changes and set the source repo to a template, or designate a
   separately reviewed template. Set `INITIALIZER_TEMPLATE` and its current default
   branch's exact approved `INITIALIZER_TEMPLATE_SHA`. Do not use the old starting
   commit above as a configured release: it lacks the new callback inputs/code.
   Upstream changes require another review and explicit config update.
3. **Register a GitHub App and configure its callbacks.** Set
   `INITIALIZER_GITHUB_APP_ID`, `INITIALIZER_GITHUB_SLUG`,
   `INITIALIZER_GITHUB_CLIENT_ID`, `INITIALIZER_GITHUB_CLIENT_SECRET`, and
   `INITIALIZER_GITHUB_PRIVATE_KEY` (PEM, secret manager/environment, never a source
   file). Callback: `{INITIALIZER_ORIGIN}/auth/github/callback`.
   Permissions: Metadata read, Contents read, Repository creation write,
   Actions write, Secrets write. Do not add Administration write merely to change
   account policies; surface disabled Actions instead. The personal-account,
   selected-installation, zero-existing-repository template flow **must be tested**
   with a consenting staging account. It is not guaranteed to be one consent click.
4. **Optionally register/publish Cloudflare OAuth.** Set
   `INITIALIZER_CF_CLIENT_ID`, `INITIALIZER_CF_CLIENT_SECRET`, and
   `INITIALIZER_CF_SCOPES` using currently verified scope IDs from Cloudflare's
   catalog for Tunnel write, DNS write, zone read and any necessary account read.
   No guessed scope IDs are shipped. Callback:
   `{INITIALIZER_ORIGIN}/auth/cloudflare/callback`. Complete Cloudflare's client
   domain TXT verification/public-client requirements if authorizing external
   accounts. Verify actual account/zone consent boundaries and enterprise denial.
   Without these settings, Quick mode still works and named authorization is
   visibly unavailable. Domain registration/nameserver onboarding is outside the
   wizard; only existing active zones are accepted.
5. **Suppress sensitive logs.** Always use `--no-access-log`. Reverse proxies,
   analytics/APM and exception collectors must not log OAuth callback query strings,
   cookies, Authorization headers, bodies or provider response payloads. Serve the
   bundled JavaScript/licenses as shipped with the CSP and no third-party scripts.
6. **Run staging acceptance with separately approved test resources.** Test real
   GitHub login/install/template creation, sealed secrets, exact dispatch/run ID,
   signed OIDC reporting, direct pinned public TLS probes and ChatGPT DCR/OAuth.
   Test two isolated accounts, CF no-zone/denied/expired authorization, DNS changes,
   network timeouts after writes, canceled consent, repeated clicks, server restart,
   manual cancellation, 60-minute expiry and subsequent Quick/named restarts.
   Inspect logs/artifacts using test canary secrets. Confirm named DNS/Tunnel reuse,
   owner-only access and manual cleanup. Run true browser/mobile rendering,
   keyboard/assistive-technology and browser Back/Forward tests.
7. **Respect launch constraints.** No recurring workflow restart, auto-renewal,
   shared multi-user desktop, production hosting promise or broad permission
   fallback is included. Confirm GitHub Actions use/billing policy for the intended
   rollout before offering the service publicly.

### Environment keys

`INITIALIZER_MODE=live` (default), `INITIALIZER_ORIGIN=https://...`,
`INITIALIZER_DATABASE=/private/persistent/path/state.sqlite3`, plus the App/template
and optional Cloudflare keys listed above. No user needs to create their own
GitHub App/OAuth client or paste a management API token into the wizard.

### Primary references

- [GitHub template repository generation](https://docs.github.com/en/rest/repos/repos#create-a-repository-using-a-template)
- [GitHub App installation behavior](https://docs.github.com/en/apps/using-github-apps/installing-a-github-app-from-a-third-party)
- [Encrypting GitHub Secrets](https://docs.github.com/en/rest/guides/encrypting-secrets-for-the-rest-api)
- [GitHub Actions OIDC reference](https://docs.github.com/en/actions/reference/security/oidc)
- [GitHub Actions terms](https://docs.github.com/en/site-policy/github-terms/github-terms-for-additional-products-and-features#actions)
- [Cloudflare OAuth clients](https://developers.cloudflare.com/fundamentals/oauth/create-an-oauth-client/)
- [Cloudflare OAuth integration](https://developers.cloudflare.com/fundamentals/oauth/integrate-with-cloudflare/)
- [Quick Tunnel limits](https://developers.cloudflare.com/tunnel/get-started/quick-tunnels/)
- [Current ChatGPT MCP availability](https://help.openai.com/en/articles/12584461-developer-mode-and-mcp-apps-in-chatgpt)
