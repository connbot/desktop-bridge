# Optional MCP plugins

The bridge can expose selected tools from owner-configured **Streamable HTTP MCP
servers** through the same authenticated endpoint. Email, telephone, search,
database administration, and other services are optional; there is no fixed
sponsor/provider enum. The project's own Coding Tools MCP remains the built-in
file/shell implementation.

**Nothing is connected by default.** Preparing an adapter or an example does not
establish an account, credential, working vendor integration, or permission to
send an email, place a call, buy something, or administer a database.

## Prepare now, configure after registering

`config/plugins.example.json` contains disabled, credential-free examples. Every
allowlist is deliberately empty. Endpoints/authentication conventions were checked
against public vendor documentation; their authenticated tool catalogs and account
eligibility have not been tested by this project.

1. Choose the service/account and review its exact available tool names. Select
   only tools needed for the task; no wildcard allowlist is supported. Database
   administration and sending/calling tools can have serious consequences.
2. Copy the example or write a minimal JSON configuration on the trusted server,
   outside the agent workspace. Use a regular file readable by the gateway and
   not writable by group/others, preferably owner-controlled or root-owned.
3. Put real credentials in the trusted host's runtime secret configuration. The
   JSON names environment variables; it must not contain credential values.
   Never paste keys into chat, source, tool arguments, memory, build arguments,
   or a workspace file. Register accounts and enter credentials yourself through
   the service's normal secure flow before enabling anything.
4. Set `BRIDGE_MCP_CONFIG` to the configuration's absolute path. Set `enabled` to
   `true` and supply an explicit `allowed_tools` list for each selected service.
5. Pause work, change configuration, restart, and reauthorize the client. Inspect
   owner-only `GET /api/plugins`, then refresh the MCP client's tool list.

Example shape for any compatible service (placeholder endpoint; not a live
integration):

```json
{
  "version": 1,
  "servers": [{
    "id": "research",
    "enabled": false,
    "url": "https://your-approved-service.example/mcp",
    "allowed_tools": ["the_exact_tool_name_from_the_vendor"],
    "headers": [{
      "name": "Authorization",
      "value_env": "RESEARCH_MCP_TOKEN",
      "prefix": "Bearer "
    }],
    "connect_timeout_seconds": 8,
    "call_timeout_seconds": 30
  }]
}
```

For a public service that needs no authentication, omit `headers`. Header values
must come from named environment variables; either raw values or the `Bearer `
prefix are supported. Reserved transport headers and forwarding the bridge owner
or context-database credential are rejected. This is not an OAuth client: services
that only support interactive OAuth need a separate implementation.

The configuration is **pluggable at restart**, not zero-downtime hot replacement.
There is no model-accessible server registration, config-editing API, live reload,
or automatic reconnect after a failed upstream session. Restart after fixing an
upstream configuration/outage. Changing endpoint or credential identity also
changes write-receipt identity, so an old action ID cannot silently apply to a new
account/target.

## What discovery and calls do

At startup, enabled/configured services are initialized independently. Only exact
allowlisted tools are exposed. A missing key reports `unconfigured`; a disabled
service reports `disabled`; an empty allowlist reports `no_tools_allowed`. A
connection/catalog failure reports `unavailable` without breaking the desktop or
other services. No selected name matching the remote catalog reports
`no_matching_tools`. Status contains IDs, states and counts, never endpoints,
headers, key values or raw upstream errors.

Names are `mcp_<server-id>__<upstream-tool>`; unusually long names get a stable
hash suffix to fit 64 characters. Server IDs cannot contain the `__` namespace
delimiter. Discovery also checks public names across the complete registry; any
collision disables every affected service rather than routing ambiguously.
Separate services can safely use the same ordinary upstream tool name. Arguments are wrapped, so an upstream's own `bridge_action_id` or
`arguments` field cannot collide with the gateway's receipt identifier:

```json
{
  "bridge_action_id": "unique-for-this-approved-operation",
  "arguments": {"the_upstream_parameter": "value"}
}
```

All optional tools are treated as potentially mutating, even if their upstream
`readOnlyHint` says otherwise. Each call needs agent control and goes through the
existing control lease/action receipt. This **does not** infer user permission to
perform a purchase, send mail, place a call, disclose data, or alter an account;
the MCP client must enforce the user's action-specific approval policy. Tool
allowlisting grants availability, not blanket permission to act.

