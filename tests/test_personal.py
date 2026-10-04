"""Security, continuity, and ownership contracts for portable personal context."""

import asyncio
import json
import os
import stat
import subprocess
import sys
import threading
from concurrent.futures import ThreadPoolExecutor

import httpx
import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError
from test_app import TOKEN, FakeBrowser, FakeCoding, FakeDesktop, login

from desktop_bridge.app import Runtime, create_app
from desktop_bridge.personal import (
    MAX_CONTEXT_BYTES,
    AgentProfileUpdate,
    Context,
    ContextImport,
    PersonalStore,
    Profile,
    ProfileUpdate,
    SavedTask,
    Task,
)
from desktop_bridge.state import BridgeError


@pytest.fixture(autouse=True)
def explicit_local_context(monkeypatch):
    monkeypatch.setenv("BRIDGE_CONTEXT_BACKEND", "local")
    monkeypatch.delenv("BRIDGE_CONTEXT_DATABASE_URL", raising=False)
    monkeypatch.delenv("BRIDGE_CONTEXT_OWNER_ID", raising=False)


@pytest.fixture
def store(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    return PersonalStore(workspace)


@pytest.fixture
def runtime(tmp_path):
    runtime = Runtime(tmp_path, desktop=FakeDesktop(), browser=FakeBrowser(), coding=FakeCoding())
    yield runtime
    runtime.session.close()


@pytest.fixture
def personal_app(tmp_path):
    runtime = Runtime(tmp_path, desktop=FakeDesktop(), browser=FakeBrowser(), coding=FakeCoding())
    return create_app(tmp_path, TOKEN, "http://testserver", runtime)


def unpack(result):
    return json.loads(result[0].text)


def profile_arguments(revision=0, action_id="profile-1", name="Sample owner"):
    return {
        "profile": {"name": name, "preferences": "Prefer short updates"},
        "expected_revision": revision,
        "action_id": action_id,
    }


def test_empty_context_has_no_sample_personal_data_or_write_side_effect(store):
    assert store.read() == Context().model_dump()
    assert store.view()["tasks"] == []
    assert not store.directory.exists()


def test_profile_persists_as_private_atomic_json(store):
    result = store.update(0, profile=Profile(name="Example", goals="Learn more"))
    assert result["revision"] == 1
    assert result["updated_by"] == "owner"
    assert result["updated_at"]
    path = store.directory / "context.json"
    assert json.loads(path.read_text()) == result
    assert PersonalStore(store.workspace).read() == result
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert stat.S_IMODE(store.directory.stat().st_mode) == 0o700
    assert not list(store.directory.glob(".context-*.tmp"))


def test_revision_conflict_preserves_original_bytes(store):
    first = store.update(0, profile=Profile(name="First"))
    original = (store.directory / "context.json").read_bytes()
    with pytest.raises(BridgeError) as caught:
        store.update(0, profile=Profile(name="Stale"))
    assert caught.value.code == "CONTEXT_CONFLICT"
    assert store.read() == first
    assert (store.directory / "context.json").read_bytes() == original


def test_task_upsert_preserves_profile_and_other_tasks(store):
    store.update(0, profile=Profile(name="Owner"))
    store.update(1, task=Task(id="one", title="First task"), actor="agent")
    store.update(2, task=Task(id="two", title="Second task"))
    result = store.update(
        3, task=Task(id="one", title="First task", status="needs_input", next_step="Ask owner")
    )
    assert result["revision"] == 4
    assert result["profile"]["name"] == "Owner"
    assert [task["id"] for task in result["tasks"]] == ["one", "two"]
    assert result["tasks"][0]["status"] == "needs_input"
    assert result["tasks"][0]["updated_at"]
    assert result["tasks"][0]["updated_by"] == "owner"


def test_completed_task_requires_evidence_or_real_artifact(store):
    with pytest.raises(BridgeError) as caught:
        store.update(0, task=Task(id="one", title="Done", status="completed", evidence="  "))
    assert caught.value.code == "EVIDENCE_REQUIRED"
    assert store.read()["revision"] == 0
    result = store.update(
        0, task=Task(id="one", title="Done", status="completed", evidence="Observed success")
    )
    assert result["tasks"][0]["evidence"] == "Observed success"


def test_artifact_view_tracks_current_file_availability(store):
    artifact = store.workspace / "result.txt"
    artifact.write_bytes(b"result")
    store.update(0, task=Task(id="one", title="Done", status="completed", artifacts=["result.txt"]))
    assert store.view()["tasks"][0]["artifact_details"] == [
        {"path": "result.txt", "available": True, "bytes": 6}
    ]
    assert "artifact_details" not in store.read()["tasks"][0]
    artifact.unlink()
    assert store.view()["tasks"][0]["artifact_details"] == [
        {"path": "result.txt", "available": False, "bytes": None}
    ]


@pytest.mark.parametrize(
    "path", ["../secret", "/tmp/secret", "a/../secret", "a/./secret", "a//b", "a\\b",
             "personal/context.json", "personal", "", "a\x00b", "x" * 501]
)
def test_artifact_path_validation_rejects_unsafe_paths(path):
    with pytest.raises(ValidationError):
        Task(id="one", title="Task", artifacts=[path])


@pytest.mark.parametrize("kind", ["missing", "directory", "external_link", "internal_link", "linked_parent"])
def test_artifact_references_require_existing_regular_non_symlink_files(store, tmp_path, kind):
    outside = tmp_path / "secret.txt"
    outside.write_text("private")
    artifact = store.workspace / "artifact"
    if kind == "directory":
        artifact.mkdir()
    elif kind == "external_link":
        artifact.symlink_to(outside)
    elif kind == "internal_link":
        real = store.workspace / "real.txt"
        real.write_text("result")
        artifact.symlink_to(real)
    elif kind == "linked_parent":
        real = store.workspace / "real"
        real.mkdir()
        (real / "result.txt").write_text("result")
        artifact.symlink_to(real, target_is_directory=True)
    path = "artifact/result.txt" if kind == "linked_parent" else "artifact"
    assert store.artifact(path)["available"] is False
    with pytest.raises(BridgeError) as caught:
        store.update(0, task=Task(id="one", title="Task", artifacts=[path]))
    assert caught.value.code == "ARTIFACT_MISSING"
    assert outside.read_text() == "private"
    assert store.read()["revision"] == 0


@pytest.mark.parametrize("target", ["directory", "context", "lock"])
def test_context_storage_rejects_symlink_redirection(store, tmp_path, target):
    outside = tmp_path / "outside"
    outside.mkdir()
    sentinel = outside / "sentinel.json"
    sentinel.write_text(json.dumps(Context(profile=Profile(name="Secret")).model_dump()))
    original = sentinel.read_bytes()
    if target == "directory":
        store.directory.symlink_to(outside, target_is_directory=True)
    else:
        store.directory.mkdir()
        (store.directory / ("context.json" if target == "context" else ".context.lock")).symlink_to(sentinel)
    with pytest.raises(BridgeError):
        store.read()
    with pytest.raises(BridgeError):
        store.update(0, profile=Profile(name="Overwrite"))
    assert sentinel.read_bytes() == original


def test_context_fifo_is_rejected_without_hanging(store):
    store.directory.mkdir()
    os.mkfifo(store.directory / "context.json")
    code = (
        "from pathlib import Path; from desktop_bridge.personal import PersonalStore; "
        "from desktop_bridge.state import BridgeError; "
        f"store = PersonalStore(Path({str(store.workspace)!r}))\n"
        "try:\n store.read()\nexcept BridgeError:\n pass\nelse:\n raise AssertionError('FIFO accepted')"
    )
    try:
        result = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, timeout=2)
    except subprocess.TimeoutExpired:
        pytest.fail("Reading FIFO context.json blocks before checking that it is a regular file")
    assert result.returncode == 0, result.stderr


