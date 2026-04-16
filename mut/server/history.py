"""Server-side version history management with pluggable backends.

Commits are identified by a 16-hex-char commit_id (SHA256 truncated)
derived from (scope_path, scope_hash, created_at_iso, who). Per-scope
head_commit_id tracking is the canonical state used by CAS.

Conflict records persist lost_content and lost_hash fields for full
auditability and recovery of overwritten content.
"""

from __future__ import annotations

import abc
import hashlib
from datetime import datetime, timezone
from pathlib import Path

from mut.foundation.config import normalize_path
from mut.foundation.fs import read_json, write_json, write_text


# ── Commit ID algorithm ────────────────────────

COMMIT_ID_LENGTH = 16  # hex chars = 64 bits; collision probability ~1/2^64 per scope


def compute_commit_id(
    scope_path: str,
    scope_hash: str,
    created_at_iso: str,
    who: str,
) -> str:
    """Deterministic commit_id from commit-identifying metadata.

    Payload: scope_path | scope_hash | created_at_iso | who

    - scope_path gives per-scope uniqueness
    - scope_hash fingerprints the content (Merkle root)
    - created_at_iso disambiguates same-content rollbacks
    - who disambiguates same-time commits from different agents

    We intentionally exclude `message` (purely cosmetic) and
    `parent_commit_id` (not persisted; linear chain uses created_at
    sorting). Future DAG support can rebuild a chain without touching
    the id algorithm.
    """
    payload = "|".join([
        scope_path or "",
        scope_hash or "",
        created_at_iso,
        who or "",
    ])
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:COMMIT_ID_LENGTH]


class HistoryBackend(abc.ABC):
    """Abstract interface for commit history storage."""

    # Global HEAD (most recent commit across all scopes — used for audit
    # display and clone response; NOT used for CAS, which is per-scope)
    @abc.abstractmethod
    def get_head_commit_id(self) -> str: ...

    @abc.abstractmethod
    def set_head_commit_id(self, cid: str) -> None: ...

    # Commit records
    @abc.abstractmethod
    def record(self, entry: dict) -> None:
        """Persist a commit entry. Entry MUST contain 'commit_id' key."""

    @abc.abstractmethod
    def get_entry(self, commit_id: str) -> dict | None: ...

    @abc.abstractmethod
    def get_since(self, since_commit_id: str,
                  limit: int = 0) -> list[dict]:
        """Return commits strictly after since_commit_id in linear order.

        When since_commit_id is empty, return all commits from the root.
        Ordering is by (created_at ASC, commit_id ASC) — commit_id acts
        as a tie-breaker for same-timestamp commits.
        """

    # Per-scope head (canonical CAS target; required, not optional)
    def get_scope_head_commit_id(self, _scope_path: str) -> str:
        return ""

    def set_scope_head_commit_id(self, _scope_path: str, _cid: str) -> None:
        ...

    # Per-scope Merkle root hash (canonical content fingerprint; used
    # alongside head_commit_id for CAS on the content side)
    def get_scope_hash(self, _scope_path: str) -> str:
        return ""

    def set_scope_hash(self, _scope_path: str, _h: str) -> None:
        ...

    # Global Merkle root (optional — only used by legacy root-hash-based
    # clone paths; scope-level storage has superseded it)
    def get_root_hash(self) -> str:
        return ""

    def set_root_hash(self, _h: str) -> None:
        ...


