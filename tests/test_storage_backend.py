"""Tests for the StorageBackend abstraction in core/object_store.py."""

import pytest

from mut.core.object_store import (
    ObjectStore, StorageBackend, FileSystemBackend,
)
from mut.foundation.error import ObjectNotFoundError
from mut.foundation.hash import hash_bytes


# ── FileSystemBackend direct tests ─────────────

class TestFileSystemBackend:
    @pytest.fixture
    def backend(self, tmp_path):
        return FileSystemBackend(tmp_path / "objects")

    def test_put_and_get(self, backend):
        data = b"test data"
        h = hash_bytes(data)
        backend.put(h, data)
        assert backend.get(h) == data

    def test_exists(self, backend):
        data = b"exists test"
        h = hash_bytes(data)
        assert not backend.exists(h)
        backend.put(h, data)
        assert backend.exists(h)

    def test_get_nonexistent(self, backend):
        with pytest.raises(ObjectNotFoundError):
            backend.get("deadbeef12345678")

    def test_all_hashes(self, backend):
        h1 = hash_bytes(b"one")
        h2 = hash_bytes(b"two")
        backend.put(h1, b"one")
        backend.put(h2, b"two")
        hashes = backend.all_hashes()
        assert h1 in hashes
        assert h2 in hashes

    def test_count(self, backend):
        backend.put(hash_bytes(b"a"), b"a")
        backend.put(hash_bytes(b"b"), b"b")
        n, size = backend.count()
        assert n == 2
        assert size > 0

    def test_idempotent_put(self, backend):
        data = b"same"
        h = hash_bytes(data)
        backend.put(h, data)
        backend.put(h, data)  # should not raise
        assert backend.get(h) == data


# ── InMemoryBackend (custom backend) ───────────

class InMemoryBackend(StorageBackend):
    """A custom in-memory backend for testing pluggability."""

    def __init__(self):
        self._store: dict[str, bytes] = {}

    def get(self, h: str) -> bytes:
        if h not in self._store:
            raise ObjectNotFoundError(f"not found: {h}")
        return self._store[h]

    def put(self, h: str, data: bytes) -> None:
        self._store[h] = data

    def exists(self, h: str) -> bool:
        return h in self._store

    def all_hashes(self) -> list[str]:
        return sorted(self._store.keys())

    def count(self) -> tuple[int, int]:
        n = len(self._store)
        size = sum(len(v) for v in self._store.values())
        return n, size

    def delete(self, h: str) -> bool:
        if h in self._store:
            del self._store[h]
            return True
        return False


class TestCustomBackend:
    def test_object_store_with_memory_backend(self, tmp_path):
        backend = InMemoryBackend()
        store = ObjectStore(tmp_path / "unused", backend=backend)

        h = store.put(b"hello world")
        assert store.get(h) == b"hello world"
        assert store.exists(h)
        assert not store.exists("0000000000000000")

    def test_deduplication(self, tmp_path):
        backend = InMemoryBackend()
        store = ObjectStore(tmp_path / "unused", backend=backend)
        h1 = store.put(b"same")
        h2 = store.put(b"same")
        assert h1 == h2
        n, _ = store.count()
        assert n == 1

    def test_all_hashes(self, tmp_path):
        backend = InMemoryBackend()
        store = ObjectStore(tmp_path / "unused", backend=backend)
        h1 = store.put(b"alpha")
        h2 = store.put(b"beta")
        hashes = store.all_hashes()
        assert h1 in hashes
        assert h2 in hashes

    def test_integrity_check(self, tmp_path):
        """ObjectStore verifies hash on read even with custom backend."""
        backend = InMemoryBackend()
        store = ObjectStore(tmp_path / "unused", backend=backend)
        h = store.put(b"real data")
        # Corrupt the backend directly
        backend._store[h] = b"corrupted"
        with pytest.raises(ObjectNotFoundError, match="corrupt"):
            store.get(h)
