"""Opt-in real PostgreSQL tests; never connect to sponsor/customer infrastructure.

CI supplies a fresh TLS-enabled local Postgres service. Without the explicit
BRIDGE_TEST_POSTGRES_URL variable these tests are skipped, not counted as passed.
"""
from __future__ import annotations

import asyncio
import json
import os
import uuid
from pathlib import Path
from urllib.parse import urlsplit

import pytest

from desktop_bridge.context_store import PostgresContextStore
from desktop_bridge.personal import Context, PersonalStore, Profile, Task
from desktop_bridge.state import BridgeError

pytestmark = pytest.mark.asyncio


@pytest.fixture
def postgres_url():
    value = os.environ.get("BRIDGE_TEST_POSTGRES_URL")
    if not value:
        pytest.skip("Set BRIDGE_TEST_POSTGRES_URL for an explicitly disposable local Postgres service")
    if urlsplit(value).hostname not in {"127.0.0.1", "localhost"}:
        pytest.fail("Integration tests only accept a disposable loopback PostgreSQL target")
    pytest.importorskip("psycopg")
    return value


@pytest.fixture
def personal(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    return PersonalStore(workspace)


async def prepared_store(personal, postgres_url, owner=None):
    from psycopg import AsyncConnection

    # This is the test's explicit fixture setup; the adapter never runs DDL.
    sql = (Path(__file__).parents[1] / "migrations" / "001_personal_context.sql").read_text()
    async with await AsyncConnection.connect(postgres_url, prepare_threshold=None) as connection:
        await connection.execute(sql)
    return PostgresContextStore(personal, postgres_url, owner or "test-" + uuid.uuid4().hex)


async def test_real_postgres_round_trip_and_new_provider_instance(personal, postgres_url):
    owner = "test-" + uuid.uuid4().hex
    store = await prepared_store(personal, postgres_url, owner)
    assert await store.read() == Context().model_dump()
    first = await store.update(0, profile=Profile(name="Fictional owner", goals="More creative weekends"))
    assert first["revision"] == 1
    second = PostgresContextStore(personal, postgres_url, owner)
    assert await second.read() == first
    artifact = personal.workspace / "plan.md"
    artifact.write_text("Fictional plan. Nothing booked.")
    completed = await second.update(1, task=Task(
        id="weekend", title="Draft a weekend plan", status="completed",
        evidence="Read back the generated draft", artifacts=["plan.md"],
    ), actor="agent")
    assert completed["revision"] == 2
    assert completed["tasks"][0]["updated_by"] == "agent"
    assert personal.view_data(await store.read())["tasks"][0]["artifact_details"][0]["available"]
    assert not personal.directory.exists(), "Postgres must not mirror or silently migrate local context"


async def test_real_postgres_first_insert_and_update_races(personal, postgres_url):
    owner = "test-" + uuid.uuid4().hex
    first = await prepared_store(personal, postgres_url, owner)
    second = PostgresContextStore(personal, postgres_url, owner)
    for revision in (0, 1):
        outcomes = await asyncio.gather(
            first.update(revision, profile=Profile(name="A")),
            second.update(revision, profile=Profile(name="B")),
            return_exceptions=True,
        )
        saved = [value for value in outcomes if isinstance(value, dict)]
        errors = [value for value in outcomes if isinstance(value, BridgeError)]
        assert len(saved) == len(errors) == 1
        assert errors[0].code == "CONTEXT_CONFLICT"
        assert saved[0]["revision"] == revision + 1
        assert await first.read() == saved[0]


async def test_real_postgres_owner_scope_conflicts_and_import(personal, postgres_url):
    first = await prepared_store(personal, postgres_url)
    other = PostgresContextStore(personal, postgres_url, "test-" + uuid.uuid4().hex)
    one = await first.update(0, profile=Profile(name="One", preferences="x'); DROP TABLE ignored; --"))
    two = await other.update(0, profile=Profile(name="Two"))
    assert (await first.read())["profile"] == one["profile"]
    assert (await other.read())["profile"] == two["profile"]
    with pytest.raises(BridgeError) as caught:
        await first.update(0, profile=Profile(name="Stale"))
    assert caught.value.code == "CONTEXT_CONFLICT"
    exported = Context.model_validate(await first.read())
    exported.revision = 999
    restored = await first.update(1, imported=exported)
    assert restored["revision"] == 2 and restored["profile"]["name"] == "One"
    assert (await other.read())["revision"] == 1


async def test_real_postgres_server_error_rolls_back_and_redacts(personal, postgres_url):
    from psycopg import AsyncConnection

    owner = "test-" + uuid.uuid4().hex
    store = await prepared_store(personal, postgres_url, owner)
    original = await store.update(0, profile=Profile(name="Before"))

    async def read_only_connect(*args, **kwargs):
        connection = await AsyncConnection.connect(*args, **kwargs)
        await connection.execute("SET TRANSACTION READ ONLY")
        return connection

    blocked = PostgresContextStore(personal, postgres_url, owner, connect=read_only_connect)
    with pytest.raises(BridgeError) as caught:
        await blocked.update(1, profile=Profile(name="Not committed"))
    assert caught.value.code == "CONTEXT_OUTCOME_UNKNOWN"
    assert postgres_url not in str(caught.value)
    assert await store.read() == original
    assert not personal.directory.exists()
    assert json.loads(json.dumps(blocked.status()))["healthy"] is False