class FileSystemHistoryBackend(HistoryBackend):
    """JSON files in .mut-server/history/.

    Layout:
      latest                      -- head_commit_id (string)
      root                        -- global Merkle root (legacy)
      commits/{commit_id}.json    -- per-commit records
      scope_state/{scope}.json    -- {"head_commit_id": "...", "hash": "..."}
    """

    LATEST_FILE = "latest"
    ROOT_FILE = "root"
    COMMITS_DIR = "commits"
    SCOPE_STATE_DIR = "scope_state"

    def __init__(self, history_dir: Path):
        self.dir = history_dir

    def get_head_commit_id(self) -> str:
        f = self.dir / self.LATEST_FILE
        if not f.exists():
            return ""
        return f.read_text().strip()

    def set_head_commit_id(self, cid: str) -> None:
        write_text(self.dir / self.LATEST_FILE, cid)

    def _commit_path(self, commit_id: str) -> Path:
        d = self.dir / self.COMMITS_DIR
        d.mkdir(parents=True, exist_ok=True)
        return d / f"{commit_id}.json"

    def record(self, entry: dict) -> None:
        commit_id = entry.get("commit_id")
        if not commit_id:
            raise ValueError("history entry missing commit_id")
        write_json(self._commit_path(commit_id), entry)

    def get_entry(self, commit_id: str) -> dict | None:
        path = self._commit_path(commit_id)
        if path.exists():
            return read_json(path)
        return None

    def get_since(self, since_commit_id: str,
                  limit: int = 0) -> list[dict]:
        commits_dir = self.dir / self.COMMITS_DIR
        if not commits_dir.exists():
            return []

        entries: list[dict] = []
        for p in commits_dir.glob("*.json"):
            entry = read_json(p)
            if entry:
                entries.append(entry)

        entries.sort(key=lambda e: (e.get("time", ""), e.get("commit_id", "")))

        if since_commit_id:
            since_entry = self.get_entry(since_commit_id)
            if since_entry is None:
                return []
            since_key = (since_entry.get("time", ""),
                         since_entry.get("commit_id", ""))
            entries = [e for e in entries
                       if (e.get("time", ""), e.get("commit_id", "")) > since_key]

        if limit > 0:
            entries = entries[-limit:]
        return entries

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
        return {"head_commit_id": "", "hash": ""}

    def _save_scope_state(self, scope_path: str, state: dict) -> None:
        write_json(self._scope_state_path(scope_path), state)

    def get_scope_head_commit_id(self, scope_path: str) -> str:
        return self._load_scope_state(scope_path).get("head_commit_id", "")

    def set_scope_head_commit_id(self, scope_path: str, cid: str) -> None:
        state = self._load_scope_state(scope_path)
        state["head_commit_id"] = cid
        self._save_scope_state(scope_path, state)

    def get_scope_hash(self, scope_path: str) -> str:
        return self._load_scope_state(scope_path).get("hash", "")

    def set_scope_hash(self, scope_path: str, h: str) -> None:
        state = self._load_scope_state(scope_path)
        state["hash"] = h
        self._save_scope_state(scope_path, state)

    def get_root_hash(self) -> str:
        root_file = self.dir / self.ROOT_FILE
        if not root_file.exists():
            return ""
        return root_file.read_text().strip()

    def set_root_hash(self, h: str) -> None:
        write_text(self.dir / self.ROOT_FILE, h)


