"""Unit tests for core/tree.py — Merkle tree operations."""

import pytest
from pathlib import Path

from mut.core.object_store import ObjectStore
from mut.core.ignore import IgnoreRules
from mut.core import tree as tree_mod


@pytest.fixture
def store(tmp_path):
    return ObjectStore(tmp_path / "objects")


def test_write_and_read_tree(store):
    h1 = store.put(b"file1 content")
    h2 = store.put(b"file2 content")
    entries = {"a.txt": ["B", h1], "b.txt": ["B", h2]}
    tree_hash = tree_mod.write_tree(store, entries)
    read_back = tree_mod.read_tree(store, tree_hash)
    assert read_back == entries


def test_scan_and_restore(store, tmp_path):
    workdir = tmp_path / "project"
    workdir.mkdir()
    (workdir / "main.py").write_text("print('hello')")
    (workdir / "sub").mkdir()
    (workdir / "sub" / "data.txt").write_text("some data")

    ignore = IgnoreRules(workdir)
    root = tree_mod.scan_dir(store, workdir, ignore)

    restore_dir = tmp_path / "restored"
    restore_dir.mkdir()
    tree_mod.restore_tree(store, root, restore_dir, ignore)

    assert (restore_dir / "main.py").read_text() == "print('hello')"
    assert (restore_dir / "sub" / "data.txt").read_text() == "some data"


def test_tree_to_flat(store, tmp_path):
    workdir = tmp_path / "proj"
    workdir.mkdir()
    (workdir / "a.py").write_bytes(b"aaa")
    (workdir / "d").mkdir()
    (workdir / "d" / "b.py").write_bytes(b"bbb")

    ignore = IgnoreRules(workdir)
    root = tree_mod.scan_dir(store, workdir, ignore)
    flat = tree_mod.tree_to_flat(store, root)

    assert "a.py" in flat
    assert "d/b.py" in flat
    assert len(flat) == 2


def test_collect_reachable_hashes(store, tmp_path):
    workdir = tmp_path / "proj"
    workdir.mkdir()
    (workdir / "x.txt").write_bytes(b"xxx")
    (workdir / "sub").mkdir()
    (workdir / "sub" / "y.txt").write_bytes(b"yyy")

    ignore = IgnoreRules(workdir)
    root = tree_mod.scan_dir(store, workdir, ignore)
    reachable = tree_mod.collect_reachable_hashes(store, root)

    assert root in reachable
    total_objects = len(store.all_hashes())
    assert len(reachable) == total_objects


def test_restore_deletes_extra_files(store, tmp_path):
    workdir = tmp_path / "proj"
    workdir.mkdir()
    (workdir / "keep.txt").write_bytes(b"keep")

    ignore = IgnoreRules(workdir)
    root = tree_mod.scan_dir(store, workdir, ignore)

    (workdir / "extra.txt").write_bytes(b"should be deleted")
    tree_mod.restore_tree(store, root, workdir, ignore)

    assert (workdir / "keep.txt").exists()
    assert not (workdir / "extra.txt").exists()
