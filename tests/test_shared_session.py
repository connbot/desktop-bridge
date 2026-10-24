"""Shared desktop input yields safely without a permanent handoff state."""

import asyncio
import json

import pytest
from test_app import FakeBrowser, FakeCoding, FakeDesktop

from desktop_bridge.app import Runtime
from desktop_bridge.state import BridgeError, Session


@pytest.fixture
def session(tmp_path):
    value = Session(tmp_path / "receipts.db")
    yield value
    value.close()


async def test_human_input_invalidates_old_screen_without_handoff(session):
    await session.transition("agent")
    before = session.observe()
    async with session.human_input("viewer"):
        assert session.mode == "agent"
        assert session.status()["human_input_pending"]
        with pytest.raises(BridgeError, match="Human input"):
            session.observe()
    assert session.mode == "agent"
    assert not session.status()["human_input_pending"]
    with pytest.raises(BridgeError) as stale:
        async with session.action("stale", {}, before):
            pytest.fail("stale click ran")
    assert stale.value.code == "STALE_OBSERVATION"
    async with session.action("fresh", {}, session.observe()) as ticket:
        ticket["result"] = {"ok": True}


async def test_input_waits_for_running_action_and_supersedes_all_queued_writes(session):
    await session.transition("agent")
    started, finish = asyncio.Event(), asyncio.Event()
    order = []

    async def running():
        async with session.action("running", {}):
            started.set()
            await finish.wait()
            order.append("agent finished")

    async def queued():
        async with session.action("queued coding", {"tool": "coding_exec"}):
            pytest.fail("pre-input queued write ran")

    async def human():
        async with session.human_input("viewer"):
            order.append("human input")

    active = asyncio.create_task(running())
    await started.wait()
    waiting = asyncio.create_task(queued())
    await asyncio.sleep(0)
    human_turn = asyncio.create_task(human())
    await asyncio.sleep(0)
    assert session.human_input_pending == 1
    with pytest.raises(BridgeError) as new_call:
        async with session.action("during input", {}):
            pytest.fail("call during pending human input queued itself")
    assert new_call.value.code == "HUMAN_INPUT_ACTIVE"
    finish.set()
    await active
    with pytest.raises(BridgeError) as old_call:
        await waiting
    assert old_call.value.code in {"HUMAN_INPUT_ACTIVE", "CONTROL_CHANGED"}
    await human_turn
    assert order == ["agent finished", "human input"]
    assert session.db.execute("SELECT id FROM receipts WHERE id='queued coding'").fetchone() is None
    async with session.action("new coding", {}):
        pass


async def test_modifier_and_drag_holds_cannot_expire_or_be_bypassed_by_other_viewer(session):
    await session.transition("agent")
    async with session.human_input("viewer"):
        session.human_holds.add("viewer")
    assert not session.human_input_pending
    assert session.status()["human_input_active"]
    with pytest.raises(BridgeError, match="Human input"):
        session.observe()
    with pytest.raises(BridgeError, match="Human input"):
        session.require_agent()
    with pytest.raises(BridgeError, match="other viewer"):
        async with session.human_input("other"):
            pytest.fail("two viewers combined held input")
    async with session.human_input("viewer"):
        session.human_holds.discard("viewer")
    session.observe()
    session.require_agent()


@pytest.mark.parametrize("mode", ["paused", "private", "stopped", "human"])
async def test_shared_input_never_resumes_explicit_modes(session, mode):
    await session.transition(mode)
    async with session.human_input("viewer"):
        pass
    assert session.mode == mode
    with pytest.raises(BridgeError) as blocked:
        async with session.action("no override", {}):
            pytest.fail("human input resumed explicit pause")
    assert blocked.value.code == "CONTROL_NOT_OWNED"
    if mode == "private":
        with pytest.raises(BridgeError) as hidden:
            session.observe()
        assert hidden.value.code == "PRIVATE_TAKEOVER"


async def test_cancelled_waiting_input_clears_pending_without_forwarding(session):
    await session.lock.acquire()

    async def human():
        async with session.human_input("viewer"):
            pytest.fail("cancelled pending input forwarded")

    task = asyncio.create_task(human())
    await asyncio.sleep(0)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    session.lock.release()
    assert session.human_input_pending == 0


async def test_shared_view_survives_mode_changes_while_legacy_control_disconnects(session):
    class Socket:
        closed = False

        async def close(self, **_):
            self.closed = True

    shared, legacy = Socket(), Socket()
    session.sockets.update({shared, legacy})
    session.shared_sockets.add(shared)
    await session.transition("private")
    assert legacy.closed
    assert not shared.closed


async def test_ordinary_input_does_not_cancel_managed_work_and_requires_new_observation(tmp_path):
    class Coding(FakeCoding):
        cancelled = False

        async def cancel_running(self):
            self.cancelled = True
            return []

    coding = Coding()
    runtime = Runtime(tmp_path, desktop=FakeDesktop(), browser=FakeBrowser(), coding=coding)
    try:
        await runtime.call("session_start", {})
        before = json.loads((await runtime.call("desktop_screenshot", {}))[0].text)
        async with runtime.session.human_input("viewer"):
            pass
        assert not coding.cancelled
        assert runtime.session.mode == "agent"
        with pytest.raises(BridgeError) as stale:
            await runtime.call("desktop_action", {
                "action": {"kind": "click", "x": 10, "y": 10},
                "action_id": "old", "observation_id": before["observation_id"],
            })
        assert stale.value.code == "STALE_OBSERVATION"
        fresh = json.loads((await runtime.call("desktop_screenshot", {}))[0].text)
        await runtime.call("desktop_action", {
            "action": {"kind": "click", "x": 10, "y": 10},
            "action_id": "new", "observation_id": fresh["observation_id"],
        })
        assert runtime.desktop.calls == 1
        await runtime.control("private")
        assert coding.cancelled
    finally:
        runtime.session.close()
