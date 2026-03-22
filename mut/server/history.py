"""Server-side version history management with pluggable backends.

Supports per-scope versioning (scope_hash, scope_version) alongside
a global version counter. No global root hash — each scope tracks
its own Merkle tree hash independently.

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
    def record(self, version: int, entry: dict) -> None: ...

    @abc.abstractmethod
    def get_entry(self, version: int) -> dict | None: ...

    @abc.abstractmethod
    def get_since(self, since_version: int, limit: int = 0) -> list[dict]: ...

    # Per-scope version tracking (default no-ops for backends that
    # don't support scope-level versioning, e.g. legacy or in-memory)
    def get_scope_version(self, _scope_path: str) -> int:
        """Get the latest scope-level version number for a scope."""
        return 0

    def set_scope_version(self, _scope_path: str, _version: int) -> None:
        """Set the scope-level version number. Override in subclass."""

    def get_scope_hash(self, _scope_path: str) -> str:
        """Get the current Merkle tree hash for a scope."""
        return ""

    def set_scope_hash(self, _scope_path: str, _h: str) -> None:
        """Set the Merkle tree hash for a scope. Override in subclass."""

    def get_version_index(self) -> dict:
        """Get the global version -> scope version mapping."""
        return {}

    def update_version_index(self, _global_version: int,
                             _scope: str, _scope_version: str) -> None:
        """Record a global version -> scope version mapping. Override in subclass."""

    # Backwards compat: root hash (deprecated, kept for migration)
    def get_root_hash(self) -> str:
        """Deprecated: use get_scope_hash instead."""
        return ""

    def set_root_hash(self, _h: str) -> None:
        """Deprecated: use set_scope_hash instead."""


class FileSystemHistoryBackend(HistoryBackend):
    """JSON files in .mut-server/history/."""

    LATEST_FILE = "latest"
    ROOT_FILE = "root"
    VERSION_INDEX_FILE = "versions.json"
    SCOPE_STATE_DIR = "scope_state"

    def __init__(self, history_dir: Path):
        self.dir = history_dir

    def get_latest_version(self) -> int:
        f = self.dir / self.LATEST_FILE
        if not f.exists():
            return 0
        return int(f.read_text().strip())

    def set_latest_version(self, version: int) -> None:
        write_text(self.dir / self.LATEST_FILE, str(version))

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

    # Per-scope state stored in scope_state/{scope_key}.json
    def _scope_key(self, scope_path: str) -> str:
        norm = normalize_path(scope_path)
        return norm.replace("/", "_") if norm else "_root"

    def _scope_state_path(self, scope_path: str) -> Path:
        d = self.dir / self.SCOPE_STATE_DIR
        d.mkdir(parents=True, exist_ok=True)
        return d / f"{self._scope_key(scope_path)}.json"

    def _load_scope_state(self, scope_path: str) -> dict:
        p = self._scope_state_path(scope_path)
        if p.exists():
            return read_json(p)
        return {"version": 0, "hash": ""}

    def _save_scope_state(self, scope_path: str, state: dict) -> None:
        write_json(self._scope_state_path(scope_path), state)

    def get_scope_version(self, scope_path: str) -> int:
        return self._load_scope_state(scope_path).get("version", 0)

    def set_scope_version(self, scope_path: str, version: int) -> None:
        state = self._load_scope_state(scope_path)
        state["version"] = version
        self._save_scope_state(scope_path, state)

    def get_scope_hash(self, scope_path: str) -> str:
        return self._load_scope_state(scope_path).get("hash", "")

    def set_scope_hash(self, scope_path: str, h: str) -> None:
        state = self._load_scope_state(scope_path)
        state["hash"] = h
        self._save_scope_state(scope_path, state)

    def get_version_index(self) -> dict:
        """Reconstruct version index from history entries (no separate file)."""
        latest = self.get_latest_version()
        index = {}
        for v in range(0, latest + 1):
            entry = self.get_entry(v)
            if entry:
                index[str(v)] = {
                    "scope": entry.get("scope", "/"),
                    "scope_version": entry.get("scope_version", ""),
                }
        return index

    def update_version_index(self, _global_version: int,
                             _scope: str, _scope_version: str) -> None:
        # Version index is derived from history entries — no separate file needed.
        # The entry itself (recorded via record()) contains scope + scope_version.
        pass

    # Backwards compat
    def get_root_hash(self) -> str:
        root_file = self.dir / self.ROOT_FILE
        if not root_file.exists():
            return ""
        return root_file.read_text().strip()

    def set_root_hash(self, h: str) -> None:
        write_text(self.dir / self.ROOT_FILE, h)


class HistoryManager:
    """Manages version history via a pluggable HistoryBackend.

    Supports per-scope versioning: each scope has independent version
    numbers (e.g. "docs/3", "src/5") and tree hashes. A global version
    counter provides cross-scope ordering.
    """

    def __init__(self, backend: HistoryBackend):
        self._backend = backend

    @staticmethod
    def _serialize_conflicts(conflicts: list) -> list:
        return [
            {"path": c.path, "strategy": c.strategy, "detail": c.detail,
             "kept": c.kept, "lost_content": c.lost_content, "lost_hash": c.lost_hash}
            for c in conflicts
        ]

    @staticmethod
    def make_scope_version_id(scope_path: str, scope_version: int) -> str:
        """Create a scope-prefixed version identifier like 'docs/3'."""
        norm = normalize_path(scope_path)
        prefix = norm if norm else "root"
        return f"{prefix}/{scope_version}"

    def _make_entry(self, global_version: int, who: str, message: str,
                    scope_path: str, changes: list,
                    conflicts: list | None = None,
                    scope_hash: str = "",
                    scope_version: str = "",
                    root_hash: str = "") -> dict:
        entry: dict = {
            "id": global_version,
            "scope_version": scope_version,
            "who": who,
            "time": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "message": message,
            "scope": scope_path,
            "scope_hash": scope_hash,
            "changes": changes,
        }
        # Keep root_hash for backwards compat if provided
        if root_hash:
            entry["root"] = root_hash
        if conflicts:
            entry["conflicts"] = self._serialize_conflicts(conflicts)
        return entry

    # Global version
    def get_latest_version(self) -> int:
        return self._backend.get_latest_version()

    def set_latest_version(self, version: int):
        self._backend.set_latest_version(version)

    # Per-scope version
    def get_scope_version(self, scope_path: str) -> int:
        return self._backend.get_scope_version(scope_path)

    def set_scope_version(self, scope_path: str, version: int):
        self._backend.set_scope_version(scope_path, version)

    def get_scope_hash(self, scope_path: str) -> str:
        return self._backend.get_scope_hash(scope_path)

    def set_scope_hash(self, scope_path: str, h: str):
        self._backend.set_scope_hash(scope_path, h)

    # Version index
    def get_version_index(self) -> dict:
        return self._backend.get_version_index()

    # Backwards compat
    def get_root_hash(self) -> str:
        return self._backend.get_root_hash()

    def set_root_hash(self, h: str):
        self._backend.set_root_hash(h)

    def record(self, version: int, who: str, message: str,
               scope_path: str, changes: list,
               conflicts: list | None = None,
               scope_hash: str = "", scope_version: str = "",
               root_hash: str = ""):
        entry = self._make_entry(version, who, message, scope_path, changes,
                                 conflicts, scope_hash, scope_version, root_hash)
        self._backend.record(version, entry)
        self._backend.update_version_index(version, scope_path, scope_version)

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

    # Async wrappers
    async def async_get_latest_version(self) -> int:
        import asyncio
        return await asyncio.to_thread(self.get_latest_version)

    async def async_set_latest_version(self, version: int):
        import asyncio
        await asyncio.to_thread(self.set_latest_version, version)

    async def async_get_scope_version(self, scope_path: str) -> int:
        import asyncio
        return await asyncio.to_thread(self.get_scope_version, scope_path)

    async def async_set_scope_version(self, scope_path: str, version: int):
        import asyncio
        await asyncio.to_thread(self.set_scope_version, scope_path, version)

    async def async_get_scope_hash(self, scope_path: str) -> str:
        import asyncio
        return await asyncio.to_thread(self.get_scope_hash, scope_path)

    async def async_set_scope_hash(self, scope_path: str, h: str):
        import asyncio
        await asyncio.to_thread(self.set_scope_hash, scope_path, h)

    async def async_record(self, version: int, who: str, message: str,
                           scope_path: str, changes: list,
                           conflicts: list | None = None,
                           scope_hash: str = "", scope_version: str = "",
                           root_hash: str = ""):
        import asyncio
        await asyncio.to_thread(
            self.record, version, who, message, scope_path,
            changes, conflicts, scope_hash, scope_version, root_hash,
        )

    async def async_get_since(self, since_version: int,
                              scope_path: str | None = None,
                              limit: int = 0) -> list:
        import asyncio
        return await asyncio.to_thread(self.get_since, since_version, scope_path, limit)

    async def async_get_entry(self, version: int) -> dict | None:
        import asyncio
        return await asyncio.to_thread(self.get_entry, version)

    async def async_get_root_hash(self) -> str:
        import asyncio
        return await asyncio.to_thread(self.get_root_hash)

    async def async_set_root_hash(self, h: str):
        import asyncio
        await asyncio.to_thread(self.set_root_hash, h)

    # Scope migration
    def migrate_scope(self, old_scope_path: str,
                      new_scope_map: dict[str, str],
                      fallback_scope: str = "/") -> int:
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
            best_scope = self._pick_new_scope(
                entry.get("changes", []), new_scope_map, fallback_scope,
            )
            entry["scope"] = best_scope
            self._backend.record(v, entry)
            count += 1
        return count

    @staticmethod
    def _pick_new_scope(changes, new_scope_map, fallback):
        if not changes:
            return fallback
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
