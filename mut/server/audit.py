"""Server-side audit log with sync and async support."""

import secrets
from datetime import datetime, timezone
from pathlib import Path

from mut.foundation.fs import write_json, mkdir_p, async_write_json, async_mkdir_p


class AuditLog:
    """Append-only audit log in .mut-server/audit/."""

    def __init__(self, audit_dir: Path):
        self.dir = audit_dir

    def _make_entry(self, event_type: str, agent_id: str, detail: dict) -> tuple:
        ts = datetime.now(timezone.utc)
        uid = secrets.token_hex(2)  # 4-char hex to avoid filename collisions
        filename = ts.strftime("%Y%m%d_%H%M%S") + f"_{uid}_{agent_id}_{event_type}.json"
        entry = {
            "type": event_type,
            "agent": agent_id,
            "time": ts.isoformat(timespec="seconds"),
            **detail,
        }
        return filename, entry

    def record(self, event_type: str, agent_id: str, detail: dict):
        mkdir_p(self.dir)
        filename, entry = self._make_entry(event_type, agent_id, detail)
        write_json(self.dir / filename, entry)

    async def async_record(self, event_type: str, agent_id: str, detail: dict):
        await async_mkdir_p(self.dir)
        filename, entry = self._make_entry(event_type, agent_id, detail)
        await async_write_json(self.dir / filename, entry)
