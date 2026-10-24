"""Bounded RFB input routing for the owner's shared desktop.

The browser's normal stream goes to the read-only VNC listener. Only decoded
input goes to a separate, input-only connection. A raw framebuffer round trip
on that connection orders input processing before the gateway releases its
lease; writing bytes to a TCP socket alone would not establish that ordering.

This is deliberately not a general RFB proxy: the bundled no-auth, loopback
x11vnc servers and noVNC client are the supported peers. Unknown messages fail
closed. No input text, key values, or framebuffer data is logged or persisted;
only currently held key/button state is retained for disconnect recovery.
"""

from __future__ import annotations

import asyncio
import struct

from .state import BridgeError

MAX_MESSAGE = 1_048_576
MAX_ENCODINGS = 2048
MAX_HELD_KEYS = 256
MAX_FRAME_BYTES = 16 * 1024 * 1024
VERSIONS = {b"RFB 003.003\n": 3, b"RFB 003.007\n": 7, b"RFB 003.008\n": 8}


class RFBError(BridgeError):
    def __init__(self, message="Unsupported or malformed desktop stream"):
        super().__init__("DESKTOP_PROTOCOL_ERROR", message)


def _key(keysym: int, down: int) -> bytes:
    return struct.pack(">BB2xI", 4, down, keysym)


def _check_input(packet: bytes) -> None:
    if not packet:
        raise RFBError()
    kind = packet[0]
    if kind == 4 and len(packet) == 8 and packet[1] in {0, 1}:
        return
    if kind == 5 and len(packet) == 6:
        return
    if kind == 6 and 8 <= len(packet) <= MAX_MESSAGE:
        length = int.from_bytes(packet[4:8], "big", signed=True)
        if length == len(packet) - 8:
            return
    raise RFBError("Expected a complete, supported desktop input message")


class ClientMessages:
    """Parse a client stream across arbitrary WebSocket message boundaries.

    ``feed`` returns complete ``(packet, is_input)`` messages in wire order.
    Non-input packets belong on the read-only viewing stream. QEMU key events
    are normalized to ordinary keysym events for the separate input connection.
    A parser that raises must be discarded, not reused after a protocol error.
    """

    def __init__(self):
        self.buffer = bytearray()
        self.stage = "version"

    def feed(self, data: bytes) -> list[tuple[bytes, bool]]:
        if len(data) > MAX_MESSAGE or len(self.buffer) + len(data) > 2 * MAX_MESSAGE:
            raise RFBError("Desktop message is too large")
        self.buffer.extend(data)
        result = []
        offset = 0
        while offset < len(self.buffer):
            remaining = len(self.buffer) - offset
            if self.stage == "version":
                if remaining < 12:
                    break
                packet = bytes(self.buffer[offset:offset + 12])
                if packet not in VERSIONS:
                    raise RFBError("Only RFB 3.3, 3.7, and 3.8 are supported")
                self.stage = "init" if VERSIONS[packet] == 3 else "security"
                result.append((packet, False))
                offset += 12
                continue
            if self.stage in {"security", "init"}:
                value = self.buffer[offset]
                if self.stage == "security":
                    if value != 1:
                        raise RFBError("Only the configured loopback no-auth server is supported")
                    self.stage = "init"
                else:
                    if value not in {0, 1}:
                        raise RFBError("Invalid RFB ClientInit")
                    self.stage = "messages"
                # The viewing stream must never disconnect other VNC clients.
                result.append((b"\x01", False))
                offset += 1
                continue

            kind = self.buffer[offset]
            if kind in {0, 3, 4, 5, 150}:
                size = {0: 20, 3: 10, 4: 8, 5: 6, 150: 10}[kind]
            elif kind == 2:
                if remaining < 4:
                    break
                count = int.from_bytes(self.buffer[offset + 2:offset + 4], "big")
                if count > MAX_ENCODINGS:
                    raise RFBError("Too many desktop encodings")
                size = 4 + 4 * count
            elif kind == 6:
                if remaining < 8:
                    break
                length = int.from_bytes(
                    self.buffer[offset + 4:offset + 8], "big", signed=True
                )
                size = 8 + abs(length)
                if size > MAX_MESSAGE:
                    raise RFBError("Desktop clipboard message is too large")
            elif kind == 248:
                if remaining < 9:
                    break
                length = self.buffer[offset + 8]
                if length > 64:
                    raise RFBError("Desktop fence payload is too large")
                size = 9 + length
            elif kind == 255:
                if remaining < 2:
                    break
                if self.buffer[offset + 1] != 0:
                    raise RFBError("Unsupported QEMU desktop extension")
                size = 12
            else:
                # Includes desktop resize, power operations, and file transfer.
                raise RFBError("Unsupported desktop message; reconnect to the shared viewer")
            if remaining < size:
                break
            packet = bytes(self.buffer[offset:offset + size])
            offset += size
            is_input = kind in {4, 5, 6, 255}
            if kind == 255:
                down = int.from_bytes(packet[2:4], "big")
                if down not in {0, 1}:
                    raise RFBError("Invalid desktop key state")
                packet = _key(int.from_bytes(packet[4:8], "big"), down)
            elif kind == 6 and int.from_bytes(packet[4:8], "big", signed=True) < 0:
                self._clipboard_transport(packet)
                is_input = False
            if is_input:
                _check_input(packet)
            result.append((packet, is_input))
        del self.buffer[:offset]
        if len(self.buffer) > MAX_MESSAGE:
            raise RFBError("Desktop message is too large")
        return result

    @staticmethod
    def _clipboard_transport(packet: bytes) -> None:
        if len(packet) < 12:
            raise RFBError("Invalid extended clipboard message")
        flags = int.from_bytes(packet[8:12], "big")
        caps, request, peek, notify, provide = (1 << bit for bit in range(24, 29))
        if flags & ~0x1F00FFFF:
            raise RFBError("Unsupported extended clipboard flags")
        actions = flags & 0x1F000000
        if actions & caps:
            # Capabilities advertise several actions, including Provide; that
            # does not itself transmit clipboard contents or represent typing.
            if len(packet) != 12 + 4 * (flags & 0xFFFF).bit_count():
                raise RFBError("Invalid extended clipboard capabilities")
        elif actions == provide:
            raise RFBError(
                "Extended clipboard paste is not supported in the shared viewer; "
                "use keyboard input instead"
            )
        elif actions not in {request, peek, notify} or len(packet) != 12:
            raise RFBError("Unsupported extended clipboard message")