@pytest.mark.parametrize("raw", [b"not JSON", b"[]", b'{"revision":true}', b'{"unknown":1}', b"x" * (MAX_CONTEXT_BYTES + 1)])
def test_corrupt_or_oversized_context_is_not_silently_reset(store, raw):
    store.directory.mkdir()
    path = store.directory / "context.json"
    path.write_bytes(raw)
    with pytest.raises(BridgeError) as caught:
        store.read()
    assert caught.value.code == "INVALID_CONTEXT"
    with pytest.raises(BridgeError):
        store.update(0, profile=Profile(name="Reset"))
    assert path.read_bytes() == raw


@pytest.mark.parametrize("revision", [True, False, "0", 0.0, -1, None])
def test_revisions_are_strict_nonnegative_integers(revision):
    with pytest.raises(ValidationError):
        ProfileUpdate(expected_revision=revision, profile=Profile())


def test_schema_bounds_unknown_keys_duplicate_ids_and_invalid_status():
    cases = [
        (Profile, {"name": "x" * 81}),
        (Profile, {"preferences": "x" * 4001}),
        (Profile, {"secret_token": "not permitted"}),
        (Task, {"id": "../task", "title": "Task"}),
        (Task, {"id": "one", "title": "Task", "status": "sent"}),
        (Task, {"id": "one", "title": "Task", "artifacts": ["a"] * 11}),
        (Context, {"tasks": [{"id": "one", "title": "Task"}] * 2}),
        (Context, {"tasks": [{"id": f"task-{i}", "title": "Task"} for i in range(101)]}),
        (Context, {"schema_version": 2}),
        (AgentProfileUpdate, {"profile": {}, "expected_revision": 0, "action_id": ""}),
    ]
    for model, value in cases:
        with pytest.raises(ValidationError):
            model.model_validate(value)


