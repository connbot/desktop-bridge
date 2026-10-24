"""Synthetic RFB peer tests, not a substitute for the Docker/noVNC acceptance run."""

import asyncio
import struct
from contextlib import asynccontextmanager

import pytest

from desktop_bridge.shared_input import (
    MAX_MESSAGE,
    ClientMessages,
    HeldInput,
    InputConnection,
    RFBError,
)
from desktop_bridge.state import BridgeError


def key(value=65, down=1):
    return struct.pack(">BB2xI", 4, down, value)


def pointer(buttons=0, x=10, y=20):
    return struct.pack(">BBHH", 5, buttons, x, y)


def clipboard(data=b"text"):
    return b"\x06\0\0\0" + struct.pack(">I", len(data)) + data


def extended(flags, data=b""):
    payload = struct.pack(">I", flags) + data
    return b"\x06\0\0\0" + struct.pack(">i", -len(payload)) + payload


def initialized(version=8):
    parser = ClientMessages()
    handshake = f"RFB 003.{version:03}\n".encode() + (b"\x01" if version != 3 else b"") + b"\x01"
    parser.feed(handshake)
    return parser


@pytest.mark.parametrize("version", [3, 7, 8])
def test_handshake_and_coalesced_messages(version):
    parser = ClientMessages()
    handshake = f"RFB 003.{version:03}\n".encode() + (b"\x01" if version != 3 else b"") + b"\x00"
    result = parser.feed(handshake + key() + pointer() + clipboard())
    assert all(not is_input for _, is_input in result[:-3])
    assert result[-4] == (b"\x01", False)  # Never request exclusive viewing.
    assert result[-3:] == [(key(), True), (pointer(), True), (clipboard(), True)]
    assert not parser.buffer


def test_every_byte_fragment_and_two_packets_per_frame():
    wire = b"RFB 003.008\n\x01\x01" + key() + key(down=0) + pointer(1) + pointer()
    parser = ClientMessages()
    output = []
    for byte in wire:
        output.extend(parser.feed(bytes([byte])))
    assert output[-4:] == [(packet, True) for packet in (key(), key(down=0), pointer(1), pointer())]
    for split in range(len(wire) + 1):
        parser = ClientMessages()
        assert parser.feed(wire[:split]) + parser.feed(wire[split:]) == output


def test_observation_transport_is_never_input():
    parser = initialized()
    packets = [
        b"\0" * 20,
        struct.pack(">BBHii", 2, 0, 2, 0, -239),
        struct.pack(">BBHHHH", 3, 1, 0, 0, 1280, 800),
        struct.pack(">BBHHHH", 150, 1, 0, 0, 1280, 800),
        struct.pack(">B3xIB", 248, 0, 4) + b"ping",
        extended(0x1F000001, struct.pack(">I", 0)),  # Capability, not Provide.
        extended(0x02000001),
        extended(0x04000000),
        extended(0x08000001),
    ]
    assert parser.feed(b"".join(packets)) == [(packet, False) for packet in packets]


def test_qemu_key_normalized_to_keysym():
    parser = initialized()
    packet = struct.pack(">BBHII", 255, 0, 1, 0xFFE1, 42)
    assert parser.feed(packet) == [(key(0xFFE1), True)]
    assert parser.feed(struct.pack(">BBHII", 255, 0, 0, 0xFFE1, 42)) == [(key(0xFFE1, 0), True)]


@pytest.mark.parametrize("packet", [
    b"\x07", b"\xfa", b"\xfb", b"\xff\x01", key(down=2),
    struct.pack(">BBHII", 255, 0, 2, 65, 30),
    b"\x02\0\xff\xff",
    b"\x06\0\0\0\x7f\xff\xff\xff",
    b"\x06\0\0\0\x80\0\0\0",
    struct.pack(">B3xIB", 248, 0, 65),
    extended(0), extended(0x20000000), extended(0x01000001),
    extended(0x02000001, b"extra"),
])
def test_unsupported_or_oversized_messages_fail_closed(packet):
    with pytest.raises(RFBError):
        initialized().feed(packet)


