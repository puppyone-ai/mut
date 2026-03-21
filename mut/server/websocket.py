"""Minimal WebSocket implementation for Mut server notifications.

Pure-Python, zero external dependencies. Implements just enough of
RFC 6455 to support server→client JSON push notifications.

Protocol:
  1. Client sends HTTP Upgrade request to /ws with Authorization header
  2. Server validates credential, sends 101 Switching Protocols
  3. Server pushes JSON notification frames to client
  4. Client sends text frames (pong, ack) back
  5. Either side can close the connection

This is intentionally minimal — production deployments (PuppyOne)
will use their own WebSocket infrastructure.
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import struct
from dataclasses import dataclass, field

_WS_MAGIC = b"258EAFA5-E914-47DA-95CA-5AB4FC00C857"
_MAX_WS_FRAME_SIZE = 1 * 1024 * 1024  # 1 MB max frame


def _accept_key(ws_key: str) -> str:
    """Compute Sec-WebSocket-Accept from client's Sec-WebSocket-Key."""
    digest = hashlib.sha1(ws_key.encode() + _WS_MAGIC).digest()
    return base64.b64encode(digest).decode()


async def _send_ws_frame(writer: asyncio.StreamWriter,
                         payload: bytes, opcode: int = 0x1) -> None:
    """Send a WebSocket frame (text=0x1, close=0x8, ping=0x9)."""
    length = len(payload)
    header = bytes([0x80 | opcode])
    if length < 126:
        header += bytes([length])
    elif length < 65536:
        header += bytes([126]) + struct.pack("!H", length)
    else:
        header += bytes([127]) + struct.pack("!Q", length)
    writer.write(header + payload)
    await writer.drain()


async def _read_ws_frame(reader: asyncio.StreamReader) -> tuple[int, bytes] | None:
    """Read a WebSocket frame. Returns (opcode, payload) or None on EOF."""
    try:
        head = await reader.readexactly(2)
    except (asyncio.IncompleteReadError, ConnectionError):
        return None

    opcode = head[0] & 0x0F
    masked = bool(head[1] & 0x80)
    length = head[1] & 0x7F

    if length == 126:
        length = struct.unpack("!H", await reader.readexactly(2))[0]
    elif length == 127:
        length = struct.unpack("!Q", await reader.readexactly(8))[0]

    if length > _MAX_WS_FRAME_SIZE:
        return None  # reject oversized frames

    mask = await reader.readexactly(4) if masked else None
    data = await reader.readexactly(length)

    if mask:
        data = bytes(b ^ mask[i % 4] for i, b in enumerate(data))

    return opcode, data


async def do_ws_handshake(writer: asyncio.StreamWriter,
                          headers: dict) -> bool:
    """Complete WebSocket handshake. Returns True on success."""
    ws_key = headers.get("sec-websocket-key", "")
    if not ws_key:
        return False

    accept = _accept_key(ws_key)
    response = (
        "HTTP/1.1 101 Switching Protocols\r\n"
        "Upgrade: websocket\r\n"
        "Connection: Upgrade\r\n"
        f"Sec-WebSocket-Accept: {accept}\r\n"
        "\r\n"
    ).encode()
    writer.write(response)
    await writer.drain()
    return True


@dataclass
class WebSocketClient:
    """A connected WebSocket client."""
    client_id: str
    scope_path: str
    writer: asyncio.StreamWriter
    reader: asyncio.StreamReader
    _closed: bool = False
    seen_ids: set = field(default_factory=set)

    async def send_json(self, data: dict) -> bool:
        """Send a JSON message. Returns False if connection is broken."""
        if self._closed:
            return False
        try:
            payload = json.dumps(data, ensure_ascii=False).encode()
            await _send_ws_frame(self.writer, payload)
            return True
        except OSError:
            self._closed = True
            return False

    async def close(self):
        if not self._closed:
            self._closed = True
            try:
                await _send_ws_frame(self.writer, b"", opcode=0x8)
                self.writer.close()
                await self.writer.wait_closed()
            except OSError:
                pass

    @property
    def is_closed(self) -> bool:
        return self._closed


class WebSocketManager:
    """Manages WebSocket client connections and notification delivery."""

    MAX_OFFLINE_PER_CLIENT = 500

    def __init__(self):
        self._clients: dict[str, WebSocketClient] = {}
        self._offline_queue: dict[str, list[dict]] = {}

    def register(self, client: WebSocketClient) -> None:
        self._clients[client.client_id] = client

    def unregister(self, client_id: str) -> None:
        self._clients.pop(client_id, None)

    @property
    def connected_clients(self) -> list[str]:
        return list(self._clients.keys())

    async def broadcast(self, notification: dict, exclude: str = "",
                        scope_path: str = "") -> dict:
        """Send notification to all connected clients with overlapping scope.

        Clients whose scope overlaps with scope_path get notified.
        Returns {"sent": [...], "queued": [...]}.
        """
        from mut.server.sync_queue import _paths_overlap
        from mut.foundation.config import normalize_path

        norm_scope = normalize_path(scope_path)
        sent = []
        queued = []

        for client_id, client in list(self._clients.items()):
            if client_id == exclude:
                continue
            # Only notify clients whose scope overlaps
            client_scope = normalize_path(client.scope_path)
            if norm_scope and client_scope and not _paths_overlap(norm_scope, client_scope):
                continue

            if client.is_closed:
                self.unregister(client_id)
                self._queue_offline(client_id, notification)
                queued.append(client_id)
                continue

            nid = notification.get("notification_id", "")
            if nid in client.seen_ids:
                continue

            ok = await client.send_json(notification)
            if ok:
                client.seen_ids.add(nid)
                sent.append(client_id)
            else:
                self.unregister(client_id)
                self._queue_offline(client_id, notification)
                queued.append(client_id)

        return {"sent": sent, "queued": queued}

    def _queue_offline(self, client_id: str, notification: dict) -> None:
        if client_id not in self._offline_queue:
            self._offline_queue[client_id] = []
        q = self._offline_queue[client_id]
        if len(q) >= self.MAX_OFFLINE_PER_CLIENT:
            q.pop(0)  # drop oldest to prevent unbounded growth
        q.append(notification)

    async def flush_offline(self, client: WebSocketClient) -> int:
        """Send queued offline notifications to a newly connected client."""
        pending = self._offline_queue.pop(client.client_id, [])
        count = 0
        for notif in pending:
            if await client.send_json(notif):
                count += 1
        return count

    async def close_all(self):
        for client in list(self._clients.values()):
            await client.close()
        self._clients.clear()
