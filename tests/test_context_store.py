"""Provider contracts with synthetic data and an in-process async database double.

These tests never connect to a database or establish a live Neon integration.
"""

import asyncio
import copy
import json

import httpx
import pytest
from fastapi.testclient import TestClient
from test_app import TOKEN, FakeBrowser, FakeCoding, FakeDesktop, login

from desktop_bridge import context_store as providers
from desktop_bridge.app import Runtime, create_app
from desktop_bridge.context_store import (
    SELECT_CONTEXT,
    UPSERT_CONTEXT,
    LocalContextStore,
    PostgresContextStore,
    context_store,
)
from desktop_bridge.personal import Context, PersonalStore, Profile, SavedTask, Task
from desktop_bridge.state import BridgeError

DATABASE_URL = "postgresql://demo:synthetic-secret@db.invalid/demo?sslmode=require"
OWNER_ID = "synthetic_owner"
SECRET_ERROR = DATABASE_URL + " SQL contained a private profile value"


@pytest.fixture(autouse=True)
def isolated_provider_environment(monkeypatch):
    # A developer's real provider configuration must never affect this suite.
    for name in (
        "BRIDGE_CONTEXT_BACKEND", "BRIDGE_CONTEXT_DATABASE_URL", "BRIDGE_CONTEXT_OWNER_ID",
    ):
        monkeypatch.delenv(name, raising=False)


