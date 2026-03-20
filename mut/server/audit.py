"""Server-side audit log with pluggable backends."""

from __future__ import annotations

import abc
import secrets
from datetime import datetime, timezone
from pathlib import Path

from mut.foundation.fs import write_json, mkdir_p


class AuditBackend(abc.ABC):
    """Abstract interface for audit log storage."""

    @abc.abstractmethod
    def append(self, entry: dict) -> None: ...


class FileSystemAuditBackend(AuditBackend):
    """One JSON file per event in .mut-server/audit/."""

    def __init__(self, audit_dir: Path):
        self.dir = audit_dir

    def append(self, entry: dict) -> None:
        mkdir_p(self.dir)
        ts = datetime.now(timezone.utc)
        uid = secrets.token_hex(2)
        agent = entry.get("agent", "unknown")
        event_type = entry.get("type", "unknown")
        filename = ts.strftime("%Y%m%d_%H%M%S") + f"_{uid}_{agent}_{event_type}.json"
        write_json(self.dir / filename, entry)


class AuditLog:
    """Append-only audit log via a pluggable AuditBackend."""

    def __init__(self, backend: AuditBackend):
        self._backend = backend

    def record(self, event_type: str, agent_id: str, detail: dict):
        entry = {
            "type": event_type,
            "agent": agent_id,
            "time": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            **detail,
        }
        self._backend.append(entry)

    async def async_record(self, event_type: str, agent_id: str, detail: dict):
        import asyncio
        await asyncio.to_thread(self.record, event_type, agent_id, detail)
