"""Tests for notification system — online push, offline queue, reconnect flush."""

import asyncio
import pytest

from mut.server.notification import (
    NotificationManager, InMemoryNotificationSink, Notification,
)
from mut.server.websocket import WebSocketManager, WebSocketClient


def _run(coro):
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


# ── NotificationManager ────────────────────────────────────────

class TestNotificationManager:
    def test_create_notification(self):
        mgr = NotificationManager("test-repo")
        notif = mgr.create_notification(
            "/docs/", 5, "alice",
            [{"path": "docs/README.md", "action": "update"}],
        )
        assert notif.repo == "test-repo"
        assert notif.scope == "/docs/"
        assert notif.version == 5
        assert notif.pushed_by == "alice"
        assert "docs/README.md" in notif.changed_files
        assert notif.notification_id  # non-empty UUID

    def test_notification_to_dict(self):
        mgr = NotificationManager("test-repo")
        notif = mgr.create_notification("/", 1, "bob", [])
        d = notif.to_dict()
        assert d["type"] == "version_update"
        assert d["repo"] == "test-repo"

    def test_notify_excludes_pusher(self):
        sink = InMemoryNotificationSink()
        mgr = NotificationManager("repo", sink=sink)

        result = _run(mgr.notify_after_push(
            "/docs/", 1, "alice",
            [{"path": "docs/a.txt", "action": "add"}],
            client_ids=["alice", "bob", "charlie"],
        ))

        assert "alice" not in result["sent"]
        assert "bob" in result["sent"]
        assert "charlie" in result["sent"]

    def test_notify_empty_client_list(self):
        mgr = NotificationManager("repo")
        result = _run(mgr.notify_after_push("/", 1, "alice", [], client_ids=[]))
        assert result["sent"] == []
        assert result["queued"] == []


# ── InMemoryNotificationSink ──────────────────────────────────

class TestInMemorySink:
    def test_send_and_retrieve(self):
        sink = InMemoryNotificationSink()
        notif = Notification(
            notification_id="n1", repo="r", scope="/", version=1,
            pushed_by="a", changed_files=[], timestamp="t",
        )
        _run(sink.send("bob", notif))

        pending = sink.get_pending("bob")
        assert len(pending) == 1
        assert pending[0]["notification_id"] == "n1"

        # Second call returns empty (cleared)
        assert sink.get_pending("bob") == []

    def test_peek_does_not_clear(self):
        sink = InMemoryNotificationSink()
        notif = Notification(
            notification_id="n2", repo="r", scope="/", version=2,
            pushed_by="a", changed_files=[], timestamp="t",
        )
        _run(sink.send("bob", notif))

        peeked = sink.peek_pending("bob")
        assert len(peeked) == 1
        # Still there after peek
        assert len(sink.peek_pending("bob")) == 1

    def test_multiple_notifications_queue(self):
        sink = InMemoryNotificationSink()
        for i in range(5):
            notif = Notification(
                notification_id=f"n{i}", repo="r", scope="/", version=i,
                pushed_by="a", changed_files=[], timestamp="t",
            )
            _run(sink.send("bob", notif))

        pending = sink.get_pending("bob")
        assert len(pending) == 5


# ── WebSocketManager ──────────────────────────────────────────

class _FakeWriter:
    """Minimal fake asyncio.StreamWriter for testing."""
    def __init__(self, fail=False):
        self.written = []
        self.fail = fail
        self.closed = False

    def write(self, data):
        if self.fail:
            raise ConnectionError("fake disconnect")
        self.written.append(data)

    async def drain(self):
        if self.fail:
            raise ConnectionError("fake disconnect")

    def close(self):
        self.closed = True

    async def wait_closed(self):
        pass


class _FakeReader:
    pass


class TestWebSocketManager:
    def _make_client(self, client_id, scope_path, fail=False):
        writer = _FakeWriter(fail=fail)
        reader = _FakeReader()
        return WebSocketClient(
            client_id=client_id, scope_path=scope_path,
            writer=writer, reader=reader,
        )

    def test_register_and_broadcast(self):
        mgr = WebSocketManager()
        c1 = self._make_client("alice", "/docs/")
        c2 = self._make_client("bob", "/docs/")
        mgr.register(c1)
        mgr.register(c2)

        notif = {"notification_id": "n1", "type": "version_update"}
        result = _run(mgr.broadcast(notif, exclude="alice", scope_path="/docs/"))
        assert "bob" in result["sent"]
        assert "alice" not in result["sent"]

    def test_broadcast_scope_filtering(self):
        """Only clients with overlapping scope get notified."""
        mgr = WebSocketManager()
        c_docs = self._make_client("alice", "/docs/")
        c_src = self._make_client("bob", "/src/")
        mgr.register(c_docs)
        mgr.register(c_src)

        notif = {"notification_id": "n2", "type": "version_update"}
        result = _run(mgr.broadcast(notif, scope_path="/docs/"))

        assert "alice" in result["sent"]
        assert "bob" not in result["sent"]
        assert "bob" not in result["queued"]

    def test_offline_client_gets_queued(self):
        """Disconnected client notifications go to offline queue."""
        mgr = WebSocketManager()
        c = self._make_client("alice", "/docs/", fail=True)
        mgr.register(c)

        notif = {"notification_id": "n3", "type": "version_update"}
        result = _run(mgr.broadcast(notif, scope_path="/docs/"))

        assert "alice" in result["queued"]

    def test_reconnect_flushes_offline_queue(self):
        """Reconnecting client receives all queued notifications."""
        mgr = WebSocketManager()

        # Simulate offline: queue notifications directly
        mgr._offline_queue["alice"] = [
            {"notification_id": "n1", "version": 1},
            {"notification_id": "n2", "version": 2},
        ]

        # Alice reconnects
        c = self._make_client("alice", "/docs/")
        mgr.register(c)
        flushed = _run(mgr.flush_offline(c))
        assert flushed == 2

        # Queue is now empty
        assert mgr._offline_queue.get("alice") is None

    def test_idempotent_notification(self):
        """Same notification_id is not sent twice to the same client."""
        mgr = WebSocketManager()
        c = self._make_client("bob", "/docs/")
        mgr.register(c)

        notif = {"notification_id": "n-same", "type": "version_update"}
        r1 = _run(mgr.broadcast(notif, scope_path="/docs/"))
        r2 = _run(mgr.broadcast(notif, scope_path="/docs/"))

        assert "bob" in r1["sent"]
        # Second time: bob already saw it, so not in sent
        assert "bob" not in r2["sent"]

    def test_parent_scope_client_gets_child_notifications(self):
        """Client on root scope gets notified about child scope changes."""
        mgr = WebSocketManager()
        c_root = self._make_client("admin", "/")
        c_docs = self._make_client("editor", "/docs/")
        mgr.register(c_root)
        mgr.register(c_docs)

        notif = {"notification_id": "n-child", "type": "version_update"}
        result = _run(mgr.broadcast(notif, scope_path="/docs/internal/"))

        # Both should receive (root overlaps with everything, docs overlaps with docs/internal)
        assert "admin" in result["sent"]
        assert "editor" in result["sent"]

    def test_close_all(self):
        mgr = WebSocketManager()
        c1 = self._make_client("a", "/")
        c2 = self._make_client("b", "/")
        mgr.register(c1)
        mgr.register(c2)

        _run(mgr.close_all())
        assert mgr.connected_clients == []
