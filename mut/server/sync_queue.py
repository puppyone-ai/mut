"""Queue-based serialization for scope operations (Lock-Free Sync).

Replaces asyncio.Lock with FIFO queues. Scopes with ancestor-descendant
path relationships share a queue (must serialize), while sibling scopes
get independent queues (can run in parallel).

Example:
  /           and /docs/          -> share queue (parent-child)
  /docs/      and /docs/internal/ -> share queue (parent-child)
  /docs/      and /src/           -> independent queues (siblings)
"""

from __future__ import annotations

import asyncio

from mut.foundation.config import normalize_path


def _paths_overlap(a: str, b: str) -> bool:
    """Two scope paths overlap if one is an ancestor of the other.

    Root path ("") overlaps with everything.
    """
    if not a or not b:
        return True
    return a.startswith(b + "/") or b.startswith(a + "/") or a == b


class ScopeQueue:
    """Per-scope-group FIFO queue that serializes overlapping scope ops.

    Operations on scopes whose paths overlap are serialized.
    Operations on non-overlapping scopes can proceed in parallel.
    """

    def __init__(self):
        self._queues: dict[str, asyncio.Lock] = {}
        self._groups: dict[str, str] = {}

    def _find_overlapping_key(self, scope_path: str) -> str | None:
        """Return the first existing queue key that overlaps with scope_path."""
        for key in self._queues:
            if _paths_overlap(scope_path, key):
                return key
        return None

    def _migrate_queue(self, old_key: str, new_key: str) -> asyncio.Lock:
        """Move a queue from old_key to new_key, updating all group refs."""
        lock = self._queues.pop(old_key)
        self._queues[new_key] = lock
        for gpath in self._groups:
            if self._groups[gpath] == old_key:
                self._groups[gpath] = new_key
        self._groups[old_key] = new_key
        self._groups[new_key] = new_key
        return lock

    def _get_or_create_queue(self, scope_path: str) -> tuple[str, asyncio.Lock]:
        """Get the queue for a scope path, creating/merging as needed."""
        cached_key = self._groups.get(scope_path)
        if cached_key and cached_key in self._queues:
            return cached_key, self._queues[cached_key]

        existing_key = self._find_overlapping_key(scope_path)
        if existing_key is None:
            lock = asyncio.Lock()
            self._queues[scope_path] = lock
            self._groups[scope_path] = scope_path
            return scope_path, lock

        # Use the shorter (more ancestral) path as the canonical key
        if len(scope_path) < len(existing_key):
            lock = self._migrate_queue(existing_key, scope_path)
            return scope_path, lock

        self._groups[scope_path] = existing_key
        return existing_key, self._queues[existing_key]

    async def acquire(self, scope_path: str) -> str:
        """Acquire the queue for a scope path. Returns the queue key.

        Blocks until all preceding operations on overlapping scopes complete.
        """
        scope_path = normalize_path(scope_path)
        _key, lock = self._get_or_create_queue(scope_path)
        await lock.acquire()
        return _key

    def release(self, scope_path: str) -> None:
        """Release the queue for a scope path."""
        scope_path = normalize_path(scope_path)
        key = self._groups.get(scope_path, scope_path)
        lock = self._queues.get(key)
        if lock and lock.locked():
            lock.release()
