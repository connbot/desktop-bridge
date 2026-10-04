# Optional personal memory providers

Personal memory is **off by default**. The desktop, browser, general MCP tools,
and the project's own **Coding Tools MCP** do not require a memory account.
Email, phone, search, model execution, and other MCP integrations are separate
capabilities; configuring this provider does not enable them.

The same three optional tools (`personal_context`, `personal_update_context`,
`personal_record_task`) and the same preferences/task UI work across the enabled
providers. This is a small storage boundary, not a new model loop or scheduler.

## Configuration choices

- `BRIDGE_CONTEXT_BACKEND=disabled` (default): no personal-context read or write,
  no database connection, and no implicit use of an existing local context file.
  The UI explains that optional memory is off and disables its edit/import/export
  controls. The rest of the computer remains usable.
- `BRIDGE_CONTEXT_BACKEND=local`: an explicit credential-free choice for an owner
  or a fictional demo. Context is bounded JSON in
  `/data/workspace/personal/context.json` on the self-hosted volume.
- `BRIDGE_CONTEXT_BACKEND=postgres`: use the configured Postgres database through
  psycopg's async driver. This includes a Neon-compatible connection path. It is
  an adapter implementation, **not evidence of a live Neon account connection**.

Database settings without an explicit `postgres` selection are rejected. Unknown
backend values are rejected. A configured database failure never starts reading
or writing the local provider. Existing data is not deleted, imported, merged, or
copied just because configuration changes.

Providers are **configuration-pluggable at restart**. This version does not swap
providers during an in-flight write. Stop work, export if appropriate, change the
trusted server configuration, restart, reauthorize the client, then explicitly
import an export if desired. Provider/target identity is part of MCP write receipt
fingerprinting, so an old successful write cannot be replayed into another store.

## Prepare the optional Postgres dependency

The standard Python install and Docker image do not require a database driver.
Choose the optional extra when preparing a Postgres-capable installation:

```sh
pip install -c constraints.txt -e '.[desktop,postgres]'
# or build an image ready for database configuration later:
docker build --build-arg BRIDGE_PYTHON_EXTRAS=desktop,postgres -t desktop-bridge:postgres .
```

The extra pins `psycopg[binary]==3.3.6`. It is imported only when Postgres is
selected. No database connection or resource creation happens during build.

## Configure an approved Neon/Postgres target later

Before entering credentials, resolve the exact project, branch, database, role,
permitted data, and schema change. Use a dedicated empty demo schema/test database
and a narrowly privileged application role. A production clone can contain
production data; cloning is not sanitization.

1. Have an authorized administrator review and apply
   `migrations/001_personal_context.sql` to the approved target through a trusted
   admin connection. The app never executes DDL or creates a role/database.
2. Give the application role only schema `USAGE` and table `SELECT`, `INSERT`, and
   `UPDATE` for `desktop_bridge.personal_context`. Do not use an administrator or
   valuable production credential in this trusted-owner demo container.
3. In the trusted host's secret configuration, the user supplies
   `BRIDGE_CONTEXT_DATABASE_URL`. Do not paste it into an AI conversation,
   preferences, a task, a workspace file, build arguments, or source control.
4. Set non-secret configuration:
   `BRIDGE_CONTEXT_BACKEND=postgres` and a stable
   `BRIDGE_CONTEXT_OWNER_ID` of 1–64 letters, digits, `_`, or `-`.
   The model cannot choose this scope. Changing it selects another context row;
   it does not migrate the previous row.
5. Restart the prepared app, authenticate, and read optional memory. Owner-only
   `/api/status` and MCP `session_status` report provider, configured state, and
   the last observed health. `healthy: null` means it has not been checked.
   No database host, username, password, connection URL, or owner ID is returned.

