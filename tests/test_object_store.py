"""Unit tests for core/object_store.py — content-addressable storage."""

import tempfile
from pathlib import Path
import pytest

from mut.core.object_store import ObjectStore
from mut.foundation.error import ObjectNotFoundError


@pytest.fixture
def store(tmp_path):
    return ObjectStore(tmp_path / "objects")


def test_put_and_get(store):
    data = b"hello world"
    h = store.put(data)
    assert isinstance(h, str)
    assert len(h) > 0
    assert store.get(h) == data


def test_deduplication(store):
    data = b"same content"
    h1 = store.put(data)
    h2 = store.put(data)
    assert h1 == h2


def test_different_content_different_hash(store):
    h1 = store.put(b"alpha")
    h2 = store.put(b"beta")
    assert h1 != h2


def test_get_nonexistent(store):
    with pytest.raises(ObjectNotFoundError):
        store.get("deadbeef12345678")


def test_exists(store):
    h = store.put(b"check exists")
    assert store.exists(h)
    assert not store.exists("0000000000000000")


def test_all_hashes(store):
    h1 = store.put(b"one")
    h2 = store.put(b"two")
    hashes = store.all_hashes()
    assert h1 in hashes
    assert h2 in hashes


def test_count(store):
    store.put(b"x")
    store.put(b"y")
    n, size = store.count()
    assert n == 2
    assert size > 0


def test_corrupt_object_detected(store):
    data = b"important data"
    h = store.put(data)
    obj_path = store._backend._path_for(h)
    obj_path.write_bytes(b"corrupted!")
    with pytest.raises(ObjectNotFoundError, match="corrupt"):
        store.get(h)
