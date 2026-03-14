"""Content-addressable object store with pluggable backends.

The ObjectStore wraps a StorageBackend (defaulting to FileSystemBackend)
so that the core VCS logic is decoupled from the underlying storage.

Future backends (S3, SQLite, …) implement the same interface.
"""

from __future__ import annotations

import abc
import asyncio
from pathlib import Path

from mut.foundation.hash import hash_bytes
from mut.foundation.fs import atomic_write
from mut.foundation.error import ObjectNotFoundError


class StorageBackend(abc.ABC):
    """Abstract interface for content-addressable blob storage."""

    @abc.abstractmethod
    def get(self, h: str) -> bytes:
        """Retrieve raw bytes by hash. Raises ObjectNotFoundError."""

    @abc.abstractmethod
    def put(self, h: str, data: bytes) -> None:
        """Store data under its hash (idempotent)."""

    @abc.abstractmethod
    def exists(self, h: str) -> bool:
        """Check whether an object with this hash exists."""

    @abc.abstractmethod
    def all_hashes(self) -> list[str]:
        """Return every stored hash (for negotiation, GC, etc.)."""

    @abc.abstractmethod
    def count(self) -> tuple[int, int]:
        """Return (object_count, total_bytes)."""


class FileSystemBackend(StorageBackend):
    """Default backend: objects/<first 2 hex chars>/<remaining hex chars>."""

    def __init__(self, objects_dir: Path):
        self.dir = objects_dir

    def _path_for(self, h: str) -> Path:
        return self.dir / h[:2] / h[2:]

    def get(self, h: str) -> bytes:
        path = self._path_for(h)
        if not path.exists():
            raise ObjectNotFoundError(f"object not found: {h}")
        return path.read_bytes()

    def put(self, h: str, data: bytes) -> None:
        path = self._path_for(h)
        if not path.exists():
            atomic_write(path, data)

    def exists(self, h: str) -> bool:
        return self._path_for(h).exists()

    def all_hashes(self) -> list[str]:
        result: list[str] = []
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


class ObjectStore:
    """Content-addressable store that hashes on write and verifies on read.

    Delegates actual I/O to a StorageBackend (FileSystemBackend by default).
    """

    def __init__(self, objects_dir: Path, backend: StorageBackend | None = None):
        self.dir = objects_dir
        self._backend = backend or FileSystemBackend(objects_dir)

    def put(self, data: bytes) -> str:
        h = hash_bytes(data)
        self._backend.put(h, data)
        return h

    def get(self, h: str) -> bytes:
        data = self._backend.get(h)
        actual = hash_bytes(data)
        if actual != h:
            raise ObjectNotFoundError(
                f"object corrupt: expected {h}, got {actual}"
            )
        return data

    def exists(self, h: str) -> bool:
        return self._backend.exists(h)

    def all_hashes(self) -> list[str]:
        return self._backend.all_hashes()

    def count(self) -> tuple[int, int]:
        return self._backend.count()

    # ── Async methods (for server-side use) ──────────

    async def async_put(self, data: bytes) -> str:
        return await asyncio.to_thread(self.put, data)

    async def async_get(self, h: str) -> bytes:
        return await asyncio.to_thread(self.get, h)

    async def async_exists(self, h: str) -> bool:
        return await asyncio.to_thread(self.exists, h)

    async def async_all_hashes(self) -> list[str]:
        return await asyncio.to_thread(self.all_hashes)

    async def async_count(self) -> tuple[int, int]:
        return await asyncio.to_thread(self.count)
