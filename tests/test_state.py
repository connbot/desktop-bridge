import asyncio

import pytest

from desktop_bridge.state import BridgeError, Session


@pytest.fixture
def session(tmp_path):
    session = Session(tmp_path / "state.db")
    yield session
    session.close()


async def test_freshness_and_cached_receipts(session):
    await session.transition("agent")
    obs = session.observe()
    async with session.action("1", {"tool": "click"}, obs) as t:
        t["result"] = {"clicked": True}
    async with session.action("1", {"tool": "click"}, obs) as t:
        assert t["cached"] == {"clicked": True}
    with pytest.raises(BridgeError, match="fresh"):
        async with session.action("2", {"tool": "click"}, obs):
            pytest.fail("stale coordinates accepted")
    with pytest.raises(BridgeError, match="another action"):
        async with session.action("1", {"tool": "type"}):
            pytest.fail("id conflict accepted")


async def test_takeover_revokes_before_lock_drains(session):
    await session.transition("agent")
    started, unblock = asyncio.Event(), asyncio.Event()

    async def slow():
        async with session.action("slow", {"tool": "write"}):
            started.set()
            await unblock.wait()

    running = asyncio.create_task(slow())
    await started.wait()
    await session.transition("human")
    assert session.status()["in_flight"] is True
    waiting = asyncio.create_task(_write(session))
    unblock.set()
    await running
    with pytest.raises(BridgeError, match="cannot act"):
        await waiting


async def _write(session):
    async with session.action("queued", {"tool": "write"}):
        pytest.fail("queued write ran after takeover")


async def test_restart_marks_unknown(tmp_path):
    db = tmp_path / "state.db"
    session = Session(db)
    await session.transition("agent")
    with pytest.raises(RuntimeError):
        async with session.action("lost", {"tool": "submit"}):
            raise RuntimeError("lost response")
    session.close()
    session = Session(db)
    await session.transition("agent")
    with pytest.raises(BridgeError, match="Do not replay"):
        async with session.action("lost", {"tool": "submit"}):
            pytest.fail()
    session.close()


async def test_private_and_expired_observation(session):
    await session.transition("private")
    with pytest.raises(BridgeError, match="private"):
        session.observe()
    await session.transition("agent")
    obs = session.observe()
    session.observation_ttl = -1
    with pytest.raises(BridgeError, match="fresh"):
        async with session.action("late", {"tool": "click"}, obs):
            pytest.fail()


@pytest.mark.parametrize("mode", ["paused", "human", "private", "stopped", "ready"])
async def test_all_nonagent_states_block(session, mode):
    if mode != "ready":
        await session.transition(mode)
    with pytest.raises(BridgeError):
        async with session.action("write", {}):
            pytest.fail()
