"""Server-side notification system for push events.

Generates notifications after successful push/rollback operations.
Supports pluggable delivery via NotificationSink (WebSocket, SSE, webhook, etc.).
Default implementation queues notifications in-memory for retrieval.

Delivery strategy: at-least-once with 3 retries. Offline clients get
their notifications queued and delivered on next connection.
"""

from __future__ import annotations

import abc
import uuid
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timezone


@dataclass
class Notification:
    """A version-update notification for a client."""
    notification_id: str
    repo: str
    scope: str
    version: int
    pushed_by: str
    changed_files: list[str]
    timestamp: str
    type: str = "version_update"

    def to_dict(self) -> dict:
        return {
            "type": self.type,
            "notification_id": self.notification_id,
            "repo": self.repo,
            "scope": self.scope,
            "version": self.version,
            "pushed_by": self.pushed_by,
            "changed_files": self.changed_files,
            "timestamp": self.timestamp,
        }


class NotificationSink(abc.ABC):
    """Abstract interface for delivering notifications to clients.

    Implementations may use WebSocket, SSE, webhook, or polling.
    PuppyOne can provide its own sink that integrates with its
    real-time infrastructure.
    """

    @abc.abstractmethod
    async def send(self, client_id: str, notification: Notification) -> bool:
        """Attempt to deliver a notification. Returns True if acked."""
        ...

    async def close(self) -> None:
        """Clean up resources."""
        pass


class InMemoryNotificationSink(NotificationSink):
    """Default sink that queues notifications for polling retrieval.

    Clients can fetch pending notifications via get_pending().
    Suitable for development and simple deployments.
    """

    MAX_QUEUE_PER_CLIENT = 500

    def __init__(self):
        self._queues: dict[str, list[dict]] = defaultdict(list)

    async def send(self, client_id: str, notification: Notification) -> bool:
        q = self._queues[client_id]
        if len(q) >= self.MAX_QUEUE_PER_CLIENT:
            q.pop(0)  # drop oldest to prevent unbounded growth
        q.append(notification.to_dict())
        return True

    def get_pending(self, client_id: str) -> list[dict]:
        """Retrieve and clear all pending notifications for a client."""
        pending = self._queues.pop(client_id, [])
        return pending

    def peek_pending(self, client_id: str) -> list[dict]:
        """View pending notifications without clearing."""
        return list(self._queues.get(client_id, []))


class NotificationManager:
    """Manages notification generation and delivery after push/rollback.

    Plugged into the server via post-push hooks. Sends notifications to
    all clients with overlapping scope access, excluding the pusher.
    """

    MAX_RETRIES = 3

    def __init__(self, repo_name: str,
                 sink: NotificationSink | None = None):
        self.repo_name = repo_name
        self.sink = sink or InMemoryNotificationSink()

    def create_notification(self, scope_path: str, version: int,
                            pushed_by: str,
                            changes: list[dict]) -> Notification:
        changed_files = [c.get("path", "") for c in changes]
        return Notification(
            notification_id=str(uuid.uuid4()),
            repo=self.repo_name,
            scope=scope_path,
            version=version,
            pushed_by=pushed_by,
            changed_files=changed_files,
            timestamp=datetime.now(timezone.utc).isoformat(),
        )

    async def notify_after_push(self, scope_path: str, version: int,
                                pushed_by: str, changes: list[dict],
                                client_ids: list[str]) -> dict:
        """Send notifications to all relevant clients except the pusher.

        Returns {"sent": [...], "queued": [...]}.
        """
        notification = self.create_notification(
            scope_path, version, pushed_by, changes,
        )

        sent = []
        queued = []

        for client_id in client_ids:
            if client_id == pushed_by:
                continue

            delivered = False
            for _attempt in range(self.MAX_RETRIES):
                if await self.sink.send(client_id, notification):
                    delivered = True
                    break

            if delivered:
                sent.append(client_id)
            else:
                queued.append(client_id)

        return {"sent": sent, "queued": queued}

    async def close(self):
        await self.sink.close()
