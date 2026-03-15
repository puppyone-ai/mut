"""Tests for server/graft.py — subtree grafting engine."""

import json
import pytest

from mut.core.object_store import ObjectStore
from mut.core import tree as tree_mod
from mut.server.graft import graft_subtree


@pytest.fixture
def store(tmp_path):
    return ObjectStore(tmp_path / "objects")


def _make_tree(store, entries: dict) -> str:
    """Helper: create a tree from {name: bytes} or {name: subtree_hash}."""
    tree_entries = {}
    for name, val in entries.items():
        if isinstance(val, bytes):
            h = store.put(val)
            tree_entries[name] = ["B", h]
        elif isinstance(val, dict):
            sub_hash = _make_tree(store, val)
            tree_entries[name] = ["T", sub_hash]
        else:
            # Assume it's a tree hash string
            tree_entries[name] = ["T", val]
    return store.put(json.dumps(tree_entries, sort_keys=True).encode())


class TestGraftSubtree:
    def test_empty_scope_replaces_root(self, store):
        old_root = _make_tree(store, {"a.txt": b"old"})
        new_root = _make_tree(store, {"b.txt": b"new"})
        result = graft_subtree(store, old_root, "", new_root)
        assert result == new_root

    def test_graft_single_level(self, store):
        old_root = _make_tree(store, {
            "src": {"main.py": b"old_main"},
            "readme.txt": b"readme",
        })
        new_src = _make_tree(store, {"main.py": b"new_main"})
        new_root = graft_subtree(store, old_root, "src", new_src)

        # Verify readme.txt is unchanged
        entries = tree_mod.read_tree(store, new_root)
        assert "readme.txt" in entries
        assert entries["readme.txt"][0] == "B"

        # Verify src was replaced
        src_entries = tree_mod.read_tree(store, entries["src"][1])
        content = store.get(src_entries["main.py"][1])
        assert content == b"new_main"

    def test_graft_nested_path(self, store):
        old_root = _make_tree(store, {
            "src": {"components": {"button.py": b"old_button"}},
        })
        new_components = _make_tree(store, {"button.py": b"new_button"})
        new_root = graft_subtree(store, old_root, "src/components", new_components)

        # Navigate to verify
        root_entries = tree_mod.read_tree(store, new_root)
        src_entries = tree_mod.read_tree(store, root_entries["src"][1])
        comp_entries = tree_mod.read_tree(store, src_entries["components"][1])
        content = store.get(comp_entries["button.py"][1])
        assert content == b"new_button"

    def test_graft_creates_missing_intermediate(self, store):
        old_root = _make_tree(store, {"readme.txt": b"hi"})
        new_deep = _make_tree(store, {"app.py": b"app"})
        new_root = graft_subtree(store, old_root, "src/deep", new_deep)

        root_entries = tree_mod.read_tree(store, new_root)
        assert "src" in root_entries
        assert "readme.txt" in root_entries

    def test_graft_preserves_siblings(self, store):
        old_root = _make_tree(store, {
            "src": {"main.py": b"main"},
            "docs": {"api.md": b"api docs"},
            "config.json": b'{"key": "value"}',
        })
        new_src = _make_tree(store, {"main.py": b"updated_main"})
        new_root = graft_subtree(store, old_root, "src", new_src)

        root_entries = tree_mod.read_tree(store, new_root)
        assert "docs" in root_entries
        assert "config.json" in root_entries
        # docs should be unchanged
        docs_entries = tree_mod.read_tree(store, root_entries["docs"][1])
        assert store.get(docs_entries["api.md"][1]) == b"api docs"

    def test_root_hash_changes(self, store):
        old_root = _make_tree(store, {"src": {"f.py": b"old"}})
        new_src = _make_tree(store, {"f.py": b"new"})
        new_root = graft_subtree(store, old_root, "src", new_src)
        assert new_root != old_root
