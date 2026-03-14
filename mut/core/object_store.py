"""Content-addressable object store on the filesystem.

Objects are stored as: <root>/objects/<first 2 hex chars>/<remaining hex chars>
Identical content is stored only once (deduplication via SHA-256).

Writes are atomic (temp + rename) to prevent corruption on crash.
Reads verify the hash to detect bitrot or truncated objects.
"""

import asyncio
from pathlib import Path

from mut.foundation.hash import hash_bytes
from mut.foundation.fs import atomic_write
from mut.foundation.error import ObjectNotFoundError


class ObjectStore:

    def __init__(self, objects_dir: Path):
        self.dir = objects_dir

    def _path_for(self, h: str) -> Path:
        return self.dir / h[:2] / h[2:]

    def put(self, data: bytes) -> str:
        h = hash_bytes(data)
        path = self._path_for(h)
        if not path.exists():
            atomic_write(path, data)
        return h

    def get(self, h: str) -> bytes:
        path = self._path_for(h)
        if not path.exists():
            raise ObjectNotFoundError(f"object not found: {h}")
        data = path.read_bytes()
        actual = hash_bytes(data)
        if actual != h:
            raise ObjectNotFoundError(
                f"object corrupt: expected {h}, got {actual}"
            )
        return data

    def exists(self, h: str) -> bool:
        return self._path_for(h).exists()

    def all_hashes(self) -> list:
        result = []
        if not self.dir.exists():
            return result
        for d in sorted(self.dir.iterdir()):
            if d.is_dir() and len(d.name) == 2:
                for f in sorted(d.iterdir()):
                    result.append(d.name + f.name)
        return result

    def count(self) -> tuple[int, int]:
        n, size = 0, 0
        if not self.dir.exists():
            return 0, 0
        for d in self.dir.iterdir():
            if d.is_dir():
                for f in d.iterdir():
                    n += 1
                    size += f.stat().st_size
        return n, size

    # ── Async methods (for server-side use) ──────────

    async def async_put(self, data: bytes) -> str:
        return await asyncio.to_thread(self.put, data)

    async def async_get(self, h: str) -> bytes:
        return await asyncio.to_thread(self.get, h)

    async def async_exists(self, h: str) -> bool:
        return await asyncio.to_thread(self.exists, h)

    async def async_all_hashes(self) -> list:
        return await asyncio.to_thread(self.all_hashes)

    async def async_count(self) -> tuple[int, int]:
        return await asyncio.to_thread(self.count)