Successful calls preserve MCP content, structured content, and `isError`, subject
to redaction and size limits. Duplicate completed action IDs return the previous
receipt without calling again. A timeout, disconnection, oversized result or
uncertain response becomes an unknown outcome; do not automatically replay the
operation. Inspect the external state first. Any control-generation change hides an in-flight upstream result, even if private
mode is entered and then left before the service replies. Stopping a request cannot undo an email/call/action already
accepted by its provider.

## Supported boundary and limits

This MVP supports HTTPS Streamable HTTP **tools only**. It does not proxy stdio,
legacy HTTP+SSE transport, resources, prompts, sampling, roots, elicitation,
upstream OAuth, task subscriptions, or server-driven agent runs. JSON responses
and Streamable HTTP SSE are handled by the pinned MCP SDK; synthetic tests cover
the JSON response path. Not every MCP service is compatible.

- Maximum 8 servers; 32 allowlisted tools per server, 128 total
- Discovery: at most 4 pages and 256 remote tool descriptions per service
- 64 KiB configuration and arguments; 32 KiB per input schema
- 256 KiB result; 2 MiB HTTP response stream; compressed responses rejected
- Connect/discovery deadline 1–15 seconds; call deadline 1–60 seconds
- Object input schemas with local JSON-pointer references; external references,
  anchors and dynamic references are rejected

Image-heavy MCP outputs can exceed these limits. Failure removes that service's
exposed tools until restart; it does not make an oversized remote action safe to
retry. A malformed owner configuration fails startup rather than guessing.

## Example services, with honest capability boundaries

- [AgentMail MCP](https://docs.agentmail.to/integrations/mcp): its documented remote
  endpoint and `x-api-key` path can be configured here after account setup and
  tool review. Mail delivery and permissions remain service/account dependent.
  [Incoming mail events](https://docs.agentmail.to/webhooks-overview) need a separate
  webhook/event consumer and runner. Adding mail tools does not make the bridge
  wake up autonomously for new email.
- [Vapi MCP](https://docs.vapi.ai/sdk/mcp-server): its documented endpoint uses bearer
  API-key authentication. A phone workflow still needs a provisioned service,
  eligible account/number, budget and explicit approval for actual calls. None
  are provisioned by this project.
- [Neon MCP](https://neon.com/guides/neon-mcp-server-github-copilot-vs-code): this is a
  database **management** integration, potentially broad and privileged. It is
  not required for [optional personal memory](context-providers.md), whose
  Postgres adapter has separate configuration. Do not enable resource-management
  tools merely to store preferences.

The model loop still lives in the existing MCP client. A later standalone agent
runner needs its own model access, approval policy, scheduling and event handling.
No installed framework or consumer subscription is silently converted into one.

## Security boundary

Only a trusted owner/admin may configure endpoints. URL checks reject HTTP,
inline credentials, query/fragment credentials, obvious local/private literal
addresses and nonstandard ports. Redirects and environment proxy inheritance are
disabled. These checks are not a complete egress firewall or DNS-rebinding defense:
a trusted hostname's DNS can still change. Use host/network-level egress control
for stronger isolation, and never accept endpoints from agent or webpage text.

Configured header secrets and common structured credential fields are redacted
from results; credential-bearing SDK wire diagnostics are suppressed. This is not
universal secret detection. Never allow tools that mint credentials or return
secret inventories merely because a sanitizer exists. Action receipts may contain
private business/mail/tool data and require protected storage.

The gateway, authenticated browser and coding shell still share one trusted-owner
container. Environment scrubbing and a configuration path outside the workspace
are not isolation from arbitrary same-UID code. Use demo accounts/minimal scopes
and low-sensitivity data. Valuable credentials require a separate trusted gateway,
separate code sandbox, separate authenticated browser, and controlled egress.

## Verification

The synthetic tests perform real MCP initialization, discovery and tool calls
against in-process FastMCP servers through an injected transport. Other contract
tests exercise outages, malformed catalogs, redirects, configured-secret
redaction, allowlists, collisions, timeouts, takeover and receipts. The injected
transport exists only as a Python test seam; JSON/env configuration cannot enable
loopback or substitute a transport. No AgentMail, Vapi or Neon account was accessed
for these tests. Validate the chosen service/account separately after setup.