class HistoryManager:
    """Manages commit history via a pluggable HistoryBackend.

    Each commit is identified by a 16-hex-char commit_id. Per-scope
    head_commit_id and scope_hash are the canonical CAS targets.
    """

    def __init__(self, backend: HistoryBackend):
        self._backend = backend

    # ── Commit ID ─────────────────────────────

    @staticmethod
    def compute_commit_id(scope_path: str, scope_hash: str,
                          created_at_iso: str, who: str) -> str:
        return compute_commit_id(scope_path, scope_hash, created_at_iso, who)

    # ── Entry construction ────────────────────

    @staticmethod
    def _serialize_conflicts(conflicts: list) -> list:
        return [
            {"path": c.path, "strategy": c.strategy, "detail": c.detail,
             "kept": c.kept, "lost_content": c.lost_content, "lost_hash": c.lost_hash}
            for c in conflicts
        ]

    def _make_entry(self, commit_id: str, who: str, message: str,
                    scope_path: str, changes: list,
                    conflicts: list | None = None,
                    scope_hash: str = "",
                    root_hash: str = "",
                    created_at_iso: str = "") -> dict:
        if not created_at_iso:
            created_at_iso = datetime.now(timezone.utc).isoformat(timespec="microseconds")
        entry: dict = {
            "commit_id": commit_id,
            "who": who,
            "time": created_at_iso,
            "message": message,
            "scope": scope_path,
            "scope_hash": scope_hash,
            "changes": changes,
        }
        if root_hash:
            entry["root"] = root_hash
        if conflicts:
            entry["conflicts"] = self._serialize_conflicts(conflicts)
        return entry

    # ── Head ─────────────────────────────────

    def get_head_commit_id(self) -> str:
        return self._backend.get_head_commit_id()

    def set_head_commit_id(self, cid: str):
        self._backend.set_head_commit_id(cid)

    # ── Per-scope head + hash ────────────────

    def get_scope_head_commit_id(self, scope_path: str) -> str:
        return self._backend.get_scope_head_commit_id(scope_path)

    def set_scope_head_commit_id(self, scope_path: str, cid: str):
        self._backend.set_scope_head_commit_id(scope_path, cid)

    def get_scope_hash(self, scope_path: str) -> str:
        return self._backend.get_scope_hash(scope_path)

    def set_scope_hash(self, scope_path: str, h: str):
        self._backend.set_scope_hash(scope_path, h)

    # ── Global root hash (legacy / clone fallback) ──

    def get_root_hash(self) -> str:
        return self._backend.get_root_hash()

    def set_root_hash(self, h: str):
        self._backend.set_root_hash(h)

    # ── Record ───────────────────────────────

    def record(self, commit_id: str, who: str, message: str,
               scope_path: str, changes: list,
               conflicts: list | None = None,
               scope_hash: str = "",
               root_hash: str = "",
               created_at_iso: str = ""):
        entry = self._make_entry(
            commit_id, who, message, scope_path, changes,
            conflicts, scope_hash, root_hash, created_at_iso,
        )
        self._backend.record(entry)

    # ── Queries ──────────────────────────────

    def get_since(self, since_commit_id: str,
                  scope_path: str | None = None,
                  limit: int = 0) -> list:
        entries = self._backend.get_since(since_commit_id, limit)
        norm_scope = normalize_path(scope_path) if scope_path else None
        if norm_scope is None:
            return entries
        result = []
        for entry in entries:
            if not _scopes_overlap(entry.get("scope", "/"), norm_scope):
                continue
            result.append(_redact_for_scope(entry, norm_scope))
        return result

    def get_entry(self, commit_id: str) -> dict | None:
        return self._backend.get_entry(commit_id)

    # ── Async wrappers ───────────────────────

    async def async_get_head_commit_id(self) -> str:
        import asyncio
        return await asyncio.to_thread(self.get_head_commit_id)

    async def async_set_head_commit_id(self, cid: str):
        import asyncio
        await asyncio.to_thread(self.set_head_commit_id, cid)

    async def async_get_scope_head_commit_id(self, scope_path: str) -> str:
        import asyncio
        return await asyncio.to_thread(self.get_scope_head_commit_id, scope_path)

    async def async_set_scope_head_commit_id(self, scope_path: str, cid: str):
        import asyncio
        await asyncio.to_thread(self.set_scope_head_commit_id, scope_path, cid)

    async def async_get_scope_hash(self, scope_path: str) -> str:
        import asyncio
        return await asyncio.to_thread(self.get_scope_hash, scope_path)

    async def async_set_scope_hash(self, scope_path: str, h: str):
        import asyncio
        await asyncio.to_thread(self.set_scope_hash, scope_path, h)

    async def async_record(self, commit_id: str, who: str, message: str,
                           scope_path: str, changes: list,
                           conflicts: list | None = None,
                           scope_hash: str = "",
                           root_hash: str = "",
                           created_at_iso: str = ""):
        import asyncio
        await asyncio.to_thread(
            self.record, commit_id, who, message, scope_path,
            changes, conflicts, scope_hash, root_hash, created_at_iso,
        )

    async def async_get_since(self, since_commit_id: str,
                              scope_path: str | None = None,
                              limit: int = 0) -> list:
        import asyncio
        return await asyncio.to_thread(self.get_since, since_commit_id, scope_path, limit)

    async def async_get_entry(self, commit_id: str) -> dict | None:
        import asyncio
        return await asyncio.to_thread(self.get_entry, commit_id)

    async def async_get_root_hash(self) -> str:
        import asyncio
        return await asyncio.to_thread(self.get_root_hash)

    async def async_set_root_hash(self, h: str):
        import asyncio
        await asyncio.to_thread(self.set_root_hash, h)

    # ── Scope migration ──────────────────────

    def migrate_scope(self, old_scope_path: str,
                      new_scope_map: dict[str, str],
                      fallback_scope: str = "/") -> int:
        """Rewrite scope paths on commit entries matching the old path.

        Iterates every existing commit rather than a 1..latest version
        range because commit identity is no longer a sequential int.
        """
        old_norm = normalize_path(old_scope_path)
        entries = self._backend.get_since("", limit=0)
        count = 0
        for entry in entries:
            entry_scope = normalize_path(entry.get("scope", "/"))
            if entry_scope != old_norm:
                continue
            best_scope = self._pick_new_scope(
                entry.get("changes", []), new_scope_map, fallback_scope,
            )
            entry["scope"] = best_scope
            self._backend.record(entry)
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
