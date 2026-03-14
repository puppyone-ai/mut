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
                    conflicts: list = None, root_hash: str = "") -> dict:
        entry = {
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
               conflicts: list = None, root_hash: str = ""):
        entry = self._make_entry(version, who, message, scope_path, changes,
                                 conflicts, root_hash)
        write_json(self.dir / f"{version:06d}.json", entry)

    def get_since(self, since_version: int, scope_path: str = None) -> list:
        """Return history entries after since_version."""
        result = []
        latest = self.get_latest_version()
        norm_scope = normalize_path(scope_path) if scope_path else None
        for v in range(since_version + 1, latest + 1):
            path = self.dir / f"{v:06d}.json"
            if path.exists():
                entry = read_json(path)
                entry = self._filter_entry(entry, norm_scope)
                if entry is not None:
                    result.append(entry)
        return result

    def get_entry(self, version: int) -> dict:
        path = self.dir / f"{version:06d}.json"
        if path.exists():
            return read_json(path)
        return None

    @staticmethod
    def _filter_entry(entry: dict, norm_scope: str | None) -> dict | None:
        """Apply scope filter + redaction. Returns None if entry should be skipped."""
        if norm_scope is None:
            return entry
        entry_scope = normalize_path(entry.get("scope", "/"))
        if entry_scope and norm_scope and entry_scope != norm_scope:
            if not (entry_scope.startswith(norm_scope + "/") or
                    norm_scope.startswith(entry_scope + "/")):
                return None
        return HistoryManager._redact_for_scope(entry, norm_scope)

    @staticmethod
    def _redact_for_scope(entry: dict, scope: str) -> dict:
        """Strip change details for paths outside the requesting scope."""
        if "changes" in entry:
            entry = dict(entry)
            entry["changes"] = [
                c for c in entry["changes"]
                if normalize_path(c["path"]).startswith(scope)
            ]
        return entry

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
                           conflicts: list = None, root_hash: str = ""):
        entry = self._make_entry(version, who, message, scope_path, changes,
                                 conflicts, root_hash)
        await async_write_json(self.dir / f"{version:06d}.json", entry)

    async def async_get_since(self, since_version: int, scope_path: str = None) -> list:
        result = []
        latest = await self.async_get_latest_version()
        norm_scope = normalize_path(scope_path) if scope_path else None
        for v in range(since_version + 1, latest + 1):
            path = self.dir / f"{v:06d}.json"
            if await async_exists(path):
                entry = await async_read_json(path)
                entry = self._filter_entry(entry, norm_scope)
                if entry is not None:
                    result.append(entry)
        return result

    async def async_get_entry(self, version: int) -> dict | None:
        path = self.dir / f"{version:06d}.json"
        if await async_exists(path):
            return await async_read_json(path)
        return None
