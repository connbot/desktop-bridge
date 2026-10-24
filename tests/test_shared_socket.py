"""Exercise the real authenticated shared WebSocket route with fake VNC peers."""

import asyncio

import pytest
from starlette.websockets import WebSocketDisconnect
from test_app import TOKEN, FakeBrowser, FakeCoding, FakeDesktop

import desktop_bridge.app as app_module
from desktop_bridge.app import Runtime, create_app
from desktop_bridge.state import BridgeError

HELLO = b"RFB 003.008\n\x01\x01"
KEY_DOWN = b"\x04\x01\x00\x00\x00\x00\x00a"
KEY_UP = b"\x04\x00\x00\x00\x00\x00\x00a"
FRAME_REQUEST = b"\x03\x01\x00\x00\x00\x00\x00\x01\x00\x01"


class Socket:
    def __init__(self, sid, origin="http://testserver"):
        self.cookies = {"bridge_session": sid}
        self.headers = {"origin": origin}
        self.incoming = asyncio.Queue()
        self.accepted = False
        self.closed = False

    async def accept(self, **_):
        self.accepted = True

    async def close(self, **_):
        self.closed = True

    async def receive_bytes(self):
        packet = await self.incoming.get()
        if packet is None:
            raise WebSocketDisconnect()
        return packet

    async def send_bytes(self, packet):
        pass


class ViewWriter:
    def __init__(self):
        self.packets = []

    def write(self, packet):
        self.packets.append(packet)

    async def drain(self):
        pass

    def close(self):
        pass

    async def wait_closed(self):
        pass


class InputPeer:
    def __init__(self):
        self.batches = []
        self.entered = asyncio.Event()
        self.barrier = asyncio.Event()
        self.barrier.set()
        self.closed = False
        self.fail = False

    async def send(self, packets, *, guard=None):
        if guard:
            guard()
        self.batches.append(packets)
        self.entered.set()
        await self.barrier.wait()
        if self.fail:
            raise OSError("synthetic uncertain input")

    async def close(self):
        self.closed = True


@pytest.fixture
async def fixture(tmp_path, monkeypatch):
    runtime = Runtime(tmp_path, desktop=FakeDesktop(), browser=FakeBrowser(), coding=FakeCoding())
    app = create_app(tmp_path, TOKEN, "http://testserver", runtime)
    sid, _ = app.state.auth.login(TOKEN)
    endpoint = next(route.endpoint for route in app.routes if route.path == "/desktop/{mode}")
    writer, reader = ViewWriter(), asyncio.StreamReader()
    peers = []

    async def open_view(host, port):
        assert host == "127.0.0.1" and port == 5901
        return reader, writer

    def new_input():
        peer = InputPeer()
        peers.append(peer)
        return peer

    monkeypatch.setattr(app_module.asyncio, "open_connection", open_view)
    monkeypatch.setattr(app_module, "InputConnection", new_input)
    await runtime.call("session_start", {})
    yield runtime, app, endpoint, sid, writer, peers
    runtime.session.close()


async def wait_for(predicate):
    async with asyncio.timeout(1):
        while not predicate():  # noqa: ASYNC110 - poll observable state in the socket fixture
            await asyncio.sleep(0)


async def test_viewing_does_not_pause_and_input_never_enters_readonly_stream(fixture):
    runtime, _, endpoint, sid, writer, peers = fixture
    ws = Socket(sid)
    task = asyncio.create_task(endpoint(ws, "shared"))
    await wait_for(lambda: ws.accepted)
    epoch = runtime.session.epoch
    await ws.incoming.put(HELLO + FRAME_REQUEST)
    await wait_for(lambda: writer.packets)
    assert b"".join(writer.packets) == HELLO + FRAME_REQUEST
    assert runtime.session.epoch == epoch
    assert not peers[0].batches
    peers[0].barrier.clear()
    before = runtime.session.observe()
    await ws.incoming.put(KEY_DOWN)
    await peers[0].entered.wait()
    assert runtime.session.human_input_pending
    assert KEY_DOWN not in writer.packets
    assert runtime.session.mode == "agent"
    with pytest.raises(BridgeError):
        runtime.session.require_agent()
    peers[0].barrier.set()
    await wait_for(lambda: not runtime.session.human_input_pending)
    assert runtime.session.human_holds
    await ws.incoming.put(KEY_UP)
    await wait_for(lambda: len(peers[0].batches) == 2 and not runtime.session.human_holds)
    with pytest.raises(BridgeError) as stale:
        async with runtime.session.action("old", {}, before):
            pytest.fail("pre-input observation accepted")
    assert stale.value.code == "STALE_OBSERVATION"
    runtime.session.require_agent()
    await ws.incoming.put(None)
    await task
    assert runtime.session.mode == "agent"
    assert not runtime.session.shared_sockets