def test_extended_clipboard_provide_rejected_without_corrupting_unicode():
    with pytest.raises(RFBError, match="Extended clipboard paste is not supported"):
        initialized().feed(extended(0x10000001, "你好".encode()))


@pytest.mark.parametrize("wire", [b"RFB 003.009\n", b"RFB 003.008\n\x02", b"RFB 003.003\n\x02"])
def test_invalid_handshake(wire):
    with pytest.raises(RFBError):
        ClientMessages().feed(wire)


def test_clipboard_payload_cannot_smuggle_input_headers():
    packet = clipboard(key() + pointer(1) + b"\xff\xfb")
    parser = initialized()
    assert parser.feed(packet[:9]) == []
    assert parser.feed(packet[9:] + key()) == [(packet, True), (key(), True)]


def test_message_limits_apply_before_payload_is_buffered():
    with pytest.raises(RFBError, match="too large"):
        initialized().feed(b"x" * (MAX_MESSAGE + 1))
    parser = initialized()
    header = b"\x06\0\0\0" + struct.pack(">I", MAX_MESSAGE - 8)
    assert parser.feed(header + b"x" * 200) == []
    result = parser.feed(b"x" * (MAX_MESSAGE - 208) + key())
    assert len(result[0][0]) == MAX_MESSAGE
    assert result[1] == (key(), True)
    assert not parser.buffer


def test_held_keys_repeat_and_release_only_after_acknowledgement():
    held = HeldInput()
    held.update(key())
    held.update(key())
    held.acknowledge()
    assert held.active
    assert held.release_packets() == [key(down=0)]
    held.update(key(down=0))
    assert held.active  # Release may not have reached x11vnc yet.
    assert held.release_packets() == [key(down=0)]
    held.acknowledge()
    assert not held.active


def test_pointer_drag_disconnect_and_uncertain_release():
    held = HeldInput()
    held.update(pointer(1))
    held.acknowledge()
    held.update(pointer(1, 100, 200))
    held.acknowledge()
    held.update(pointer(0, 100, 200))
    assert held.active
    assert held.release_packets() == [pointer(0, 100, 200)]
    held.clear()
    assert not held.active
    assert held.release_packets() == []


def test_held_input_batch_and_key_bound():
    held = HeldInput()
    held.update(key(0xFFE3))
    held.update(pointer(1))
    held.update(key())
    held.update(key(down=0))
    assert held.release_packets() == [pointer(), key(down=0), key(0xFFE3, 0)]
    held.acknowledge()
    assert held.release_packets() == [pointer(), key(0xFFE3, 0)]
    held.clear()
    for value in range(256):
        held.update(key(value))
    with pytest.raises(RFBError, match="Too many"):
        held.update(key(256))


PIXEL_FORMAT = struct.pack(">BBBBHHHBBB3x", 32, 24, 0, 1, 255, 255, 255, 16, 8, 0)
UPDATE_REQUEST = struct.pack(">BBHHHH", 3, 0, 0, 0, 1, 1)
RAW_UPDATE = b"\0\0\0\x01" + struct.pack(">HHHHi", 0, 0, 1, 1, 0) + b"\0\0\0\0"


@asynccontextmanager
async def peer(handler):
    failures, tasks = [], []

    async def serve(reader, writer):
        try:
            await handler(reader, writer)
        except BaseException as error:
            failures.append(error)
        finally:
            writer.close()
            await writer.wait_closed()

    def connected(reader, writer):
        tasks.append(asyncio.create_task(serve(reader, writer)))

    server = await asyncio.start_server(connected, "127.0.0.1", 0)
    try:
        yield server.sockets[0].getsockname()[1]
    finally:
        server.close()
        await server.wait_closed()
        for task in tasks:
            if not task.done():
                task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        for error in failures:
            if not isinstance(error, asyncio.CancelledError):
                raise error