class HeldInput:
    """Track a viewer's potentially delivered keys/buttons, never input text.

    Update before attempting delivery. On an uncertain send this conservatively
    retains the releases needed for recovery. Call ``acknowledge`` after each
    successful send/barrier. ``release_packets`` does not clear state: call
    ``clear`` only after those releases have been acknowledged.
    """

    def __init__(self):
        self.keys: set[int] = set()
        self.releasing_keys: set[int] = set()
        self.buttons = 0
        self.releasing_buttons = 0
        self.x = self.y = 0

    @property
    def active(self) -> bool:
        return bool(self.keys or self.releasing_keys or self.buttons or self.releasing_buttons)

    def update(self, packet: bytes) -> None:
        _check_input(packet)
        if packet[0] == 4:
            keysym = int.from_bytes(packet[4:8], "big")
            if packet[1]:
                if (keysym not in self.keys | self.releasing_keys
                        and len(self.keys | self.releasing_keys) >= MAX_HELD_KEYS):
                    raise RFBError("Too many held desktop keys")
                self.keys.add(keysym)
            else:
                if keysym in self.keys:
                    self.releasing_keys.add(keysym)
                self.keys.discard(keysym)
        elif packet[0] == 5:
            self.releasing_buttons |= self.buttons
            _, self.buttons, self.x, self.y = struct.unpack(">BBHH", packet)

    def acknowledge(self) -> None:
        self.releasing_keys.clear()
        self.releasing_buttons = 0

    def release_packets(self) -> list[bytes]:
        packets = []
        if self.buttons or self.releasing_buttons:
            packets.append(struct.pack(">BBHH", 5, 0, self.x, self.y))
        packets.extend(_key(key, 0) for key in sorted(self.keys | self.releasing_keys))
        return packets

    def clear(self) -> None:
        self.keys.clear()
        self.buttons = 0
        self.acknowledge()