async def test_disconnect_releases_held_keys_then_reconnects_without_handoff(fixture):
    runtime, _, endpoint, sid, _, peers = fixture
    for _ in range(2):
        ws = Socket(sid)
        task = asyncio.create_task(endpoint(ws, "shared"))
        await ws.incoming.put(HELLO + KEY_DOWN)
        await wait_for(lambda: runtime.session.human_holds and not runtime.session.human_input_pending)
        await ws.incoming.put(None)
        await task
        assert peers[-1].batches == [[KEY_DOWN], [KEY_UP]]
        assert not runtime.session.human_holds
        assert not runtime.pending_releases
        assert runtime.session.mode == "agent"
        runtime.session.require_agent()


@pytest.mark.parametrize("private", [False, True])
async def test_failed_release_stays_blocked_until_explicit_resume_barrier(fixture, private):
    runtime, _, endpoint, sid, _, peers = fixture
    if private:
        await runtime.control("private")
    ws = Socket(sid)
    task = asyncio.create_task(endpoint(ws, "shared"))
    await ws.incoming.put(HELLO + KEY_DOWN)
    await wait_for(lambda: runtime.session.human_holds and not runtime.session.human_input_pending)
    peers[-1].fail = True
    await ws.incoming.put(None)
    await task
    assert runtime.session.mode == ("private" if private else "paused")
    assert runtime.pending_releases
    assert runtime.session.human_holds
    await runtime.control("agent")
    assert peers[-1].batches == [[KEY_UP]]
    assert not runtime.pending_releases
    assert not runtime.session.human_holds
    runtime.session.require_agent()


@pytest.mark.parametrize("sid,origin", [("invalid", "http://testserver"), (None, "https://evil.example")])
async def test_shared_socket_requires_owner_cookie_and_exact_origin(fixture, sid, origin):
    _, _, endpoint, valid_sid, _, peers = fixture
    ws = Socket(sid or valid_sid, origin)
    await endpoint(ws, "shared")
    assert not ws.accepted
    assert ws.closed
    assert not peers


async def test_uncertain_pointer_input_blocks_before_input_lock_is_released(fixture):
    runtime, _, endpoint, sid, _, peers = fixture
    ws = Socket(sid)
    task = asyncio.create_task(endpoint(ws, "shared"))
    await wait_for(lambda: peers)
    peers[0].fail = True
    pointer = b"\x05\x00\x00\x05\x00\x05"
    await ws.incoming.put(HELLO + pointer)
    await peers[0].entered.wait()
    await wait_for(lambda: not runtime.session.human_input_pending)
    assert runtime.session.mode == "paused"
    with pytest.raises(BridgeError):
        runtime.session.require_agent()
    await task


async def test_new_private_request_wins_over_pending_resume_recovery(fixture):
    runtime, _, _, _, _, peers = fixture
    await runtime.control("paused")
    runtime.pending_releases["lost viewer"] = [KEY_UP]
    runtime.session.human_holds.add("lost viewer")
    # Delay recovery at the lock so a later private request revokes the resume.
    await runtime.session.lock.acquire()
    resume = asyncio.create_task(runtime.control("agent"))
    await asyncio.sleep(0)
    private = asyncio.create_task(runtime.control("private"))
    await asyncio.sleep(0)
    assert runtime.session.mode == "private"
    runtime.session.lock.release()
    await asyncio.gather(resume, private)
    assert peers[-1].batches == [[KEY_UP]]
    assert runtime.session.mode == "private"
    with pytest.raises(BridgeError) as hidden:
        runtime.session.observe()
    assert hidden.value.code == "PRIVATE_TAKEOVER"


async def test_owner_revoked_during_real_auxiliary_handshake_sends_no_original_input(fixture, monkeypatch):
    from desktop_bridge.shared_input import InputConnection

    runtime, app, endpoint, sid, _, _ = fixture
    entered, finish = asyncio.Event(), asyncio.Event()
    written = []

    class Writer(ViewWriter):
        def writelines(self, packets):
            written.extend(packets)

    async def connect(connection):
        entered.set()
        await finish.wait()
        connection.writer = Writer()

    monkeypatch.setattr(InputConnection, "_connect", connect)
    monkeypatch.setattr(app_module, "InputConnection", InputConnection)
    ws = Socket(sid)
    task = asyncio.create_task(endpoint(ws, "shared"))
    await ws.incoming.put(HELLO + KEY_DOWN)
    await entered.wait()
    app.state.auth.revoke()
    finish.set()
    await task
    assert not written
    assert runtime.session.mode == "paused"
    assert runtime.pending_releases  # Only idempotent releases may be recovered.
    assert ws.closed
