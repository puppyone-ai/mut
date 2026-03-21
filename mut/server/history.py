"""Server-side version history management with pluggable backends.

Conflict records persist lost_content and lost_hash fields
for full auditability and recovery of overwritten content.
"""

from __future__ import annotations

import abc
from datetime import datetime, timezone
from pathlib import Path

from mut.foundation.config import normalize_path
from mut.foundation.fs import read_json, write_json, write_text


class HistoryBackend(abc.ABC):
    """Abstract interface for version history storage."""

    @abc.abstractmethod
    def get_latest_version(self) -> int: ...

    @abc.abstractmethod
    def set_latest_version(self, version: int) -> None: ...

    @abc.abstractmethod
    def get_root_hash(self) -> str: ...

    @abc.abstractmethod
    def set_root_hash(self, h: str) -> None: ...

    @abc.abstractmethod
    def record(self, version: int, entry: dict) -> None: ...

    @abc.abstractmethod
    def get_entry(self, version: int) -> dict | None: ...

    @abc.abstractmethod
    def get_since(self, since_version: int, limit: int = 0) -> list[dict]: ...


class FileSystemHistoryBackend(HistoryBackend):
    """JSON files in .mut-server/history/."""

    LATEST_FILE = "latest"
    ROOT_FILE = "root"

    def __init__(self, history_dir: Path):
        self.dir = history_dir

    def get_latest_version(self) -> int:
        return int((self.dir / self.LATEST_FILE).read_text().strip())

    def set_latest_version(self, version: int) -> None:
        write_text(self.dir / self.LATEST_FILE, str(version))

    def get_root_hash(self) -> str:
        root_file = self.dir / self.ROOT_FILE
        if not root_file.exists():
            return ""
        return root_file.read_text().strip()

    def set_root_hash(self, h: str) -> None:
        write_text(self.dir / self.ROOT_FILE, h)

    def record(self, version: int, entry: dict) -> None:
        write_json(self.dir / f"{version:06d}.json", entry)

    def get_entry(self, version: int) -> dict | None:
        path = self.dir / f"{version:06d}.json"
        if path.exists():
            return read_json(path)
        return None

    def get_since(self, since_version: int, limit: int = 0) -> list[dict]:
        latest = self.get_latest_version()
        start = since_version + 1
        if limit > 0:
            start = max(start, latest - limit + 1)
        result = []
        for v in range(start, latest + 1):
            entry = self.get_entry(v)
            if entry is not None:
                result.append(entry)
        return result


class HistoryManager:
    """Manages version history via a pluggable HistoryBackend."""

    def __init__(self, backend: HistoryBackend):
        self._backend = backend

    @staticmethod
    def _serialize_conflicts(conflicts: list) -> list:
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

    def get_latest_version(self) -> int:
        return self._backend.get_latest_version()

    def set_latest_version(self, version: int):
        self._backend.set_latest_version(version)

    def get_root_hash(self) -> str:
        return self._backend.get_root_hash()

    def set_root_hash(self, h: str):
        self._backend.set_root_hash(h)

    def record(self, version: int, who: str, message: str,
               scope_path: str, changes: list,
               conflicts: list | None = None, root_hash: str = ""):
        entry = self._make_entry(version, who, message, scope_path, changes,
                                 conflicts, root_hash)
        self._backend.record(version, entry)

    def get_since(self, since_version: int, scope_path: str | None = None,
                   limit: int = 0) -> list:
        entries = self._backend.get_since(since_version, limit)
        norm_scope = normalize_path(scope_path) if scope_path else None
        if norm_scope is None:
            return entries
        result = []
        for entry in entries:
            if not _scopes_overlap(entry.get("scope", "/"), norm_scope):
                continue
            result.append(_redact_for_scope(entry, norm_scope))
        return result

    def get_entry(self, version: int) -> dict | None:
        return self._backend.get_entry(version)

    async def async_get_latest_version(self) -> int:
        import asyncio
        return await asyncio.to_thread(self.get_latest_version)

    async def async_set_latest_version(self, version: int):
        import asyncio
        await asyncio.to_thread(self.set_latest_version, version)

    async def async_get_root_hash(self) -> str:
        import asyncio
        return await asyncio.to_thread(self.get_root_hash)

    async def async_set_root_hash(self, h: str):
        import asyncio
        await asyncio.to_thread(self.set_root_hash, h)

    async def async_record(self, version: int, who: str, message: str,
                           scope_path: str, changes: list,
                           conflicts: list | None = None, root_hash: str = ""):
        import asyncio
        await asyncio.to_thread(
            self.record, version, who, message, scope_path,
            changes, conflicts, root_hash,
        )

    async def async_get_since(self, since_version: int,
                              scope_path: str | None = None,
                              limit: int = 0) -> list:
        import asyncio
        return await asyncio.to_thread(self.get_since, since_version, scope_path, limit)

    async def async_get_entry(self, version: int) -> dict | None:
        import asyncio
        return await asyncio.to_thread(self.get_entry, version)


    def migrate_scope(self, old_scope_path: str,
                      new_scope_map: dict[str, str],
                      fallback_scope: str = "/") -> int:
        """Re-attribute history entries when scopes change.

        old_scope_path: the scope being split/changed (e.g. "docs")
        new_scope_map: {path_prefix: new_scope_path} for routing
            e.g. {"docs/internal": "docs/internal", "docs/public": "docs/public"}
        fallback_scope: where to assign entries that don't match any new scope

        Returns the number of entries migrated.
        """
        old_norm = normalize_path(old_scope_path)
        latest = self._backend.get_latest_version()
        count = 0

        for v in range(1, latest + 1):
            entry = self._backend.get_entry(v)
            if entry is None:
                continue
            entry_scope = normalize_path(entry.get("scope", "/"))
            if entry_scope != old_norm:
                continue

            # Determine the best new scope based on changes
            best_scope = self._pick_new_scope(
                entry.get("changes", []), new_scope_map, fallback_scope,
            )
            entry["scope"] = best_scope
            self._backend.record(v, entry)
            count += 1

        return count

    @staticmethod
    def _pick_new_scope(changes: list[dict],
                        new_scope_map: dict[str, str],
                        fallback: str) -> str:
        """Pick the best new scope for a history entry based on its changes."""
        if not changes:
            return fallback

        # Count how many changes fall under each new scope
        votes: dict[str, int] = {}
        for change in changes:
            path = normalize_path(change.get("path", ""))
            matched = False
            for prefix, new_scope in new_scope_map.items():
                norm_prefix = normalize_path(prefix)
                if path.startswith(norm_prefix + "/") or path == norm_prefix:
                    votes[new_scope] = votes.get(new_scope, 0) + 1
                    matched = True
                    break
            if not matched:
                votes[fallback] = votes.get(fallback, 0) + 1

        if not votes:
            return fallback
        return max(votes, key=votes.get)


def _scopes_overlap(entry_scope: str, requesting_scope: str) -> bool:
    es = normalize_path(entry_scope)
    rs = requesting_scope
    if not es or not rs:
        return True
    return (es.startswith(rs + "/") or rs.startswith(es + "/") or es == rs)


def _redact_for_scope(entry: dict, scope: str) -> dict:
    redacted = dict(entry)
    redacted.pop("root", None)
    if "changes" in entry:
        redacted["changes"] = [
            c for c in entry["changes"]
            if normalize_path(c["path"]).startswith(scope)
        ]
    if "conflicts" in entry:
        redacted["conflicts"] = [
            c for c in entry["conflicts"]
            if normalize_path(c["path"]).startswith(scope)
        ]
    return redacted