@pytest.fixture
def personal(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    return PersonalStore(workspace)


class Cursor:
    def __init__(self, row):
        self.row = row

    async def fetchone(self):
        return self.row


class DatabaseDouble:
    """A scripted connection lifecycle, not a substitute for real SQL tests."""

    def __init__(self, row=None, *, failure=None, reject_upsert=False):
        self.row = copy.deepcopy(row)
        self.failure = failure
        self.reject_upsert = reject_upsert
        self.connections = []
        self.statements = []
        self.commits = 0
        self.rollbacks = 0
        self.closed = 0

    async def connect(self, url, **options):
        self.connections.append((url, options))
        if self.failure == "connect":
            raise OSError(SECRET_ERROR)
        return Connection(self)


class Connection:
    def __init__(self, database):
        self.database = database
        self.pending = None

    async def __aenter__(self):
        return self

    async def __aexit__(self, kind, error, traceback):
        database = self.database
        database.closed += 1
        if kind:
            database.rollbacks += 1
        elif database.failure == "commit":
            raise OSError(SECRET_ERROR)
        else:
            database.commits += 1
            if self.pending is not None:
                database.row = self.pending

    async def execute(self, query, params=None):
        database = self.database
        database.statements.append((query, params))
        if query == SELECT_CONTEXT:
            if database.failure == "select":
                raise OSError(SECRET_ERROR)
            return Cursor(copy.deepcopy(database.row))
        if query == UPSERT_CONTEXT:
            if database.failure == "upsert":
                raise OSError(SECRET_ERROR)
            if database.reject_upsert:
                return Cursor(None)
            owner, revision, payload, expected = params
            assert owner == OWNER_ID
            assert revision == expected + 1
            self.pending = (revision, json.loads(payload))
            return Cursor((revision,))
        assert query.startswith("SET LOCAL "), "Unexpected schema or data mutation"
        assert params is None
        return Cursor(None)


def postgres(personal, database):
    return PostgresContextStore(personal, DATABASE_URL, OWNER_ID, connect=database.connect)


def make_runtime(tmp_path, context):
    return Runtime(
        tmp_path, desktop=FakeDesktop(), browser=FakeBrowser(), coding=FakeCoding(),
        context=context,
    )


def arguments(action_id="context-write"):
    return {
        "expected_revision": 0, "profile": {"name": "Synthetic profile"},
        "action_id": action_id,
    }


def unpack(result):
    return json.loads(result[0].text)


async def test_local_adapter_preserves_async_read_write_contract(personal):
    provider = context_store(personal, {"BRIDGE_CONTEXT_BACKEND": "local"})
    assert isinstance(provider, LocalContextStore)
    assert await provider.read() == Context().model_dump()
    assert not personal.directory.exists()
    saved = await provider.update(0, profile=Profile(name="Local only"))
    assert saved == await provider.read() == personal.read()
    assert provider.status() == {
        "provider": "local", "configured": True, "healthy": True, "error": None,
    }


async def test_unconfigured_context_is_disabled_and_never_reads_or_writes_local_data(personal):
    original = personal.update(0, profile=Profile(name="Local data is not implicitly enabled"))
    provider = context_store(personal, {})
    assert provider.status() == {
        "provider": "disabled", "configured": False, "healthy": None,
        "error": "CONTEXT_DISABLED",
    }
    for operation in (provider.read(), provider.update(0, profile=Profile(name="Not saved"))):
        with pytest.raises(BridgeError) as caught:
            await operation
        assert caught.value.code == "CONTEXT_DISABLED"
    assert personal.read() == original


@pytest.mark.parametrize("environment", [
    {"BRIDGE_CONTEXT_BACKEND": "unknown"},
    {"BRIDGE_CONTEXT_BACKEND": ""},
    {"BRIDGE_CONTEXT_BACKEND": "LOCAL"},
    {"BRIDGE_CONTEXT_DATABASE_URL": DATABASE_URL},
    {"BRIDGE_CONTEXT_OWNER_ID": OWNER_ID},
    {"BRIDGE_CONTEXT_BACKEND": "local", "BRIDGE_CONTEXT_DATABASE_URL": DATABASE_URL},
])
def test_factory_rejects_ambiguous_configuration_without_local_fallback(personal, environment):
    with pytest.raises(BridgeError) as caught:
        context_store(personal, environment)
    assert caught.value.code == "CONTEXT_CONFIG_ERROR"
    assert DATABASE_URL not in str(caught.value)
    assert not personal.directory.exists()


def test_factory_passes_only_explicit_server_configuration(personal, monkeypatch):
    selected = []
    sentinel = object()

    def constructor(store, database_url, owner):
        selected.append((store, database_url, owner))
        return sentinel

    monkeypatch.setattr(providers, "PostgresContextStore", constructor)
    environment = {
        "BRIDGE_CONTEXT_BACKEND": "postgres",
        "BRIDGE_CONTEXT_DATABASE_URL": DATABASE_URL,
        "BRIDGE_CONTEXT_OWNER_ID": OWNER_ID,
    }
    assert context_store(personal, environment) is sentinel
    assert selected == [(personal, DATABASE_URL, OWNER_ID)]


@pytest.mark.parametrize("url,owner", [
    ("", OWNER_ID),
    ("postgresql://demo:secret@db.invalid/demo", OWNER_ID),
    (DATABASE_URL.replace("require", "disable"), OWNER_ID),
    (DATABASE_URL + "&sslmode=disable", OWNER_ID),
    (DATABASE_URL + "&options=-c%20search_path%3Dpublic", OWNER_ID),
    (DATABASE_URL + "#fragment", OWNER_ID),
    ("https://db.invalid/demo?sslmode=require", OWNER_ID),
    (DATABASE_URL, ""),
    (DATABASE_URL, "owner'; DROP TABLE context; --"),
    (DATABASE_URL, "x" * 65),
])
def test_postgres_configuration_rejects_unsafe_targets_without_connection(personal, url, owner):
    database = DatabaseDouble()
    with pytest.raises(BridgeError) as caught:
        PostgresContextStore(personal, url, owner, connect=database.connect)
    assert caught.value.code == "CONTEXT_CONFIG_ERROR"
    assert "secret" not in str(caught.value)
    assert database.connections == []


async def test_configured_is_not_healthy_until_verified_and_constructor_does_not_connect(personal):
    database = DatabaseDouble()
    provider = postgres(personal, database)
    assert provider.status() == {
        "provider": "postgres", "configured": True, "healthy": None, "error": None,
    }
    assert database.connections == []
    assert await provider.read() == Context().model_dump()
    assert provider.status()["healthy"] is True
    assert not personal.directory.exists()


async def test_database_round_trip_binds_data_and_never_creates_schema_or_local_state(personal):
    database = DatabaseDouble()
    provider = postgres(personal, database)
    content = "Robert'); DROP TABLE desktop_bridge.personal_context; -- 字"
    saved = await provider.update(0, profile=Profile(name=content), actor="agent")
    assert saved["revision"] == 1
    assert saved["updated_by"] == "agent"
    assert await provider.read() == saved
    assert database.commits == database.closed == 2
    assert database.rollbacks == 0
    for url, options in database.connections:
        assert url == DATABASE_URL
        assert 0 < options["connect_timeout"] <= 10
        assert options["prepare_threshold"] is None
    for sql, params in database.statements:
        assert OWNER_ID not in sql and content not in sql and DATABASE_URL not in sql
        assert not any(word in sql.upper() for word in ("CREATE ", "ALTER ", "DROP "))
        if sql == SELECT_CONTEXT:
            assert params == (OWNER_ID,)
        elif sql == UPSERT_CONTEXT:
            assert params[0] == OWNER_ID and params[-1] == 0
            assert json.loads(params[2])["profile"]["name"] == content
    assert not personal.directory.exists()


async def test_postgres_selection_does_not_import_or_overwrite_existing_local_context(personal):
    local = personal.update(0, profile=Profile(name="Local data stays local"))
    original = (personal.directory / "context.json").read_bytes()
    database = DatabaseDouble()
    provider = postgres(personal, database)
    assert (await provider.read())["revision"] == 0
    await provider.update(0, profile=Profile(name="Separate database value"))
    assert personal.read() == local
    assert (personal.directory / "context.json").read_bytes() == original


async def test_existing_revision_conflict_rolls_back_before_upsert(personal):
    stored = Context(revision=4, profile=Profile(name="Current")).model_dump()
    database = DatabaseDouble((4, stored))
    provider = postgres(personal, database)
    with pytest.raises(BridgeError) as caught:
        await provider.update(3, profile=Profile(name="Stale"))
    assert caught.value.code == "CONTEXT_CONFLICT"
    assert database.row == (4, stored)
    assert database.rollbacks == database.closed == 1
    assert all(sql != UPSERT_CONTEXT for sql, _ in database.statements)


async def test_conditional_upsert_conflict_is_not_reported_as_saved(personal):
    database = DatabaseDouble(reject_upsert=True)
    provider = postgres(personal, database)
    with pytest.raises(BridgeError) as caught:
        await provider.update(0, profile=Profile(name="Racing first write"))
    assert caught.value.code == "CONTEXT_CONFLICT"
    assert database.row is None
    assert database.commits == 0 and database.rollbacks == 1
    assert not personal.directory.exists()


@pytest.mark.parametrize("writing,failure", [
    (False, "connect"), (False, "select"), (False, "commit"),
    (True, "connect"), (True, "select"), (True, "upsert"), (True, "commit"),
])
async def test_driver_failures_are_redacted_and_never_fall_back(personal, caplog, writing, failure):
    database = DatabaseDouble(failure=failure)
    provider = postgres(personal, database)
    operation = (
        provider.update(0, profile=Profile(name="Private profile"))
        if writing else provider.read()
    )
    with pytest.raises(BridgeError) as caught:
        await operation
    expected = "CONTEXT_OUTCOME_UNKNOWN" if writing else "CONTEXT_UNAVAILABLE"
    assert caught.value.code == expected
    assert caught.value.__suppress_context__ is True
    public = str(caught.value) + repr(provider) + json.dumps(provider.status()) + caplog.text
    assert "synthetic-secret" not in public and "private profile value" not in public
    assert provider.status() == {
        "provider": "postgres", "configured": True, "healthy": False, "error": expected,
    }
    assert database.row is None
    assert not personal.directory.exists()


async def test_health_recovers_after_a_successful_read(personal):
    database = DatabaseDouble(failure="connect")
    provider = postgres(personal, database)
    with pytest.raises(BridgeError):
        await provider.read()
    database.failure = None
    await provider.read()
    assert provider.status()["healthy"] is True
    assert provider.status()["error"] is None


def blocked_postgres(personal):
    database = DatabaseDouble()
    started = asyncio.Event()

    class BlockingConnection(Connection):
        async def execute(self, query, params=None):
            if query == SELECT_CONTEXT:
                started.set()
                await asyncio.Event().wait()
            return await super().execute(query, params)

    async def connect(url, **options):
        database.connections.append((url, options))
        return BlockingConnection(database)

    provider = PostgresContextStore(personal, DATABASE_URL, OWNER_ID, connect=connect)
    return provider, database, started


@pytest.mark.parametrize("writing", [False, True])
async def test_provider_cancellation_rolls_back_closes_and_preserves_cancellation(personal, writing):
    provider, database, started = blocked_postgres(personal)
    operation = asyncio.create_task(
        provider.update(0, profile=Profile(name="Cancelled")) if writing else provider.read(),
    )
    try:
        await asyncio.wait_for(started.wait(), 1)
        operation.cancel()
        with pytest.raises(asyncio.CancelledError):
            await operation
        assert database.rollbacks == database.closed == 1
        assert database.commits == 0
        assert provider.status()["configured"] is True
        assert provider.status()["healthy"] is False
        assert provider.status()["error"] == (
            "CONTEXT_OUTCOME_UNKNOWN" if writing else "CONTEXT_UNAVAILABLE"
        )
        assert not personal.directory.exists()
    finally:
        operation.cancel()
        await asyncio.gather(operation, return_exceptions=True)


@pytest.mark.parametrize("writing", [False, True])
async def test_provider_has_bounded_operation_deadline_and_does_not_retry(personal, monkeypatch, writing):
    provider, database, started = blocked_postgres(personal)
    timeout = asyncio.timeout
    deadlines = []

    def fast_timeout(seconds):
        deadlines.append(seconds)
        return timeout(0.001)

    monkeypatch.setattr(providers.asyncio, "timeout", fast_timeout)
    with pytest.raises(BridgeError) as caught:
        if writing:
            await provider.update(0, profile=Profile(name="Timed out"))
        else:
            await provider.read()
    assert started.is_set() and len(deadlines) == 1 and 0 < deadlines[0] <= 10
    assert caught.value.code == ("CONTEXT_OUTCOME_UNKNOWN" if writing else "CONTEXT_UNAVAILABLE")
    assert database.rollbacks == database.closed == len(database.connections) == 1
    assert database.commits == 0
    assert not personal.directory.exists()


@pytest.mark.parametrize("row", [
    (1, Context().model_dump()),
    (True, Context(revision=1).model_dump()),
    (0, {"unknown": "field"}),
    (0, []),
    (0, {"profile": {"preferences": "x" * 4001}}),
])
async def test_corrupt_database_payload_is_rejected_without_reset_or_write(personal, row):
    database = DatabaseDouble()
    provider = postgres(personal, database)
    await provider.read()
    database.row = copy.deepcopy(row)
    with pytest.raises(BridgeError) as caught:
        await provider.read()
    assert caught.value.code == "INVALID_CONTEXT"
    assert provider.status()["healthy"] is False
    assert database.row == row
    assert all(sql != UPSERT_CONTEXT for sql, _ in database.statements)
    assert not personal.directory.exists()


async def test_database_uses_shared_artifact_and_completion_validation(personal):
    database = DatabaseDouble()
    provider = postgres(personal, database)
    for task, code in [
        (Task(id="one", title="Claim", status="completed"), "EVIDENCE_REQUIRED"),
        (Task(id="one", title="Missing", artifacts=["missing.txt"]), "ARTIFACT_MISSING"),
    ]:
        with pytest.raises(BridgeError) as caught:
            await provider.update(0, task=task)
        assert caught.value.code == code
    assert database.row is None
    assert all(sql != UPSERT_CONTEXT for sql, _ in database.statements)
    imported = Context(revision=100, tasks=[
        SavedTask(id="portable", title="Portable", status="completed", artifacts=["missing.txt"]),
    ])
    saved = await provider.update(0, imported=imported)
    assert saved["revision"] == 1 and imported.revision == 100
    assert personal.view_data(saved)["tasks"][0]["artifact_details"][0]["available"] is False


async def test_database_outage_keeps_core_runtime_and_desktop_usable(personal, tmp_path):
    database = DatabaseDouble(failure="connect")
    provider = postgres(personal, database)
    runtime = make_runtime(tmp_path, provider)
    try:
        await runtime.start()
        assert runtime.ready and database.connections == []
        with pytest.raises(BridgeError) as caught:
            await runtime.call("personal_context", {})
        assert caught.value.code == "CONTEXT_UNAVAILABLE"
        assert unpack(await runtime.call("session_status", {}))["state"] == "READY"
        await runtime.call("session_start", {})
        shot = unpack((await runtime.call("desktop_screenshot", {}))[:1])
        result = await runtime.call("desktop_action", {
            "action_id": "desktop-during-outage", "observation_id": shot["observation_id"],
            "action": {"kind": "click", "x": 10, "y": 20},
        })
        assert unpack(result)["ok"] is True
        assert not personal.directory.exists()
    finally:
        await runtime.close()


def test_http_outage_reports_503_without_disabling_health_or_owner_controls(personal, tmp_path):
    database = DatabaseDouble(failure="connect")
    runtime = make_runtime(tmp_path, postgres(personal, database))
    app = create_app(tmp_path, TOKEN, "http://testserver", runtime)
    with TestClient(app) as client:
        headers = login(client)
        assert client.get("/healthz").status_code == 200
        assert database.connections == []
        assert client.get("/api/status").json()["context_store"]["healthy"] is None
        read = client.get("/api/personal")
        assert read.status_code == 503 and read.json()["error"] == "context_unavailable"
        write = client.put("/api/personal/profile", headers=headers, json={
            "expected_revision": 0, "profile": {"name": "Not saved locally"},
        })
        assert write.status_code == 503 and write.json()["error"] == "context_outcome_unknown"
        status = client.get("/api/status")
        assert status.status_code == 200 and status.json()["ready"] is True
        assert status.json()["context_store"]["healthy"] is False
        assert "synthetic-secret" not in read.text + write.text + status.text
        assert client.post("/api/control/private", headers=headers).status_code == 200
        assert client.get("/healthz").status_code == 200
    assert not personal.directory.exists()


class DelayedContext:
    identity = "mock-delayed-context"

    def __init__(self):
        self.started = asyncio.Event()
        self.finish = asyncio.Event()
        self.saved = False
        self.updates = 0

    async def read(self):
        self.started.set()
        await self.finish.wait()
        return Context(profile=Profile(name="Should stay private")).model_dump()

    async def update(self, expected_revision, **changes):
        self.updates += 1
        self.started.set()
        await self.finish.wait()
        self.saved = True
        return Context(revision=expected_revision + 1).model_dump()

    def status(self):
        return {"provider": "mock", "configured": True, "healthy": True, "error": None}


@pytest.mark.parametrize("mode,error", [
    ("private", "PRIVATE_TAKEOVER"), ("human", "STALE_OBSERVATION"),
])
async def test_takeover_while_database_read_is_pending_never_returns_context(tmp_path, mode, error):
    provider = DelayedContext()
    runtime = make_runtime(tmp_path, provider)
    read = asyncio.create_task(runtime.call("personal_context", {}))
    try:
        await asyncio.wait_for(provider.started.wait(), 1)
        await runtime.session.transition(mode)
        provider.finish.set()
        with pytest.raises(BridgeError) as caught:
            await read
        assert caught.value.code == error
    finally:
        provider.finish.set()
        await asyncio.gather(read, return_exceptions=True)
        runtime.session.close()


async def test_cancelled_context_write_holds_lease_and_leaves_nonreplayable_receipt(tmp_path):
    provider = DelayedContext()
    runtime = make_runtime(tmp_path, provider)
    await runtime.call("session_start", {})
    write = asyncio.create_task(runtime.call("personal_update_context", arguments()))
    try:
        await asyncio.wait_for(provider.started.wait(), 1)
        write.cancel()
        await asyncio.sleep(0)
        assert not write.done() and runtime.session.lock.locked()
        assert not provider.saved
        provider.finish.set()
        with pytest.raises(asyncio.CancelledError):
            await write
        assert provider.saved and not runtime.session.lock.locked()
        assert runtime.session.db.execute(
            "SELECT state FROM receipts WHERE id = ?", ("context-write",),
        ).fetchone()[0] == "unknown"
        with pytest.raises(BridgeError) as caught:
            await runtime.call("personal_update_context", arguments())
        assert caught.value.code == "OUTCOME_UNKNOWN"
        assert provider.updates == 1
        assert "Synthetic profile" not in json.dumps(runtime.session.events)
    finally:
        provider.finish.set()
        await asyncio.gather(write, return_exceptions=True)
        runtime.session.close()


async def test_owner_write_waits_for_provider_to_finish_after_agent_request_cancellation(tmp_path):
    provider = DelayedContext()
    runtime = make_runtime(tmp_path, provider)
    await runtime.call("session_start", {})
    app = create_app(tmp_path, TOKEN, "http://testserver", runtime)
    write = asyncio.create_task(runtime.call("personal_update_context", arguments()))
    owner_write = None
    try:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://testserver",
        ) as client:
            response = await client.post("/api/login", json={"token": TOKEN})
            await asyncio.wait_for(provider.started.wait(), 1)
            write.cancel()
            owner_write = asyncio.create_task(client.put(
                "/api/personal/profile", headers={"X-CSRF-Token": response.json()["csrf"]},
                json={"expected_revision": 1, "profile": {"name": "Owner edit"}},
            ))
            await asyncio.sleep(0.01)
            assert provider.updates == 1 and not owner_write.done()
            provider.finish.set()
            with pytest.raises(asyncio.CancelledError):
                await write
            assert (await owner_write).status_code == 200
            assert provider.updates == 2
    finally:
        provider.finish.set()
        await asyncio.gather(write, *([owner_write] if owner_write else []), return_exceptions=True)
        runtime.session.close()


async def test_receipt_cannot_be_replayed_after_context_provider_target_changes(tmp_path):
    provider = DelayedContext()
    provider.finish.set()
    runtime = make_runtime(tmp_path, provider)
    try:
        await runtime.call("session_start", {})
        await runtime.call("personal_update_context", arguments())
        second = DelayedContext()
        second.identity = "mock-different-target"
        second.finish.set()
        runtime.context = second
        with pytest.raises(BridgeError) as caught:
            await runtime.call("personal_update_context", arguments())
        assert caught.value.code == "IDEMPOTENCY_CONFLICT"
        assert second.updates == 0
    finally:
        runtime.session.close()