The connection URL must use `postgresql://` or `postgres://`, include a host,
username and database, and explicitly request `sslmode=require`, `verify-ca`, or
`verify-full`. The supported URL query options are `sslmode` and optional
`channel_binding` (`require`, `prefer`, or `disable`); other options and duplicate
parameters are rejected. Use the service's exact approved TLS settings. A pooled
Neon endpoint is compatible with this design: prepared statements are disabled,
and no session state is relied on between transactions.

`sslmode=require` encrypts the connection but is not a claim of full hostname/CA
verification. Use `verify-full` with the appropriate trusted certificate setup
when that is required. The CI fixture uses only `require` with its disposable
self-signed certificate; it does not validate Neon's production TLS setup.

## Data, concurrency, and failure behavior

The adapter keeps one bounded JSON document per server-configured owner in the
fixed `desktop_bridge.personal_context` table. The existing schema stores explicit
preferences/goals, author-reported tasks, evidence, artifact references, revision,
and provenance. No model credentials, cookies, screenshots, complete tool outputs,
or arbitrary shell environment dumps belong in it. User data is always passed as
SQL parameters, never interpolated into identifiers or SQL.

Each operation uses a short async connection/transaction. Connect timeout is three
seconds, statement timeout three seconds, lock timeout 1.5 seconds, and the outer
operation deadline ten seconds. A conditional upsert checks the expected revision
at the database, including first-write races. Imports use the live revision rather
than trusting the export's revision. The shared validation applies the same item,
field, path, evidence, and 128 KiB document limits to both enabled providers.

The gateway's existing control lease still protects all model writes. Private
mode also blocks model reads, including a read that finishes after takeover.
Cancelled requests retain the lease until the started write settles. An uncertain
commit is reported as `CONTEXT_OUTCOME_UNKNOWN`; read current context before a new
write and never blindly repeat an unknown external effect. Existing SQLite action
receipts remain on the private volume, rather than moving control coordination
into the database.

Database errors are redacted. Context endpoints can return unavailable while
`/healthz`, desktop controls, browser tools, and Coding Tools MCP continue working.
The UI displays a context-unavailable state rather than substituting a local
profile. A restart/new client can read the same database context, but this does
not resume a model loop or restore in-memory OAuth grants.

Context JSON export/import contains preferences and task records with references,
**not artifact file bytes**. Artifacts, the browser profile, and action receipts
still need the private workspace volume. Download those files separately; missing
imported artifact references are marked unavailable. Actions runners remain
explicitly disposable regardless of the selected context provider.

## Honest verification

- Unit/contract tests use an injected async driver to exercise selection, SQL
  parameter binding, failure redaction, private takeover, revisions, and
  cancellation without any sponsor credential or network connection.
- `tests/test_postgres_integration.py` runs only when
  `BRIDGE_TEST_POSTGRES_URL` explicitly points to a disposable loopback service.
  Otherwise it is skipped. It exercises real psycopg transactions, first-insert
  and update races, reconnect continuity, owner separation, import, and rollback.
- `.github/workflows/context-postgres.yml` prepares that disposable Postgres 17
  service with TLS and synthetic credentials. It requires no sponsor secret.
- A passed local mocked suite is not a live Postgres/Neon pass. A passed ephemeral
  Postgres job is not a live Neon/Fly deployment. Verify and report those stages
  separately after the user configures the intended services.

## Security boundary

This remains a **single trusted-owner container**, not isolation from malicious
agent code. Scrubbing a subprocess environment reduces accidental inheritance;
it does not prevent same-UID code from inspecting the gateway, local services,
process state, or other private files. Do not combine valuable database access or
authenticated personal accounts with untrusted arbitrary code in this monolith.
A production design needs the credential-bearing gateway separated from the
arbitrary execution environment and authenticated browser. The adapter alone
does not provide that separation, row-level tenant isolation, or restricted egress.

References: [Psycopg async API](https://www.psycopg.org/psycopg3/docs/advanced/async.html),
[Psycopg connection behavior](https://www.psycopg.org/psycopg3/docs/api/connections.html),
[Neon connection pooling](https://neon.com/docs/connect/connection-pooling).