def test_aggregate_utf8_limit_preserves_prior_context(store):
    original = store.update(0, profile=Profile(name="Preserved"))
    large = Context(tasks=[
        SavedTask(id=f"task-{i}", title="Task", summary="字" * 2000, evidence="字" * 2000)
        for i in range(12)
    ])
    with pytest.raises(BridgeError) as caught:
        store.update(1, imported=large)
    assert caught.value.code == "CONTEXT_TOO_LARGE"
    assert store.read() == original


def test_atomic_replace_failure_preserves_file_and_cleans_temporary_file(store, monkeypatch):
    original = store.update(0, profile=Profile(name="Original"))

    def fail_replace(*args, **kwargs):
        raise OSError("Simulated failed rename")

    monkeypatch.setattr("desktop_bridge.personal.os.replace", fail_replace)
    with pytest.raises(BridgeError) as caught:
        store.update(1, profile=Profile(name="Replacement"))
    assert caught.value.code == "CONTEXT_STORAGE_ERROR"
    assert store.read() == original
    assert not list(store.directory.glob(".context-*.tmp"))


def test_independent_store_instances_serialize_revision_checked_writes(store):
    barrier = threading.Barrier(2)

    def update(name):
        other = PersonalStore(store.workspace)
        barrier.wait(timeout=3)
        try:
            return other.update(0, profile=Profile(name=name))
        except BridgeError as exc:
            return exc.code

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(update, ["First", "Second"]))
    assert len([result for result in results if isinstance(result, dict)]) == 1
    assert results.count("CONTEXT_CONFLICT") == 1
    assert store.read()["revision"] == 1


def test_import_uses_live_revision_and_keeps_profile_tasks(store):
    store.update(0, profile=Profile(name="Current"))
    imported = Context(
        revision=9000,
        profile=Profile(name="Portable", constraints="No evening meetings"),
        tasks=[SavedTask(id="one", title="Task", status="completed", evidence="Observed")],
    )
    result = store.update(1, imported=imported)
    assert result["revision"] == 2
    assert result["profile"]["name"] == "Portable"
    assert result["tasks"][0]["id"] == "one"
    assert result["updated_by"] == "owner"
    assert imported.revision == 9000


def test_import_cannot_bypass_task_evidence_requirement(store):
    with pytest.raises((BridgeError, ValidationError)):
        imported = Context(tasks=[SavedTask(id="one", title="Completed", status="completed")])
        store.update(0, imported=imported)
    assert store.read()["revision"] == 0


@pytest.mark.parametrize("kind", ["missing_artifact", "symlink_artifact"])
def test_portable_import_preserves_unavailable_safe_artifact_references(store, tmp_path, kind):
    if kind == "symlink_artifact":
        target = tmp_path / "secret.txt"
        target.write_text("private")
        (store.workspace / "result.txt").symlink_to(target)
    imported = Context(tasks=[
        SavedTask(id="one", title="Completed", status="completed", artifacts=["result.txt"])
    ])
    store.update(0, imported=imported)
    assert store.read()["revision"] == 1
    assert store.view()["tasks"][0]["artifact_details"] == [
        {"path": "result.txt", "available": False, "bytes": None}
    ]


