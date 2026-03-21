"""Client-side notification listener — connects to server via WebSocket.

Maintains a persistent WebSocket connection to the Mut server's /ws
endpoint and receives push notifications in real-time. When offline,
queued notifications are delivered upon reconnection.

Usage:
    listener = NotificationListener(server_url, credential, user_identity)
    listener.on_notification = my_callback
    await listener.connect()   # blocks, reconnects on disconnect
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import struct
from typing import Callable


_WS_MAGIC = b"258EAFA5-E914-47DA-95CA-5AB4FC00C857"


class NotificationListener:
    """Async WebSocket client that receives server push notifications."""

    def __init__(self, server_url: str, credential: str,
                 user_identity: str = "",
                 on_notification: Callable[[dict], None] | None = None):
        self.server_url = server_url.rstrip("/")
        self.credential = credential
        self.user_identity = user_identity
        self.on_notification = on_notification
        self._seen_ids: set[str] = set()
        self._closed = False

    async def connect(self, reconnect: bool = True,
                      reconnect_delay: float = 5.0):
        """Connect to server /ws and listen for notifications.

        If reconnect=True, automatically reconnects on disconnect.
        """
        while not self._closed:
            try:
                await self._connect_once()
            except OSError:
                pass
            if not reconnect or self._closed:
                break
            await asyncio.sleep(reconnect_delay)

    async def _connect_once(self):
        """Single WebSocket connection attempt."""
        from urllib.parse import urlparse
        parsed = urlparse(self.server_url)
        host = parsed.hostname or "127.0.0.1"
        port = parsed.port or (443 if parsed.scheme == "https" else 80)

        reader, writer = await asyncio.open_connection(host, port)

        # Build WebSocket upgrade request
        ws_key = base64.b64encode(b"mut-client-notify-key!").decode()
        headers = (
            f"GET /ws HTTP/1.1\r\n"
            f"Host: {host}:{port}\r\n"
            f"Upgrade: websocket\r\n"
            f"Connection: Upgrade\r\n"
            f"Sec-WebSocket-Key: {ws_key}\r\n"
            f"Sec-WebSocket-Version: 13\r\n"
            f"Authorization: Bearer {self.credential}\r\n"
        )
        if self.user_identity:
            headers += f"X-Mut-User: {self.user_identity}\r\n"
        headers += "\r\n"

        writer.write(headers.encode())
        await writer.drain()

        # Read upgrade response
        response_line = await reader.readline()
        if b"101" not in response_line:
            writer.close()
            raise OSError("WebSocket upgrade failed")

        # Skip remaining headers
        while True:
            line = await reader.readline()
            if line in (b"\r\n", b"\n", b""):
                break

        # Read notification frames
        try:
            while not self._closed:
                frame = await self._read_frame(reader)
                if frame is None:
                    break
                opcode, data = frame
                if opcode == 0x8:  # close
                    break
                if opcode == 0x9:  # ping -> pong
                    await self._send_frame(writer, data, opcode=0xA)
                    continue
                if opcode == 0x1:  # text frame
                    self._handle_message(data)
        finally:
            writer.close()
            try:
                await writer.wait_closed()
            except OSError:
                pass

    def _handle_message(self, data: bytes):
        """Process a received WebSocket text frame."""
        try:
            msg = json.loads(data.decode())
        except (json.JSONDecodeError, UnicodeDecodeError):
            return

        nid = msg.get("notification_id", "")
        if nid and nid in self._seen_ids:
            return  # idempotent: skip duplicates
        if nid:
            self._seen_ids.add(nid)

        if self.on_notification:
            self.on_notification(msg)

    def close(self):
        """Signal the listener to stop."""
        self._closed = True

    @staticmethod
    async def _read_frame(reader) -> tuple[int, bytes] | None:
        try:
            head = await reader.readexactly(2)
        except (asyncio.IncompleteReadError, OSError):
            return None

        opcode = head[0] & 0x0F
        length = head[1] & 0x7F

        if length == 126:
            length = struct.unpack("!H", await reader.readexactly(2))[0]
        elif length == 127:
            length = struct.unpack("!Q", await reader.readexactly(8))[0]

        if length > 1024 * 1024:  # 1MB max
            return None

        # Server frames are NOT masked (only client->server must be masked)
        data = await reader.readexactly(length)
        return opcode, data

    @staticmethod
    async def _send_frame(writer, payload: bytes, opcode: int = 0x1):
        """Send a masked WebSocket frame (client->server must be masked)."""
        import os
        mask = os.urandom(4)
        masked_data = bytes(b ^ mask[i % 4] for i, b in enumerate(payload))

        length = len(payload)
        header = bytes([0x80 | opcode])
        if length < 126:
            header += bytes([0x80 | length])
        elif length < 65536:
            header += bytes([0x80 | 126]) + struct.pack("!H", length)
        else:
            header += bytes([0x80 | 127]) + struct.pack("!Q", length)

        writer.write(header + mask + masked_data)
        await writer.drain()