class InputConnection:
    """Input-only, raw-encoding RFB connection with an explicit round trip.

    The caller owns serialization, revocation, and held-input recovery. Never
    retry a failed batch: it may already have taken effect. A failed/closed
    instance cannot reconnect; use a new instance only for explicit recovery.
    """

    def __init__(self, host="127.0.0.1", port=5900, *, timeout=5):
        self.host, self.port, self.timeout = host, port, timeout
        self.reader = self.writer = None
        self.width = self.height = 0
        self.closed = False

    async def send(self, packets: list[bytes], *, guard=None) -> None:
        if self.closed:
            raise RFBError("Desktop input connection is closed; input outcome may be unknown")
        if not packets:
            return
        for packet in packets:
            _check_input(packet)
        if sum(map(len, packets)) > MAX_MESSAGE:
            raise RFBError("Desktop input batch is too large")
        try:
            async with asyncio.timeout(self.timeout):
                if self.writer is None:
                    await self._connect()
                # A lazy handshake awaits network I/O. Authentication may have
                # expired or been revoked during it, so recheck immediately
                # before the first input byte, with no intervening await.
                if guard is not None:
                    guard()
                self.writer.writelines(packets)
                # No other request is ever outstanding on this connection.
                self.writer.write(struct.pack(">BBHHHH", 3, 0, 0, 0, 1, 1))
                await self.writer.drain()
                await self._framebuffer_barrier()
        except BaseException as error:
            await self.close()
            if isinstance(error, (OSError, EOFError, TimeoutError)):
                raise RFBError(
                    "Desktop input was not acknowledged; its outcome may be unknown"
                ) from error
            raise

    async def _read(self, size: int) -> bytes:
        return await self.reader.readexactly(size)

    async def _connect(self) -> None:
        self.reader, self.writer = await asyncio.open_connection(self.host, self.port)
        version = await self._read(12)
        if version not in VERSIONS:
            raise RFBError("Unsupported loopback desktop protocol version")
        self.writer.write(version)
        await self.writer.drain()
        if VERSIONS[version] == 3:
            if int.from_bytes(await self._read(4), "big") != 1:
                raise RFBError("Unexpected loopback desktop security configuration")
        else:
            count = (await self._read(1))[0]
            if not count or 1 not in await self._read(count):
                raise RFBError("Loopback desktop did not offer no-auth security")
            self.writer.write(b"\x01")
            await self.writer.drain()
            if VERSIONS[version] == 8 and await self._read(4) != b"\0\0\0\0":
                raise RFBError("Loopback desktop security negotiation failed")
        self.writer.write(b"\x01")  # Shared: do not evict the agent's VNC client.
        await self.writer.drain()
        init = await self._read(24)
        self.width, self.height = struct.unpack(">HH", init[:4])
        name_length = int.from_bytes(init[20:24], "big")
        if not (0 < self.width <= 16384 and 0 < self.height <= 16384) or name_length > 4096:
            raise RFBError("Invalid loopback desktop initialization")
        await self._read(name_length)
        # Explicit 32-bit true color and raw only: the barrier never needs to
        # parse compressed pixels, cursor pseudo-encodings, or desktop resize.
        self.writer.write(b"\0\0\0\0" + struct.pack(
            ">BBBBHHHBBB3x", 32, 24, 0, 1, 255, 255, 255, 16, 8, 0
        ))
        self.writer.write(struct.pack(">BBHi", 2, 0, 1, 0))
        await self.writer.drain()

    async def _discard(self, size: int) -> None:
        while size:
            chunk = min(size, 65536)
            await self._read(chunk)
            size -= chunk

    async def _framebuffer_barrier(self) -> None:
        for _ in range(128):
            kind = (await self._read(1))[0]
            if kind == 0:
                count = int.from_bytes((await self._read(3))[1:3], "big")
                if not 0 < count <= 1024:
                    raise RFBError("Invalid desktop framebuffer acknowledgement")
                total = 0
                covers_request = False
                for _ in range(count):
                    x, y, width, height, encoding = struct.unpack(">HHHHi", await self._read(12))
                    size = width * height * 4
                    total += size
                    if (encoding != 0 or not width or not height
                            or x + width > self.width or y + height > self.height
                            or total > MAX_FRAME_BYTES):
                        raise RFBError("Unexpected desktop framebuffer acknowledgement")
                    covers_request |= x == 0 and y == 0
                    await self._discard(size)
                if not covers_request:
                    raise RFBError("Desktop framebuffer did not acknowledge the requested area")
                return
            if kind == 1:  # Color-map entries, never application input.
                count = int.from_bytes((await self._read(5))[3:5], "big")
                await self._discard(count * 6)
            elif kind == 2:  # Bell.
                continue
            elif kind == 3:  # Ignore unsolicited clipboard data; do not retain it.
                length = abs(int.from_bytes((await self._read(7))[3:7], "big", signed=True))
                if length > MAX_MESSAGE:
                    raise RFBError("Desktop clipboard response is too large")
                await self._discard(length)
            else:
                raise RFBError("Unexpected loopback desktop response")
        raise RFBError("Too many desktop messages before input acknowledgement")

    async def close(self) -> None:
        self.closed = True
        writer, self.writer = self.writer, None
        if writer is not None:
            writer.close()
            try:
                await asyncio.wait_for(writer.wait_closed(), timeout=1)
            except (OSError, TimeoutError):
                pass
