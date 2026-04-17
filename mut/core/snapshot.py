"""Snapshot (commit) management — per-file storage for O(1) create/mark.

Each snapshot is stored as an individual JSON file: snapshots/{id:06d}.json
Metadata files:
  snapshots/latest — latest snapshot ID
  snapshots/pushed — watermark: all IDs <= this are pushed

Migrates automatically from legacy single-file (snapshots.json) format.
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from mut.foundation.fs import read_json, write_json, read_text, write_text


class SnapshotChain:

    def __init__(self, snapshots_path: Path):
        """Accept either a directory path or legacy file path."""
        # Legacy: path ends with .json → derive directory from parent
        if snapshots_path.suffix == ".json":
            self.dir = snapshots_path.parent / "snapshots"
            self._legacy_path = snapshots_path
        else:
            self.dir = snapshots_path
            self._legacy_path = None
        self._migrated = False

    def _ensure_migrated(self):
        """Migrate from legacy single-file format if needed (once per session)."""
        if self._migrated:
            return
        self._migrated = True
        if self._legacy_path and self._legacy_path.exists():
            self._migrate_legacy()

    def _migrate_legacy(self):
        """Convert snapshots.json → per-file format, then remove old file."""
        snaps = read_json(self._legacy_path)
        if not snaps:
            self._legacy_path.unlink(missing_ok=True)
            return
        self.dir.mkdir(parents=True, exist_ok=True)
        for s in snaps:
            write_json(self.dir / f"{s['id']:06d}.json", s)
        write_text(self.dir / "latest", str(snaps[-1]["id"]))
        # Compute pushed watermark: highest ID where all IDs <= it are pushed
        pushed_wm = 0
        for s in snaps:
            if s.get("pushed", False):
                pushed_wm = s["id"]
            else:
                break
        write_text(self.dir / "pushed", str(pushed_wm))
        self._legacy_path.unlink(missing_ok=True)

    def _get_latest_id(self) -> int:
        latest_file = self.dir / "latest"
        if latest_file.exists():
            return int(read_text(latest_file))
        return 0

    def _get_pushed_watermark(self) -> int:
        pushed_file = self.dir / "pushed"
        if pushed_file.exists():
            return int(read_text(pushed_file))
        return 0

    def _read_snap(self, snap_id: int) -> dict | None:
        path = self.dir / f"{snap_id:06d}.json"
        if path.exists():
            return read_json(path)
        return None

    def _write_snap(self, snap: dict):
        self.dir.mkdir(parents=True, exist_ok=True)
        write_json(self.dir / f"{snap['id']:06d}.json", snap)

    def load_all(self) -> list[dict]:
        self._ensure_migrated()
        latest = self._get_latest_id()
        if latest == 0:
            return []
        result = []
        for i in range(1, latest + 1):
            snap = self._read_snap(i)
            if snap:
                result.append(snap)
        return result

    def save_all(self, snaps: list[dict]):
        """Bulk write — used by legacy callers. Prefer create/mark_pushed."""
        self.dir.mkdir(parents=True, exist_ok=True)
        for s in snaps:
            self._write_snap(s)
        if snaps:
            write_text(self.dir / "latest", str(snaps[-1]["id"]))

    def get(self, snap_id: int):
        self._ensure_migrated()
        if snap_id < 1:
            return None
        return self._read_snap(snap_id)

    def latest(self):
        self._ensure_migrated()
        latest_id = self._get_latest_id()
        if latest_id == 0:
            return None
        return self._read_snap(latest_id)

    def create(self, root_hash: str, who: str, message: str,
               pushed: bool = False,
               server_commit_id: str = ""):
        """Create a new snapshot. Returns None if nothing changed since last.

        `id` is a local cursor (client-only; monotonic int).
        `server_commit_id` is populated after a successful push (or when this
        snapshot was created from a server pull). Empty until then.
        """
        self._ensure_migrated()
        latest_id = self._get_latest_id()

        if latest_id > 0:
            prev = self._read_snap(latest_id)
            if prev and prev["root"] == root_hash:
                return None

        snap = {
            "id": latest_id + 1,
            "root": root_hash,
            "parent": latest_id if latest_id > 0 else None,
            "who": who,
            "message": message,
            "time": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "pushed": pushed,
            "server_commit_id": server_commit_id,
        }
        self._write_snap(snap)
        write_text(self.dir / "latest", str(snap["id"]))
        return snap

    def get_unpushed(self) -> list[dict]:
        self._ensure_migrated()
        latest = self._get_latest_id()
        if latest == 0:
            return []
        watermark = self._get_pushed_watermark()
        result = []
        for i in range(watermark + 1, latest + 1):
            snap = self._read_snap(i)
            if snap and not snap.get("pushed", False):
                result.append(snap)
        return result

    def mark_pushed(self, up_to_id: int, server_commit_id: str = ""):
        """Mark local snapshots ≤ up_to_id as pushed.

        If `server_commit_id` is provided, it is stamped onto every snapshot
        newly flipped to pushed in this call (so `mut log` can correlate the
        local cursor with the server commit).
        """
        self._ensure_migrated()
        latest = self._get_latest_id()
        watermark = self._get_pushed_watermark()
        for i in range(watermark + 1, min(up_to_id, latest) + 1):
            snap = self._read_snap(i)
            if snap and not snap.get("pushed", False):
                snap["pushed"] = True
                if server_commit_id and not snap.get("server_commit_id"):
                    snap["server_commit_id"] = server_commit_id
                self._write_snap(snap)
        write_text(self.dir / "pushed", str(up_to_id))

    def reset_pushed_watermark(self) -> int:
        """Clear the "already pushed" flag on every local snapshot so that
        the next push re-uploads them.

        Used when the server reports it no longer recognizes our
        ``REMOTE_HEAD`` (history truncated, restored from older backup,
        or manually wiped) — the local watermark was lying about what
        the server actually has, so we drop it and let push rebuild
        from scratch (see Bug #6 / ``docs/design/mut-git-alignment.md``).

        Snapshots that were pulled from the server (``who == "pull"`` and
        carrying a non-empty ``server_commit_id``) are left alone: their
        data came from the server and re-pushing them as fresh commits
        would be semantically wrong.

        Returns the number of snapshots whose ``pushed`` flag was cleared.
        """
        self._ensure_migrated()
        latest = self._get_latest_id()
        cleared = 0
        for i in range(1, latest + 1):
            snap = self._read_snap(i)
            if not snap or not snap.get("pushed"):
                continue
            if snap.get("who") == "pull" and snap.get("server_commit_id"):
                continue
            snap["pushed"] = False
            snap["server_commit_id"] = ""
            self._write_snap(snap)
            cleared += 1
        write_text(self.dir / "pushed", "0")
        return cleared

    def count(self) -> int:
        self._ensure_migrated()
        return self._get_latest_id()
