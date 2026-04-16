"""Server-side repository management with sync and async support.

Manages the MUT protocol state: scopes, objects, history, and the
current file tree. Auth is handled externally by an Authenticator.

Server directory layout:
  /repo-root/
  ├── current/           ← live project files
  └── .mut-server/
      ├── config.json
      ├── objects/        ← full project object store
      ├── scopes/         ← subtree boundary definitions
      ├── history/        ← per-version JSON records
      │   └── latest      ← current version number
      ├── audit/          ← append-only audit log
      └── locks/          ← atomic lock files
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

from mut.foundation.config import (
    MUT_SERVER_DIR, SERVER_OBJECTS_DIR, SERVER_CURRENT_DIR,
    SERVER_SCOPES_DIR, SERVER_HISTORY_DIR,
    SERVER_LOCKS_DIR, SERVER_LATEST_FILE, SERVER_ROOT_FILE,
    SERVER_CONFIG_FILE, SERVER_AUDIT_DIR, BUILTIN_IGNORE, normalize_path,
)
from mut.foundation.fs import (
    read_json, write_json, write_text, mkdir_p,
    lock_acquire, lock_release, is_safe_path,
    async_read_json, async_write_json, async_mkdir_p,
    async_read_bytes, async_write_bytes, async_exists,
    async_unlink, async_iterdir,
)
from mut.core.object_store import ObjectStore
from mut.core.ignore import IgnoreRules
from mut.core import tree as tree_mod
from mut.server.scope_manager import ScopeManager, FileSystemScopeBackend
from mut.server.history import HistoryManager, FileSystemHistoryBackend
from mut.server.audit import AuditLog, FileSystemAuditBackend
from mut.server.sync_queue import ScopeQueue


class ServerRepo:
    """Manages a server-side Mut repository (protocol state only, no auth)."""

    def __init__(self, repo_root: str):
        self.root = Path(repo_root).resolve()
        self.meta = self.root / MUT_SERVER_DIR
        self.current = self.root / SERVER_CURRENT_DIR
        self.store = ObjectStore(self.meta / SERVER_OBJECTS_DIR)

        self.scopes = ScopeManager(FileSystemScopeBackend(self.meta / SERVER_SCOPES_DIR))
        self.history = HistoryManager(FileSystemHistoryBackend(self.meta / SERVER_HISTORY_DIR))
        self.audit = AuditLog(FileSystemAuditBackend(self.meta / SERVER_AUDIT_DIR))
        self.locks_dir = self.meta / SERVER_LOCKS_DIR

        self._scope_queue = ScopeQueue()
        self._version_counter: int | None = None  # lazy-loaded in-memory counter

    def next_global_version(self) -> int:
        """Atomically increment and return the next global version number.

        Uses an in-memory counter (no await between read+write) so it's
        safe within asyncio's single-threaded event loop even when
        sibling scopes run in parallel.
        """
        if self._version_counter is None:
            self._version_counter = self.history.get_latest_version()
        self._version_counter += 1
        self.history.set_latest_version(self._version_counter)
        return self._version_counter

    # ── Init ──────────────────────────────────────

    @staticmethod
    def init(repo_root: str, project_name: str = "my-project") -> ServerRepo:
        root = Path(repo_root).resolve()
        meta = root / MUT_SERVER_DIR
        if meta.exists():
            raise FileExistsError(f"server repo already initialized: {meta}")

        mkdir_p(root / SERVER_CURRENT_DIR)
        mkdir_p(meta / SERVER_OBJECTS_DIR)
        mkdir_p(meta / SERVER_SCOPES_DIR)
        mkdir_p(meta / SERVER_HISTORY_DIR)
        mkdir_p(meta / SERVER_LOCKS_DIR)
        mkdir_p(meta / SERVER_AUDIT_DIR)

        write_json(meta / SERVER_CONFIG_FILE, {"project": project_name})
        write_text(meta / SERVER_HISTORY_DIR / SERVER_LATEST_FILE, "0")
        write_text(meta / SERVER_HISTORY_DIR / SERVER_ROOT_FILE, "")

        return ServerRepo(repo_root)

    def check_init(self):
        if not self.meta.exists():
            raise FileNotFoundError(
                "not a mut server repo (run 'mut-server init' first)"
            )

    def get_project_name(self) -> str:
        cfg = read_json(self.meta / SERVER_CONFIG_FILE)
        return cfg.get("project", "project")

    # ── Delegated scope access ────────────────────

    def add_scope(self, scope_id: str, path: str,
                  exclude: list | None = None) -> dict:
        return self.scopes.add(scope_id, path, exclude)

    # ── Delegated history access ──────────────────

    def get_latest_version(self) -> int:
        return self.history.get_latest_version()

    def set_latest_version(self, version: int):
        self.history.set_latest_version(version)

    # Deprecated: use scope-level hash instead
    def get_root_hash(self) -> str:
        return self.history.get_root_hash()

    def set_root_hash(self, h: str):
        self.history.set_root_hash(h)

    # Per-scope version + hash
    def get_scope_version(self, scope_path: str) -> int:
        return self.history.get_scope_version(scope_path)

    def set_scope_version(self, scope_path: str, version: int):
        self.history.set_scope_version(scope_path, version)

    def get_scope_hash(self, scope_path: str) -> str:
        return self.history.get_scope_hash(scope_path)

    def set_scope_hash(self, scope_path: str, h: str):
        self.history.set_scope_hash(scope_path, h)

    def record_history(self, version: int, who: str, message: str,
                       scope_path: str, changes: list,
                       conflicts: list | None = None,
                       scope_hash: str = "", scope_version: str = "",
                       root_hash: str = ""):
        self.history.record(version, who, message, scope_path, changes,
                            conflicts, scope_hash, scope_version, root_hash)

    def get_history_since(self, since_version: int,
                          scope_path: str | None = None,
                          limit: int = 0) -> list:
        return self.history.get_since(since_version, scope_path, limit=limit)

    def get_history_entry(self, version: int) -> dict | None:
        return self.history.get_entry(version)

    # Async delegations
    async def async_get_latest_version(self) -> int:
        return await self.history.async_get_latest_version()

    async def async_set_latest_version(self, version: int):
        await self.history.async_set_latest_version(version)

    async def async_get_root_hash(self) -> str:
        return await self.history.async_get_root_hash()

    async def async_set_root_hash(self, h: str):
        await self.history.async_set_root_hash(h)

    async def async_get_scope_version(self, scope_path: str) -> int:
        return await self.history.async_get_scope_version(scope_path)

    async def async_get_scope_hash(self, scope_path: str) -> str:
        return await self.history.async_get_scope_hash(scope_path)

    async def async_record_history(self, version: int, who: str, message: str,
                                   scope_path: str, changes: list,
                                   conflicts: list = None,
                                   scope_hash: str = "", scope_version: str = "",
                                   root_hash: str = ""):
        await self.history.async_record(version, who, message, scope_path,
                                        changes, conflicts, scope_hash,
                                        scope_version, root_hash)

    async def async_get_history_since(self, since_version: int,
                                      scope_path: str = None,
                                      limit: int = 0) -> list:
        return await self.history.async_get_since(since_version, scope_path,
                                                  limit=limit)

    async def async_get_history_entry(self, version: int) -> dict | None:
        return await self.history.async_get_entry(version)

    # ── Delegated audit access ────────────────────

    def record_audit(self, event_type: str, agent_id: str, detail: dict):
        self.audit.record(event_type, agent_id, detail)

    async def async_record_audit(self, event_type: str, agent_id: str,
                                 detail: dict):
        await self.audit.async_record(event_type, agent_id, detail)

    # ── Files in current/ ─────────────────────────

    def list_scope_files(self, scope: dict) -> dict[str, bytes]:
        scope_path = normalize_path(scope["path"])
        excludes = [normalize_path(e) for e in scope.get("exclude", [])]
        base = _scope_base(self.current, scope_path)

        if not base.exists():
            return {}

        result: dict[str, bytes] = {}
        self._walk_scope_dir(base, "", scope_path, excludes, result)
        return result

    def _walk_scope_dir(self, dirpath: Path, prefix: str, scope_path: str,
                        excludes: list[str], out: dict[str, bytes]) -> None:
        for child in sorted(dirpath.iterdir()):
            rel, full_rel = _child_paths(child.name, prefix, scope_path)
            if _should_skip(child.name, full_rel, excludes):
                continue
            if child.is_file():
                out[rel] = child.read_bytes()
            elif child.is_dir():
                self._walk_scope_dir(child, rel, scope_path, excludes, out)

    async def async_list_scope_files(self, scope: dict) -> dict[str, bytes]:
        scope_path = normalize_path(scope["path"])
        excludes = [normalize_path(e) for e in scope.get("exclude", [])]
        base = _scope_base(self.current, scope_path)
        result: dict[str, bytes] = {}

        if not await async_exists(base):
            return result

        await self._async_walk_scope(base, "", scope_path, excludes, result)
        return result

    async def _async_walk_scope(self, dirpath: Path, prefix: str,
                                scope_path: str, excludes: list[str],
                                out: dict[str, bytes]) -> None:
        children = await async_iterdir(dirpath)
        for child in children:
            rel, full_rel = _child_paths(child.name, prefix, scope_path)
            if _should_skip(child.name, full_rel, excludes):
                continue
            if child.is_file():
                out[rel] = await async_read_bytes(child)
            elif child.is_dir():
                await self._async_walk_scope(child, rel, scope_path,
                                             excludes, out)

    def write_scope_files(self, scope: dict, files: dict) -> None:
        scope_path = normalize_path(scope["path"])
        base = _scope_base(self.current, scope_path)
        mkdir_p(base)
        for rel_path, content in files.items():
            target = base / rel_path
            if not is_safe_path(base, target):
                raise ValueError(f"path traversal blocked: {rel_path}")
            mkdir_p(target.parent)
            target.write_bytes(content)

    async def async_write_scope_files(self, scope: dict, files: dict):
        scope_path = normalize_path(scope["path"])
        base = _scope_base(self.current, scope_path)
        await async_mkdir_p(base)
        for rel_path, content in files.items():
            target = base / rel_path
            if not is_safe_path(base, target):
                raise ValueError(f"path traversal blocked: {rel_path}")
            await async_write_bytes(target, content)

    def delete_scope_file(self, scope: dict, rel_path: str) -> None:
        scope_path = normalize_path(scope["path"])
        base = _scope_base(self.current, scope_path)
        target = base / rel_path
        if target.exists():
            target.unlink()
            parent = target.parent
            while parent != base and not any(parent.iterdir()):
                parent.rmdir()
                parent = parent.parent

    async def async_delete_scope_file(self, scope: dict, rel_path: str):
        scope_path = normalize_path(scope["path"])
        base = _scope_base(self.current, scope_path)
        target = base / rel_path
        await async_unlink(target)
        parent = target.parent
        while parent != base:
            children = await async_iterdir(parent)
            if children:
                break
            await asyncio.to_thread(parent.rmdir)
            parent = parent.parent

    # ── Tree operations ───────────────────────────

    def build_full_tree(self) -> str:
        ignore = IgnoreRules(self.current)
        if not self.current.exists() or not any(self.current.iterdir()):
            empty = json.dumps({}, sort_keys=True).encode()
            return self.store.put(empty)
        return tree_mod.scan_dir(self.store, self.current, ignore)

    async def async_build_full_tree(self) -> str:
        return await asyncio.to_thread(self.build_full_tree)

    def build_scope_tree(self, scope: dict) -> str:
        files = self.list_scope_files(scope)
        return self._build_tree_from_files(files)

    async def async_build_scope_tree(self, scope: dict) -> str:
        files = await self.async_list_scope_files(scope)
        return await asyncio.to_thread(self._build_tree_from_files, files)

    def _build_tree_from_files(self, files: dict) -> str:
        nested: dict = {}
        for path, content in files.items():
            parts = path.split("/")
            d = nested
            for p in parts[:-1]:
                d = d.setdefault(p, {})
            blob_hash = self.store.put(content)
            d[parts[-1]] = ("B", blob_hash)
        return self._write_nested_tree(nested)

    def _write_nested_tree(self, node: dict) -> str:
        entries: dict = {}
        for name, val in sorted(node.items()):
            if isinstance(val, tuple):
                entries[name] = list(val)
            else:
                sub_hash = self._write_nested_tree(val)
                entries[name] = ["T", sub_hash]
        return self.store.put(json.dumps(entries, sort_keys=True).encode())

    # ── Lock (sync: file-based, async: asyncio.Lock) ──

    def acquire_lock(self, scope_id: str) -> bool:
        return lock_acquire(self.locks_dir / f"{scope_id}.lock")

    def release_lock(self, scope_id: str):
        lock_release(self.locks_dir / f"{scope_id}.lock")

    def cas_update_scope(self, scope_path: str, old_hash: str, new_hash: str) -> bool:
        """CAS update scope hash. Returns True if old_hash matched and update succeeded.
        
        Default implementation uses file-based scope state (suitable for single-process).
        PuppyOne overrides this with a database CAS (UPDATE ... WHERE scope_hash = old_hash).
        """
        current = self.get_scope_hash(scope_path)
        if current != old_hash:
            return False
        self.set_scope_hash(scope_path, new_hash)
        return True


def _scope_base(current: Path, scope_path: str) -> Path:
    return current / scope_path if scope_path else current


def _is_excluded(full_rel: str, excludes: list[str]) -> bool:
    return any(
        full_rel.startswith(exc + "/") or full_rel == exc
        for exc in excludes
    )


def _child_paths(name: str, prefix: str, scope_path: str) -> tuple[str, str]:
    rel = f"{prefix}/{name}" if prefix else name
    full_rel = f"{scope_path}/{rel}" if scope_path else rel
    return rel, full_rel


def _should_skip(name: str, full_rel: str, excludes: list[str]) -> bool:
    return name in BUILTIN_IGNORE or _is_excluded(full_rel, excludes)
