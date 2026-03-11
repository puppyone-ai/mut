"""Snapshot (commit) management — linear chain of tree snapshots.

Each snapshot:
  { id, root, parent, who, message, time, pushed }
"""

from datetime import datetime, timezone
from pathlib import Path

from mut.foundation.fs import read_json, write_json


class SnapshotChain:

    def __init__(self, snapshots_path: Path):
        self.path = snapshots_path

    def load_all(self) -> list[dict]:
        if not self.path.exists():
            return []
        return read_json(self.path)

    def save_all(self, snaps: list[dict]):
        write_json(self.path, snaps)

    def get(self, snap_id: int):
        snaps = self.load_all()
        if snap_id < 1 or snap_id > len(snaps):
            return None
        return snaps[snap_id - 1]

    def latest(self):
        snaps = self.load_all()
        return snaps[-1] if snaps else None

    def create(self, root_hash: str, who: str, message: str, pushed: bool = False):
        """Create a new snapshot. Returns None if nothing changed since last snapshot."""
        snaps = self.load_all()
        if snaps and snaps[-1]["root"] == root_hash:
            return None

        snap = {
            "id": len(snaps) + 1,
            "root": root_hash,
            "parent": snaps[-1]["id"] if snaps else None,
            "who": who,
            "message": message,
            "time": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "pushed": pushed,
        }
        snaps.append(snap)
        self.save_all(snaps)
        return snap

    def get_unpushed(self) -> list[dict]:
        return [s for s in self.load_all() if not s.get("pushed", True)]

    def mark_pushed(self, up_to_id: int):
        snaps = self.load_all()
        for s in snaps:
            if s["id"] <= up_to_id:
                s["pushed"] = True
        self.save_all(snaps)

    def count(self) -> int:
        return len(self.load_all())