def test_personal_tools_have_bounded_closed_schemas(runtime):
    tools = {tool.name: tool for tool in runtime.tools()}
    assert {"personal_context", "personal_update_context", "personal_record_task"} <= tools.keys()
    for name in ("personal_update_context", "personal_record_task"):
        schema = tools[name].inputSchema
        assert schema["additionalProperties"] is False
        assert {"action_id", "expected_revision"} <= set(schema["required"])
    assert "authority" in tools["personal_context"].description


@pytest.mark.parametrize("mode", ["ready", "human", "private", "paused", "stopped"])
@pytest.mark.parametrize("tool_name", ["personal_update_context", "personal_record_task"])
async def test_all_agent_context_writes_require_agent_control(runtime, mode, tool_name):
    if mode != "ready":
        await runtime.session.transition(mode)
    args = profile_arguments()
    if tool_name == "personal_record_task":
        args.pop("profile")
        args["task"] = {"id": "one", "title": "Task"}
    with pytest.raises(BridgeError) as caught:
        await runtime.call(tool_name, args)
    assert caught.value.code == "CONTROL_NOT_OWNED"
    assert runtime.personal.read()["revision"] == 0


@pytest.mark.parametrize("mode", ["ready", "agent", "human", "paused", "stopped", "private"])
async def test_mcp_context_read_is_blocked_only_in_private_mode(runtime, mode):
    if mode != "ready":
        await runtime.session.transition(mode)
    if mode == "private":
        with pytest.raises(BridgeError) as caught:
            await runtime.call("personal_context", {})
        assert caught.value.code == "PRIVATE_TAKEOVER"
    else:
        assert unpack(await runtime.call("personal_context", {}))["revision"] == 0


async def test_agent_idempotency_preserves_revision_and_private_blocks_cached_write(runtime):
    await runtime.call("session_start", {})
    args = profile_arguments()
    first = unpack(await runtime.call("personal_update_context", args))
    second = unpack(await runtime.call("personal_update_context", args))
    assert first["revision"] == second["revision"] == 1
    assert second["replayed"] is True
    assert runtime.personal.read()["updated_by"] == "agent"
    with pytest.raises(BridgeError) as caught:
        await runtime.call("personal_update_context", profile_arguments(name="Changed"))
    assert caught.value.code == "IDEMPOTENCY_CONFLICT"
    assert runtime.personal.read()["profile"]["name"] == "Sample owner"
    assert "Sample owner" not in json.dumps(runtime.session.events)
    await runtime.session.transition("private")
    with pytest.raises(BridgeError) as caught:
        await runtime.call("personal_update_context", args)
    assert caught.value.code == "CONTROL_NOT_OWNED"


async def test_agent_task_result_and_receipt_do_not_copy_personal_content(runtime):
    await runtime.call("session_start", {})
    args = {
        "task": {"id": "one", "title": "Private task title", "status": "completed", "evidence": "Observed"},
        "expected_revision": 0,
        "action_id": "task-1",
    }
    result = unpack(await runtime.call("personal_record_task", args))
    assert result["saved"] is True
    assert runtime.personal.read()["tasks"][0]["updated_by"] == "agent"
    assert "Private task title" not in json.dumps(result)
    receipt = runtime.session.db.execute("SELECT result FROM receipts WHERE id='task-1'").fetchone()[0]
    assert "Private task title" not in receipt


async def test_completed_context_write_receipt_survives_restart(tmp_path):
    first = Runtime(tmp_path, desktop=FakeDesktop(), browser=FakeBrowser(), coding=FakeCoding())
    await first.call("session_start", {})
    await first.call("personal_update_context", profile_arguments())
    first.session.close()
    second = Runtime(tmp_path, desktop=FakeDesktop(), browser=FakeBrowser(), coding=FakeCoding())
    try:
        assert second.session.mode == "ready"
        await second.call("session_start", {})
        result = unpack(await second.call("personal_update_context", profile_arguments()))
        assert result["replayed"] is True
        assert second.personal.read()["revision"] == 1
        assert second.personal.read()["profile"]["name"] == "Sample owner"
    finally:
        second.session.close()


