"""Server-side version history management with sync and async support.

Bug fix: conflict records now persist lost_content and lost_hash fields
for full auditability and recovery of overwritten content.
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from mut.foundation.config import normalize_path
from mut.foundation.fs import (
    read_json, write_json, write_text,
    async_read_json, async_write_json, async_write_text, async_read_text,
    async_exists,
)


class HistoryManager:
    """Manages version history in .mut-server/history/."""

    LATEST_FILE = "latest"
    ROOT_FILE = "root"

    def __init__(self, history_dir: Path):
        self.dir = history_dir

    @staticmethod
    def _serialize_conflicts(conflicts: list) -> list:
        """Serialize ConflictRecord objects to dicts, including lost_content and lost_hash."""
        return [
            {"path": c.path, "strategy": c.strategy, "detail": c.detail,
             "kept": c.kept, "lost_content": c.lost_content, "lost_hash": c.lost_hash}
            for c in conflicts
        ]

    def _make_entry(self, version: int, who: str, message: str,
                    scope_path: str, changes: list,
                    conflicts: list | None = None, root_hash: str = "") -> dict:
        entry: dict = {
            "id": version,
            "who": who,
            "time": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "message": message,
            "scope": scope_path,
            "root": root_hash,
            "changes": changes,
        }
        if conflicts:
            entry["conflicts"] = self._serialize_conflicts(conflicts)
        return entry

    # ── Sync methods ──────────────────────────────

    def get_latest_version(self) -> int:
        return int((self.dir / self.LATEST_FILE).read_text().strip())

    def set_latest_version(self, version: int):
        write_text(self.dir / self.LATEST_FILE, str(version))

    def get_root_hash(self) -> str:
        root_file = self.dir / self.ROOT_FILE
        if not root_file.exists():
            return ""
        return root_file.read_text().strip()

    def set_root_hash(self, h: str):
        write_text(self.dir / self.ROOT_FILE, h)

    def record(self, version: int, who: str, message: str,
               scope_path: str, changes: list,
               conflicts: list | None = None, root_hash: str = ""):
        entry = self._make_entry(version, who, message, scope_path, changes,
                                 conflicts, root_hash)
        write_json(self.dir / f"{version:06d}.json", entry)

    def get_since(self, since_version: int, scope_path: str | None = None) -> list:
        """Return history entries after since_version.

        If scope_path is given, only entries whose scope overlaps
        with scope_path are included (prevents cross-scope info leak).
        """
        result: list[dict] = []
        latest = self.get_latest_version()
        norm_scope = normalize_path(scope_path) if scope_path else None

        for v in range(since_version + 1, latest + 1):
            entry = self.get_entry(v)
            if entry is None:
                continue
            if norm_scope is not None:
                if not _scopes_overlap(entry.get("scope", "/"), norm_scope):
                    continue
                entry = _redact_for_scope(entry, norm_scope)
            result.append(entry)
        return result

    def get_entry(self, version: int) -> dict | None:
        path = self.dir / f"{version:06d}.json"
        if path.exists():
            return read_json(path)
        return None

    # ── Async methods ─────────────────────────────

    async def async_get_latest_version(self) -> int:
        text = await async_read_text(self.dir / self.LATEST_FILE)
        return int(text)

    async def async_set_latest_version(self, version: int):
        await async_write_text(self.dir / self.LATEST_FILE, str(version))

    async def async_get_root_hash(self) -> str:
        root_file = self.dir / self.ROOT_FILE
        if not await async_exists(root_file):
            return ""
        return await async_read_text(root_file)

    async def async_set_root_hash(self, h: str):
        await async_write_text(self.dir / self.ROOT_FILE, h)

    async def async_record(self, version: int, who: str, message: str,
                           scope_path: str, changes: list,
                           conflicts: list | None = None, root_hash: str = ""):
        entry = self._make_entry(version, who, message, scope_path, changes,
                                 conflicts, root_hash)
        await async_write_json(self.dir / f"{version:06d}.json", entry)

    async def async_get_since(self, since_version: int,
                              scope_path: str | None = None) -> list:
        result: list[dict] = []
        latest = await self.async_get_latest_version()
        norm_scope = normalize_path(scope_path) if scope_path else None
        for v in range(since_version + 1, latest + 1):
            entry = await self.async_get_entry(v)
            if entry is None:
                continue
            if norm_scope is not None:
                if not _scopes_overlap(entry.get("scope", "/"), norm_scope):
                    continue
                entry = _redact_for_scope(entry, norm_scope)
            result.append(entry)
        return result

    async def async_get_entry(self, version: int) -> dict | None:
        path = self.dir / f"{version:06d}.json"
        if await async_exists(path):
            return await async_read_json(path)
        return None


# ── Module-level helpers ─────────────────────

def _scopes_overlap(entry_scope: str, requesting_scope: str) -> bool:
    """Check if two scope paths overlap (one is prefix of the other)."""
    es = normalize_path(entry_scope)
    rs = requesting_scope  # already normalized by caller
    if not es or not rs:
        return True
    return (es.startswith(rs + "/") or rs.startswith(es + "/") or es == rs)


def _redact_for_scope(entry: dict, scope: str) -> dict:
    """Strip change details for paths outside the requesting scope."""
    if "changes" not in entry:
        return entry
    redacted = dict(entry)
    redacted["changes"] = [
        c for c in entry["changes"]
        if normalize_path(c["path"]).startswith(scope)
    ]
    return redacted
