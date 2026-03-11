"""Server-side audit log."""

from datetime import datetime, timezone
from pathlib import Path

from mut.foundation.fs import write_json, mkdir_p


class AuditLog:
    """Append-only audit log in .mut-server/audit/."""

    def __init__(self, audit_dir: Path):
        self.dir = audit_dir

    def record(self, event_type: str, agent_id: str, detail: dict):
        mkdir_p(self.dir)
        ts = datetime.now(timezone.utc)
        filename = ts.strftime("%Y%m%d_%H%M%S") + f"_{agent_id}_{event_type}.json"
        entry = {
            "type": event_type,
            "agent": agent_id,
            "time": ts.isoformat(timespec="seconds"),
            **detail,
        }
        write_json(self.dir / filename, entry)