async def handshake(reader, writer, version=8, *, before_init=None):
    banner = f"RFB 003.{version:03}\n".encode()
    writer.write(banner)
    await writer.drain()
    assert await reader.readexactly(12) == banner
    if version == 3:
        writer.write(b"\0\0\0\x01")
        await writer.drain()
    else:
        writer.write(b"\x01\x01")
        await writer.drain()
        assert await reader.readexactly(1) == b"\x01"
        if version == 8:
            writer.write(b"\0\0\0\0")
            await writer.drain()
    assert await reader.readexactly(1) == b"\x01"
    if before_init is not None:
        await before_init()
    writer.write(struct.pack(">HH", 1280, 800) + PIXEL_FORMAT + struct.pack(">I", 4) + b"test")
    await writer.drain()
    assert await reader.readexactly(20) == b"\0\0\0\0" + PIXEL_FORMAT
    assert await reader.readexactly(8) == struct.pack(">BBHi", 2, 0, 1, 0)


@pytest.mark.parametrize("version", [3, 7, 8])
async def test_input_barrier_waits_for_same_connection_frame(version):
    input_received, allow_reply = asyncio.Event(), asyncio.Event()

    async def handler(reader, writer):
        await handshake(reader, writer, version)
        assert await reader.readexactly(14) == key() + pointer(1)
        assert await reader.readexactly(10) == UPDATE_REQUEST
        input_received.set()
        await allow_reply.wait()
        writer.write(RAW_UPDATE)
        await writer.drain()
        assert await reader.read() == b""

    async with peer(handler) as port:
        connection = InputConnection(port=port)
        task = asyncio.create_task(connection.send([key(), pointer(1)]))
        await input_received.wait()
        assert not task.done()
        allow_reply.set()
        await task
        await connection.close()


async def test_input_connection_reused_and_ignores_bell_clipboard_colormap():
    batches = [[key()], [key(down=0), clipboard()]]

    async def handler(reader, writer):
        await handshake(reader, writer)
        for batch in batches:
            expected = b"".join(batch)
            assert await reader.readexactly(len(expected)) == expected
            assert await reader.readexactly(10) == UPDATE_REQUEST
            writer.write(b"\x02" + b"\x03\0\0\0\0\0\0\x04text")
            writer.write(b"\x01\0\0\0\0\x01" + b"\0" * 6)
            for byte in RAW_UPDATE:
                writer.write(bytes([byte]))
                await writer.drain()
            await writer.drain()
        assert await reader.read() == b""

    async with peer(handler) as port:
        connection = InputConnection(port=port)
        for batch in batches:
            await connection.send(batch)
        await connection.close()


@pytest.mark.parametrize("response", [
    b"", b"\xfa", b"\0\0\0\0",  # EOF, unknown type, empty frame.
    b"\0\0\0\x01" + struct.pack(">HHHHi", 0, 0, 1, 1, 7),  # Compression not negotiated.
    b"\0\0\0\x01" + struct.pack(">HHHHi", 0, 0, 65535, 65535, 0),
    b"\0\0\0\x01" + struct.pack(">HHHHi", 2, 2, 1, 1, 0) + b"\0" * 4,
    b"\x03\0\0\0\x7f\xff\xff\xff",
    b"\x02" * 128,
])
async def test_uncertain_input_never_retries_or_reconnects(response):
    calls = []

    async def handler(reader, writer):
        await handshake(reader, writer)
        calls.append(await reader.readexactly(8))
        assert await reader.readexactly(10) == UPDATE_REQUEST
        writer.write(response)
        await writer.drain()

    async with peer(handler) as port:
        connection = InputConnection(port=port)
        with pytest.raises(RFBError):
            await connection.send([key()])
        assert connection.closed
        with pytest.raises(RFBError, match="closed"):
            await connection.send([key()])
        assert calls == [key()]


async def test_timeout_is_unknown_and_does_not_replay():
    received = asyncio.Event()

    async def handler(reader, writer):
        await handshake(reader, writer)
        assert await reader.readexactly(18) == key() + UPDATE_REQUEST
        received.set()
        assert await reader.read() == b""

    async with peer(handler) as port:
        connection = InputConnection(port=port, timeout=0.05)
        with pytest.raises(RFBError, match="outcome may be unknown"):
            await connection.send([key()])
        assert received.is_set()
        assert connection.closed


