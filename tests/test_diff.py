"""Unit tests for core/diff.py — tree and manifest diffing."""

import pytest

from mut.core.object_store import ObjectStore
from mut.core.ignore import IgnoreRules
from mut.core import tree as tree_mod
from mut.core.diff import diff_trees, diff_manifests


@pytest.fixture
def store(tmp_path):
    return ObjectStore(tmp_path / "objects")


def test_diff_manifests_added():
    changes = diff_manifests({}, {"new.txt": "abc123"})
    assert len(changes) == 1
    assert changes[0]["op"] == "added"


def test_diff_manifests_deleted():
    changes = diff_manifests({"old.txt": "abc123"}, {})
    assert len(changes) == 1
    assert changes[0]["op"] == "deleted"


def test_diff_manifests_modified():
    changes = diff_manifests({"f.txt": "aaa"}, {"f.txt": "bbb"})
    assert len(changes) == 1
    assert changes[0]["op"] == "modified"


def test_diff_manifests_unchanged():
    changes = diff_manifests({"f.txt": "same"}, {"f.txt": "same"})
    assert len(changes) == 0


def test_diff_trees_identical(store, tmp_path):
    d = tmp_path / "proj"
    d.mkdir()
    (d / "a.txt").write_bytes(b"content")
    ignore = IgnoreRules(d)
    h = tree_mod.scan_dir(store, d, ignore)
    assert diff_trees(store, h, h) == []


def test_diff_trees_modified(store, tmp_path):
    d = tmp_path / "proj"
    d.mkdir()
    (d / "a.txt").write_bytes(b"v1")
    ignore = IgnoreRules(d)
    h1 = tree_mod.scan_dir(store, d, ignore)

    (d / "a.txt").write_bytes(b"v2")
    h2 = tree_mod.scan_dir(store, d, ignore)

    changes = diff_trees(store, h1, h2)
    assert len(changes) == 1
    assert changes[0]["op"] == "modified"
    assert changes[0]["path"] == "a.txt"


def test_diff_trees_added_and_deleted(store, tmp_path):
    d = tmp_path / "proj"
    d.mkdir()
    (d / "a.txt").write_bytes(b"keep")
    (d / "b.txt").write_bytes(b"remove")
    ignore = IgnoreRules(d)
    h1 = tree_mod.scan_dir(store, d, ignore)

    (d / "b.txt").unlink()
    (d / "c.txt").write_bytes(b"new")
    h2 = tree_mod.scan_dir(store, d, ignore)

    changes = diff_trees(store, h1, h2)
    ops = {c["path"]: c["op"] for c in changes}
    assert ops["b.txt"] == "deleted"
    assert ops["c.txt"] == "added"
    assert "a.txt" not in ops