async def test_concurrent_agent_writes_do_not_lose_updates(runtime):
    await runtime.call("session_start", {})
    results = await asyncio.gather(
        runtime.call("personal_update_context", profile_arguments(action_id="a", name="A")),
        runtime.call("personal_update_context", profile_arguments(action_id="b", name="B")),
        return_exceptions=True,
    )
    assert len([value for value in results if isinstance(value, list)]) == 1
    errors = [value for value in results if isinstance(value, BridgeError)]
    assert len(errors) == 1 and errors[0].code == "CONTEXT_CONFLICT"
    assert runtime.personal.read()["revision"] == 1


async def test_queued_context_write_and_read_observe_private_takeover(runtime):
    await runtime.call("session_start", {})
    await runtime.session.lock.acquire()
    write = asyncio.create_task(runtime.call("personal_update_context", profile_arguments()))
    read = asyncio.create_task(runtime.call("personal_context", {}))
    await asyncio.sleep(0)
    await runtime.session.transition("private")
    runtime.session.lock.release()
    results = await asyncio.gather(write, read, return_exceptions=True)
    assert [result.code for result in results] == ["CONTROL_NOT_OWNED", "PRIVATE_TAKEOVER"]
    assert runtime.personal.read()["revision"] == 0


@pytest.mark.parametrize("method,path", [
    ("get", "/api/personal"), ("get", "/api/personal/export"),
    ("put", "/api/personal/profile"), ("post", "/api/personal/tasks"),
    ("post", "/api/personal/import"),
])
def test_owner_context_endpoints_require_login(personal_app, method, path):
    with TestClient(personal_app) as client:
        assert getattr(client, method)(path).status_code == 401


@pytest.mark.parametrize("method,path,payload", [
    ("put", "/api/personal/profile", {"profile": {}, "expected_revision": 0}),
    ("post", "/api/personal/tasks", {"task": {"id": "one", "title": "Task"}, "expected_revision": 0}),
    ("post", "/api/personal/import", {"context": Context().model_dump(), "expected_revision": 0}),
])
def test_owner_writes_require_csrf_and_same_origin(personal_app, method, path, payload):
    with TestClient(personal_app) as client:
        headers = login(client)
        for bad in ({}, {"X-CSRF-Token": "wrong"}, {**headers, "Origin": "https://evil.test"}):
            assert getattr(client, method)(path, json=payload, headers=bad).status_code == 401
        assert client.get("/api/personal").json()["revision"] == 0
        assert getattr(client, method)(path, json=payload, headers=headers).status_code == 200


def test_owner_can_edit_and_export_during_private_takeover(personal_app):
    with TestClient(personal_app) as client:
        headers = login(client)
        assert client.post("/api/control/private", headers=headers).status_code == 200
        response = client.put(
            "/api/personal/profile", headers=headers,
            json={"profile": {"name": "Private owner"}, "expected_revision": 0},
        )
        assert response.status_code == 200
        assert response.json()["updated_by"] == "owner"
        assert client.get("/api/personal").json()["profile"]["name"] == "Private owner"
        export = client.get("/api/personal/export")
        assert export.status_code == 200
        assert export.headers["content-type"].startswith("application/json")
        assert "attachment" in export.headers["content-disposition"]
        assert export.headers["cache-control"] == "no-store"
        assert export.json()["profile"]["name"] == "Private owner"


def test_owner_export_import_round_trip_and_stale_revision(personal_app):
    with TestClient(personal_app) as client:
        headers = login(client)
        client.put("/api/personal/profile", headers=headers,
                   json={"profile": {"name": "Portable"}, "expected_revision": 0})
        client.post("/api/personal/tasks", headers=headers,
                    json={"task": {"id": "one", "title": "Task", "status": "completed", "evidence": "Observed"},
                          "expected_revision": 1})
        exported = client.get("/api/personal/export").json()
        ContextImport.model_validate({"context": exported, "expected_revision": 2})
        exported["revision"] = 500
        result = client.post("/api/personal/import", headers=headers,
                             json={"context": exported, "expected_revision": 2})
        assert result.status_code == 200
        assert result.json()["revision"] == 3
        assert result.json()["profile"]["name"] == "Portable"
        assert len(result.json()["tasks"]) == 1
        conflict = client.put("/api/personal/profile", headers=headers,
                              json={"profile": {"name": "Lost edit"}, "expected_revision": 2})
        assert conflict.status_code == 400
        assert conflict.json()["error"] == "context_conflict"
        assert client.get("/api/personal").json()["profile"]["name"] == "Portable"