async def test_empty_batch_does_not_open_connection_and_only_input_is_accepted():
    connection = InputConnection(port=1)
    await connection.send([])
    assert connection.writer is None
    for packet in [b"", UPDATE_REQUEST, b"\xfa\0\x01\x02", extended(0x10000001)]:
        with pytest.raises(RFBError):
            await connection.send([packet])
    assert connection.writer is None
    await connection.close()


@pytest.mark.parametrize("failure", ["version", "types", "security", "dimensions", "name"])
async def test_bad_server_handshake_never_receives_input(failure):
    remainder = []

    async def handler(reader, writer):
        writer.write(b"RFB 003.009\n" if failure == "version" else b"RFB 003.008\n")
        await writer.drain()
        if failure != "version":
            assert await reader.readexactly(12) == b"RFB 003.008\n"
            writer.write(b"\x01\x02" if failure == "types" else b"\x01\x01")
            await writer.drain()
            if failure != "types":
                assert await reader.readexactly(1) == b"\x01"
                writer.write(b"\0\0\0\x01" if failure == "security" else b"\0\0\0\0")
                await writer.drain()
                if failure != "security":
                    assert await reader.readexactly(1) == b"\x01"
                    width = 0 if failure == "dimensions" else 1280
                    name_length = 4097 if failure == "name" else 0
                    writer.write(struct.pack(">HH", width, 800) + PIXEL_FORMAT
                                 + struct.pack(">I", name_length))
                    await writer.drain()
        remainder.append(await reader.read())

    async with peer(handler) as port:
        connection = InputConnection(port=port)
        with pytest.raises(RFBError):
            await connection.send([key()])
        assert connection.closed
        await asyncio.sleep(0)
    assert remainder == [b""]


async def test_cancelled_input_connection_closes_and_does_not_replay():
    received = asyncio.Event()

    async def handler(reader, writer):
        await handshake(reader, writer)
        assert await reader.readexactly(18) == key() + UPDATE_REQUEST
        received.set()
        assert await reader.read() == b""

    async with peer(handler) as port:
        connection = InputConnection(port=port)
        send = asyncio.create_task(connection.send([key()]))
        await received.wait()
        send.cancel()
        with pytest.raises(asyncio.CancelledError):
            await send
        assert connection.closed
        with pytest.raises(RFBError, match="closed"):
            await connection.send([key()])


async def test_guard_rechecks_revocation_after_handshake_before_any_input():
    negotiating, continue_handshake = asyncio.Event(), asyncio.Event()
    authorized, guards, remainder = True, [], []

    async def before_init():
        negotiating.set()
        await continue_handshake.wait()

    async def handler(reader, writer):
        await handshake(reader, writer, before_init=before_init)
        remainder.append(await reader.read())

    def guard():
        guards.append(authorized)
        if not authorized:
            raise BridgeError("UNAUTHORIZED", "Owner session revoked")

    async with peer(handler) as port:
        connection = InputConnection(port=port)
        task = asyncio.create_task(connection.send([key()], guard=guard))
        await negotiating.wait()
        assert guards == []
        authorized = False
        continue_handshake.set()
        with pytest.raises(BridgeError, match="Owner session revoked"):
            await task
        assert connection.closed
        await asyncio.sleep(0)
    assert guards == [False]
    assert remainder == [b""]  # Neither the input nor its barrier was written.


async def test_guard_also_checks_each_batch_on_existing_connection():
    remainder = []

    async def handler(reader, writer):
        await handshake(reader, writer)
        assert await reader.readexactly(18) == key() + UPDATE_REQUEST
        writer.write(RAW_UPDATE)
        await writer.drain()
        remainder.append(await reader.read())

    def deny():
        raise BridgeError("UNAUTHORIZED", "Owner session revoked")

    async with peer(handler) as port:
        connection = InputConnection(port=port)
        await connection.send([key()], guard=lambda: None)
        with pytest.raises(BridgeError, match="Owner session revoked"):
            await connection.send([key(down=0)], guard=deny)
        await asyncio.sleep(0)
    assert remainder == [b""]
