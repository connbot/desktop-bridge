"""Small async storage boundary. Selecting Postgres never falls back to local data.

The model cannot select the provider, owner scope, or database target. Switching
configuration requires a restart and never imports/migrates context automatically.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import os
import re
from typing import Protocol
from urllib.parse import parse_qsl, urlsplit

from .personal import MAX_CONTEXT_BYTES, Context, PersonalStore
from .state import BridgeError

SELECT_CONTEXT = """SELECT revision, payload FROM desktop_bridge.personal_context
WHERE owner_id = %s"""
UPSERT_CONTEXT = """INSERT INTO desktop_bridge.personal_context (owner_id, revision, payload)
VALUES (%s, %s, %s::jsonb)
ON CONFLICT (owner_id) DO UPDATE SET revision = EXCLUDED.revision,
payload = EXCLUDED.payload, updated_at = CURRENT_TIMESTAMP
WHERE desktop_bridge.personal_context.revision = %s
RETURNING revision"""


class ContextStore(Protocol):
    identity: str

    async def read(self) -> dict: ...
    async def update(self, expected_revision: int, **changes) -> dict: ...
    def status(self) -> dict: ...


class DisabledContextStore:
    """Optional memory is off; no filesystem or database context is accessed."""
    identity = "disabled"

    async def read(self):
        raise BridgeError("CONTEXT_DISABLED", "Optional memory is off. Configure a context provider to save preferences and task history")

    async def update(self, expected_revision, **changes):
        return await self.read()

    def status(self):
        return {"provider": "disabled", "configured": False, "healthy": None, "error": "CONTEXT_DISABLED"}


class LocalContextStore:
    """Credential-free local adapter, only when the owner explicitly selects it."""
    identity = "local"

    def __init__(self, personal: PersonalStore):
        self.personal = personal

    async def read(self):
        return self.personal.read()

    async def update(self, expected_revision, **changes):
        return self.personal.update(expected_revision, **changes)

    def status(self):
        return {"provider": "local", "configured": True, "healthy": True, "error": None}


class PostgresContextStore:
    """One short async transaction per operation; no schema creation or migration.

    Per-operation connections keep the adapter small and pooler-compatible. The
    existing runtime lease serializes requests; a conditional upsert also guards
    concurrent processes. SQLite still owns action receipts on the private volume.
    """
    def __init__(self, personal: PersonalStore, database_url: str, owner_id: str, *, connect=None):
        self._validate_config(database_url, owner_id)
        if connect is None:
            try:
                from psycopg import AsyncConnection
            except ImportError:
                raise BridgeError("CONTEXT_CONFIG_ERROR", "Postgres requires the optional postgres dependency extra") from None
            connect = AsyncConnection.connect
        self.personal, self._connect = personal, connect
        self._database_url, self._owner_id = database_url, owner_id
        # A provider/target change must never replay another provider's write receipt.
        self.identity = "postgres:" + hashlib.sha256((database_url + "\0" + owner_id).encode()).hexdigest()
        self._healthy, self._error = None, None

    @staticmethod
    def _validate_config(database_url, owner_id):
        try:
            url = urlsplit(database_url)
            query = parse_qsl(url.query, keep_blank_values=True)
            options = dict(query)
            valid = (
                isinstance(database_url, str) and len(database_url) <= 4096
                and url.scheme in {"postgres", "postgresql"}
                and bool(url.hostname and url.username and url.path.strip("/"))
                and not url.fragment and url.port != 0
                and len(query) == len(options)
                and set(options) <= {"sslmode", "channel_binding"}
                and options.get("sslmode") in {"require", "verify-ca", "verify-full"}
                and options.get("channel_binding", "prefer") in {"require", "prefer", "disable"}
                and isinstance(owner_id, str) and re.fullmatch(r"[A-Za-z0-9_-]{1,64}", owner_id)
            )
        except (ValueError, TypeError, AttributeError):
            valid = False
        if not valid:
            raise BridgeError("CONTEXT_CONFIG_ERROR", "Postgres requires a TLS PostgreSQL URL and a stable owner ID; see provider setup docs")

    def status(self):
        return {"provider": "postgres", "configured": True, "healthy": self._healthy, "error": self._error}

    def __repr__(self):
        return "PostgresContextStore(configured=True)"

    @staticmethod
    def _decode(row):
        if row is None:
            return Context()
        try:
            revision, payload = row
            encoded = json.dumps(payload, ensure_ascii=False).encode()
            if len(encoded) > MAX_CONTEXT_BYTES:
                raise ValueError("too large")
            context = Context.model_validate(payload)
            if type(revision) is not int or context.revision != revision:
                raise ValueError("revision mismatch")
            return context
        except (ValueError, TypeError):
            raise BridgeError("INVALID_CONTEXT", "Stored database context failed validation") from None

    async def _run(self, expected_revision=None, changes=None):
        writing = changes is not None
        try:
            async with asyncio.timeout(10):
                # Disable prepared statements for transaction-pooler compatibility.
                async with await self._connect(
                    self._database_url, connect_timeout=3, prepare_threshold=None,
                ) as connection:
                    await connection.execute("SET LOCAL statement_timeout = '3000ms'")
                    await connection.execute("SET LOCAL lock_timeout = '1500ms'")
                    cursor = await connection.execute(SELECT_CONTEXT, (self._owner_id,))
                    row = await cursor.fetchone()
                    try:
                        current = self._decode(row)
                    except BridgeError:
                        self._healthy, self._error = False, "INVALID_CONTEXT"
                        raise
                    self._healthy, self._error = True, None
                    if not writing:
                        data = current.model_dump()
                    else:
                        data = self.personal.prepare(current, expected_revision, **changes)
                        cursor = await connection.execute(
                            UPSERT_CONTEXT,
                            (self._owner_id, data["revision"], json.dumps(data, ensure_ascii=False), expected_revision),
                        )
                        if await cursor.fetchone() is None:
                            raise BridgeError("CONTEXT_CONFLICT", "Context changed. Read it again before saving; your edit was not applied")
                # The context manager has committed successfully before reporting saved.
            self._healthy, self._error = True, None
            return data
        except BridgeError:
            raise
        except asyncio.CancelledError:
            self._healthy, self._error = False, "CONTEXT_OUTCOME_UNKNOWN" if writing else "CONTEXT_UNAVAILABLE"
            raise
        except Exception:
            # Driver errors can contain a credential-bearing DSN, SQL or user data.
            # Do not log, chain, or return them. A failed commit may have succeeded.
            code = "CONTEXT_OUTCOME_UNKNOWN" if writing else "CONTEXT_UNAVAILABLE"
            self._healthy, self._error = False, code
            message = (
                "Database write outcome is uncertain. Read context before retrying with a new action ID; no local fallback was used"
                if writing else "Configured context database is unavailable. The desktop still works; no local fallback was used"
            )
            raise BridgeError(code, message) from None

    async def read(self):
        return await self._run()

    async def update(self, expected_revision, **changes):
        return await self._run(expected_revision, changes)


def context_store(personal: PersonalStore, environment=None) -> ContextStore:
    env = os.environ if environment is None else environment
    backend = env.get("BRIDGE_CONTEXT_BACKEND", "disabled")
    if backend == "disabled":
        if env.get("BRIDGE_CONTEXT_DATABASE_URL") or env.get("BRIDGE_CONTEXT_OWNER_ID"):
            raise BridgeError("CONTEXT_CONFIG_ERROR", "Database settings require explicit BRIDGE_CONTEXT_BACKEND=postgres")
        return DisabledContextStore()
    if backend == "local":
        if env.get("BRIDGE_CONTEXT_DATABASE_URL") or env.get("BRIDGE_CONTEXT_OWNER_ID"):
            raise BridgeError("CONTEXT_CONFIG_ERROR", "Database settings require BRIDGE_CONTEXT_BACKEND=postgres; refusing an implicit local fallback")
        return LocalContextStore(personal)
    if backend == "postgres":
        return PostgresContextStore(personal, env.get("BRIDGE_CONTEXT_DATABASE_URL", ""), env.get("BRIDGE_CONTEXT_OWNER_ID", ""))
    raise BridgeError("CONTEXT_CONFIG_ERROR", "BRIDGE_CONTEXT_BACKEND must be disabled, local or postgres")