@pytest.mark.parametrize("body", [
    b"[]", b"not json", b'{"expected_revision":true,"profile":{}}',
    b'{"expected_revision":0,"profile":{},"extra":1}', b"x" * (MAX_CONTEXT_BYTES + 1025),
])
def test_owner_invalid_or_oversized_requests_leave_context_unchanged(personal_app, body):
    with TestClient(personal_app) as client:
        headers = login(client)
        response = client.put("/api/personal/profile", content=body, headers=headers)
        assert response.status_code == 400
        assert client.get("/api/personal").json()["revision"] == 0


async def test_concurrent_owner_http_writes_have_one_revision_winner(runtime, tmp_path):
    app = create_app(tmp_path, TOKEN, "http://testserver", runtime)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://testserver") as client:
        response = await client.post("/api/login", json={"token": TOKEN})
        headers = {"X-CSRF-Token": response.json()["csrf"]}
        responses = await asyncio.gather(*[
            client.put("/api/personal/profile", headers=headers,
                       json={"profile": {"name": name}, "expected_revision": 0})
            for name in ("One", "Two")
        ])
    assert sorted(response.status_code for response in responses) == [200, 400]
    assert runtime.personal.read()["revision"] == 1


def test_owner_task_limit_returns_validation_error_without_losing_existing_tasks(personal_app):
    runtime = personal_app.state.runtime
    runtime.personal.update(0, imported=Context(tasks=[
        SavedTask(id=f"task-{i}", title="Task") for i in range(100)
    ]))
    with TestClient(personal_app, raise_server_exceptions=False) as client:
        headers = login(client)
        response = client.post("/api/personal/tasks", headers=headers, json={
            "expected_revision": 1, "task": {"id": "overflow", "title": "One too many"},
        })
        assert response.status_code == 400
        current = client.get("/api/personal").json()
        assert current["revision"] == 1
        assert len(current["tasks"]) == 100
        updated = client.post("/api/personal/tasks", headers=headers, json={
            "expected_revision": 1,
            "task": {"id": "task-0", "title": "Updated", "status": "in_progress"},
        })
        assert updated.status_code == 200
        assert updated.json()["revision"] == 2
        assert len(updated.json()["tasks"]) == 100


async def test_invalid_agent_profile_is_rejected_before_recording_action(runtime):
    await runtime.call("session_start", {})
    args = profile_arguments()
    args["profile"]["name"] = "x" * 81
    with pytest.raises(ValidationError):
        await runtime.call("personal_update_context", args)
    assert runtime.session.db.execute("SELECT count(*) FROM receipts").fetchone()[0] == 0
    assert runtime.personal.read()["revision"] == 0


async def test_owner_write_waits_for_existing_agent_lease(runtime, tmp_path):
    app = create_app(tmp_path, TOKEN, "http://testserver", runtime)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://testserver") as client:
        response = await client.post("/api/login", json={"token": TOKEN})
        headers = {"X-CSRF-Token": response.json()["csrf"]}
        await runtime.session.lock.acquire()
        write = asyncio.create_task(client.put("/api/personal/profile", headers=headers, json={
            "profile": {"name": "Owner edit"}, "expected_revision": 0,
        }))
        try:
            await asyncio.sleep(0.01)
            assert not write.done()
            assert runtime.personal.read()["revision"] == 0
        finally:
            runtime.session.lock.release()
        assert (await write).status_code == 200
        assert runtime.personal.read()["profile"]["name"] == "Owner edit"


async def test_artifact_lists_exclude_reserved_personal_directory(runtime, tmp_path):
    runtime.personal.update(0, profile=Profile(name="Private context"))
    (runtime.personal.directory / ".context-incomplete.tmp").write_text("temporary context")
    (runtime.workspace / "result.txt").write_text("result")
    (runtime.workspace / "personal-result.txt").write_text("ordinary result")
    expected = {"result.txt", "personal-result.txt"}
    assert {item["path"] for item in unpack(await runtime.call("artifacts_list", {}))["files"]} == expected
    app = create_app(tmp_path, TOKEN, "http://testserver", runtime)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://testserver") as client:
        await client.post("/api/login", json={"token": TOKEN})
        response = await client.get("/api/artifacts")
        assert response.status_code == 200
        assert {item["path"] for item in response.json()["files"]} == expected
        export = await client.get("/api/personal/export")
        assert export.status_code == 200
        assert export.json()["profile"]["name"] == "Private context"
