"""Server-side repository management with sync and async support.

Bug fix: async methods use asyncio.Lock for global version/root_hash
writes, preventing race conditions when multiple scopes push concurrently.

Server directory layout:
  /repo-root/
  ├── current/           ← live project files
  └── .mut-server/
      ├── config.json
      ├── secret.key
      ├── objects/        ← full project object store
      ├── scopes/         ← per-scope permission files
      ├── history/        ← per-version JSON records
      │   └── latest      ← current version number
      ├── audit/          ← append-only audit log
      ├── locks/          ← atomic lock files
      └── invites/        ← invite tokens
"""

from __future__ import annotations

import asyncio
import json
import secrets
from pathlib import Path

from mut.foundation.config import (
    MUT_SERVER_DIR, SERVER_OBJECTS_DIR, SERVER_CURRENT_DIR,
    SERVER_SCOPES_DIR, SERVER_HISTORY_DIR,
    SERVER_LOCKS_DIR, SERVER_LATEST_FILE, SERVER_ROOT_FILE,
    SERVER_CONFIG_FILE, SECRET_KEY_FILE, SERVER_AUDIT_DIR,
    SERVER_INVITES_DIR, BUILTIN_IGNORE, normalize_path,
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
from mut.core.auth import sign_token
from mut.server.scope_manager import ScopeManager
from mut.server.history import HistoryManager
from mut.server.audit import AuditLog


class ServerRepo:
    """Manages a server-side Mut repository."""

    def __init__(self, repo_root: str):
        self.root = Path(repo_root).resolve()
        self.meta = self.root / MUT_SERVER_DIR
        self.current = self.root / SERVER_CURRENT_DIR
        self.store = ObjectStore(self.meta / SERVER_OBJECTS_DIR)

        self.scopes = ScopeManager(self.meta / SERVER_SCOPES_DIR)
        self.history = HistoryManager(self.meta / SERVER_HISTORY_DIR)
        self.audit = AuditLog(self.meta / SERVER_AUDIT_DIR)
        self.locks_dir = self.meta / SERVER_LOCKS_DIR
        self.invites_dir = self.meta / SERVER_INVITES_DIR

        # Async locks for concurrent safety
        self._global_lock = asyncio.Lock()      # protects version + root_hash
        self._scope_locks: dict[str, asyncio.Lock] = {}  # per-scope push serialization

    def _get_scope_lock(self, scope_id: str) -> asyncio.Lock:
        """Get or create an asyncio.Lock for a scope."""
        if scope_id not in self._scope_locks:
            self._scope_locks[scope_id] = asyncio.Lock()
        return self._scope_locks[scope_id]

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
        mkdir_p(meta / SERVER_INVITES_DIR)

        write_json(meta / SERVER_CONFIG_FILE, {"project": project_name})

        secret = secrets.token_hex(32)
        write_text(meta / SECRET_KEY_FILE, secret)

        write_text(meta / SERVER_HISTORY_DIR / SERVER_LATEST_FILE, "0")
        write_text(meta / SERVER_HISTORY_DIR / SERVER_ROOT_FILE, "")

        return ServerRepo(repo_root)

    def check_init(self):
        if not self.meta.exists():
            raise FileNotFoundError("not a mut server repo (run 'mut-server init' first)")

    def get_project_name(self) -> str:
        cfg = read_json(self.meta / SERVER_CONFIG_FILE)
        return cfg.get("project", "project")

    # ── Secret & Token ────────────────────────────

    def get_secret(self) -> str:
        return (self.meta / SECRET_KEY_FILE).read_text().strip()

    def issue_token(self, agent_id: str, expiry_seconds: int = 0) -> str:
        scope = self.scopes.get_for_agent(agent_id)
        if scope is None:
            raise ValueError(f"no scope configured for agent '{agent_id}'")
        return sign_token(
            self.get_secret(), agent_id, scope["path"], scope["mode"], expiry_seconds
        )

    # ── Invites ────────────────────────────────────

    def create_invite(self, scope_path: str, mode: str = "rw",
                      exclude: list = None, max_uses: int = 0) -> dict:
        """Create an invite. max_uses=0 means unlimited."""
        from datetime import datetime, timezone
        invite_id = secrets.token_urlsafe(12)
        invite = {
            "id": invite_id,
            "scope_path": scope_path,
            "mode": mode,
            "exclude": exclude or [],
            "max_uses": max_uses,
            "used": 0,
            "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        }
        mkdir_p(self.invites_dir)
        write_json(self.invites_dir / f"{invite_id}.json", invite)
        return invite

    def use_invite(self, invite_id: str) -> tuple[str, str]:
        """Consume an invite: create scope + agent, return (agent_id, token)."""
        path = self.invites_dir / f"{invite_id}.json"
        if not path.exists():
            raise ValueError("invalid invite")

        invite = read_json(path)
        if invite["max_uses"] > 0 and invite["used"] >= invite["max_uses"]:
            raise ValueError("invite has been fully used")

        agent_id = f"agent-{secrets.token_hex(4)}"
        scope_id = f"scope-{secrets.token_hex(4)}"

        self.add_scope(
            scope_id, invite["scope_path"], [agent_id],
            invite["mode"], invite["exclude"],
        )
        token = self.issue_token(agent_id)

        invite["used"] += 1
        write_json(path, invite)

        self.record_audit("invite_used", agent_id, {
            "invite_id": invite_id,
            "scope_path": invite["scope_path"],
        })

        return agent_id, token

    async def async_use_invite(self, invite_id: str) -> tuple[str, str]:
        """Async: consume an invite."""
        path = self.invites_dir / f"{invite_id}.json"
        if not await async_exists(path):
            raise ValueError("invalid invite")

        invite = await async_read_json(path)
        if invite["max_uses"] > 0 and invite["used"] >= invite["max_uses"]:
            raise ValueError("invite has been fully used")

        agent_id = f"agent-{secrets.token_hex(4)}"
        scope_id = f"scope-{secrets.token_hex(4)}"

        await self.scopes.async_add(
            scope_id, invite["scope_path"], [agent_id],
            invite["mode"], invite["exclude"],
        )
        # issue_token is sync (just HMAC), fine to call directly
        token = self.issue_token(agent_id)

        invite["used"] += 1
        await async_write_json(path, invite)

        await self.audit.async_record("invite_used", agent_id, {
            "invite_id": invite_id,
            "scope_path": invite["scope_path"],
        })

        return agent_id, token

    # ── Delegated scope access ────────────────────

    def add_scope(self, scope_id: str, path: str, agents: list,
                  mode: str = "rw", exclude: list = None) -> dict:
        return self.scopes.add(scope_id, path, agents, mode, exclude)

    def get_scope_for_agent(self, agent_id: str) -> dict | None:
        return self.scopes.get_for_agent(agent_id)

    async def async_get_scope_for_agent(self, agent_id: str) -> dict | None:
        return await self.scopes.async_get_for_agent(agent_id)

    # ── Delegated history access ──────────────────

    def get_latest_version(self) -> int:
        return self.history.get_latest_version()

    def set_latest_version(self, version: int):
        self.history.set_latest_version(version)

    def get_root_hash(self) -> str:
        return self.history.get_root_hash()

    def set_root_hash(self, h: str):
        self.history.set_root_hash(h)

    def record_history(self, version: int, who: str, message: str,
                       scope_path: str, changes: list,
                       conflicts: list = None, root_hash: str = ""):
        self.history.record(version, who, message, scope_path, changes,
                            conflicts, root_hash)

    def get_history_since(self, since_version: int, scope_path: str = None) -> list:
        return self.history.get_since(since_version, scope_path)

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

    async def async_record_history(self, version: int, who: str, message: str,
                                   scope_path: str, changes: list,
                                   conflicts: list = None, root_hash: str = ""):
        await self.history.async_record(version, who, message, scope_path, changes,
                                        conflicts, root_hash)

    async def async_get_history_since(self, since_version: int,
                                      scope_path: str = None) -> list:
        return await self.history.async_get_since(since_version, scope_path)

    async def async_get_history_entry(self, version: int) -> dict | None:
        return await self.history.async_get_entry(version)

    # ── Delegated audit access ────────────────────

    def record_audit(self, event_type: str, agent_id: str, detail: dict):
        self.audit.record(event_type, agent_id, detail)

    async def async_record_audit(self, event_type: str, agent_id: str, detail: dict):
        await self.audit.async_record(event_type, agent_id, detail)

    # ── Files in current/ ─────────────────────────

    def list_scope_files(self, scope: dict) -> dict:
        """Return {relative_path: file_bytes} for all files in scope."""
        scope_path = normalize_path(scope["path"])
        excludes = [normalize_path(e) for e in scope.get("exclude", [])]
        base = self.current / scope_path if scope_path else self.current
        result = {}

        if not base.exists():
            return result

        def _walk(dirpath, prefix):
            for child in sorted(dirpath.iterdir()):
                rel, full_rel = _child_paths(child.name, prefix, scope_path)
                if _should_skip(child.name, full_rel, excludes):
                    continue
                if child.is_file():
                    result[rel] = child.read_bytes()
                elif child.is_dir():
                    _walk(child, rel)

        _walk(base, "")
        return result

    async def async_list_scope_files(self, scope: dict) -> dict:
        """Async: return {relative_path: file_bytes} for all files in scope."""
        scope_path = normalize_path(scope["path"])
        excludes = [normalize_path(e) for e in scope.get("exclude", [])]
        base = self.current / scope_path if scope_path else self.current
        result: dict[str, bytes] = {}

        if not await async_exists(base):
            return result

        await self._async_walk_scope(base, "", scope_path, excludes, result)
        return result

    async def _async_walk_scope(self, dirpath: Path, prefix: str,
                                scope_path: str, excludes: list,
                                out: dict) -> None:
        children = await async_iterdir(dirpath)
        for child in children:
            rel, full_rel = _child_paths(child.name, prefix, scope_path)
            if _should_skip(child.name, full_rel, excludes):
                continue
            if child.is_file():
                out[rel] = await async_read_bytes(child)
            elif child.is_dir():
                await self._async_walk_scope(child, rel, scope_path, excludes, out)

    def write_scope_files(self, scope: dict, files: dict):
        scope_path = normalize_path(scope["path"])
        base = self.current / scope_path if scope_path else self.current
        mkdir_p(base)
        for rel_path, content in files.items():
            target = base / rel_path
            if not is_safe_path(base, target):
                raise ValueError(f"path traversal blocked: {rel_path}")
            mkdir_p(target.parent)
            target.write_bytes(content)

    async def async_write_scope_files(self, scope: dict, files: dict):
        scope_path = normalize_path(scope["path"])
        base = self.current / scope_path if scope_path else self.current
        await async_mkdir_p(base)
        for rel_path, content in files.items():
            target = base / rel_path
            if not is_safe_path(base, target):
                raise ValueError(f"path traversal blocked: {rel_path}")
            await async_write_bytes(target, content)

    def delete_scope_file(self, scope: dict, rel_path: str):
        scope_path = normalize_path(scope["path"])
        base = self.current / scope_path if scope_path else self.current
        target = base / rel_path
        if target.exists():
            target.unlink()
            parent = target.parent
            while parent != base and not any(parent.iterdir()):
                parent.rmdir()
                parent = parent.parent

    async def async_delete_scope_file(self, scope: dict, rel_path: str):
        scope_path = normalize_path(scope["path"])
        base = self.current / scope_path if scope_path else self.current
        target = base / rel_path
        await async_unlink(target)
        # Clean empty parents
        parent = target.parent
        while parent != base:
            children = await async_iterdir(parent)
            if children:
                break
            await asyncio.to_thread(parent.rmdir)
            parent = parent.parent

    # ── Tree operations ───────────────────────────

    def build_full_tree(self) -> str:
        """Build a Merkle tree of the entire current/ directory."""
        ignore = IgnoreRules(self.current)
        if not self.current.exists() or not any(self.current.iterdir()):
            empty = json.dumps({}, sort_keys=True).encode()
            return self.store.put(empty)
        return tree_mod.scan_dir(self.store, self.current, ignore)

    async def async_build_full_tree(self) -> str:
        return await asyncio.to_thread(self.build_full_tree)

    def build_scope_tree(self, scope: dict) -> str:
        """Build a Merkle tree for the files in this scope and return root hash."""
        files = self.list_scope_files(scope)
        return self._build_tree_from_files(files)

    async def async_build_scope_tree(self, scope: dict) -> str:
        files = await self.async_list_scope_files(scope)
        return await asyncio.to_thread(self._build_tree_from_files, files)

    def _build_tree_from_files(self, files: dict) -> str:
        nested = {}
        for path, content in files.items():
            parts = path.split("/")
            d = nested
            for p in parts[:-1]:
                d = d.setdefault(p, {})
            blob_hash = self.store.put(content)
            d[parts[-1]] = ("B", blob_hash)
        return self._write_nested_tree(nested)

    def _write_nested_tree(self, node: dict) -> str:
        entries = {}
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

    # ── Global lock for version/root atomicity (bug fix #2) ──

    async def async_update_global_state(self, new_version: int, new_root: str,
                                        who: str, message: str, scope_path: str,
                                        changes: list, conflicts: list = None):
        """Atomically update version + root hash under global lock.

        This prevents race conditions when multiple scopes push concurrently.
        """
        async with self._global_lock:
            await self.async_record_history(
                new_version, who, message, scope_path, changes,
                conflicts=conflicts, root_hash=new_root,
            )
            await self.async_set_latest_version(new_version)
            await self.async_set_root_hash(new_root)


def _is_excluded(full_rel: str, excludes: list[str]) -> bool:
    """Check if a path matches any exclusion pattern."""
    return any(
        full_rel.startswith(exc + "/") or full_rel == exc
        for exc in excludes
    )


def _child_paths(name: str, prefix: str, scope_path: str) -> tuple[str, str]:
    """Compute relative and full-relative paths for a child entry."""
    rel = f"{prefix}/{name}" if prefix else name
    full_rel = f"{scope_path}/{rel}" if scope_path else rel
    return rel, full_rel


def _should_skip(name: str, full_rel: str, excludes: list[str]) -> bool:
    """Check if a child should be skipped (builtin ignore or excluded)."""
    return name in BUILTIN_IGNORE or _is_excluded(full_rel, excludes)
