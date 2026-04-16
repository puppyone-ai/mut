"""Subtree grafting engine — the core of Mut's server-side merge.

Grafting replaces a subtree at a given path in the full project tree,
then recalculates hashes upward to the root.

Example:
  Old root tree has /src/ with hash AAA.
  Agent-A pushes a new /src/ subtree with hash BBB.
  Graft replaces the /src/ node, rebuilds parent hashes → new root hash.
"""

from __future__ import annotations

import json

from mut.foundation.config import normalize_path
from mut.core.object_store import ObjectStore


def graft_subtree(
    store: ObjectStore, old_root_hash: str,
    scope_path: str, new_subtree_hash: str
) -> str:
    """Replace the subtree at scope_path with new_subtree_hash.

    Returns the new root hash with all intermediate hashes recomputed.

    scope_path: e.g. "src" or "src/components" (no leading/trailing slash)
    If scope_path is empty, the new_subtree_hash *is* the new root.
    """
    if not scope_path:
        return new_subtree_hash

    parts = normalize_path(scope_path).split("/")
    return _graft_recursive(store, old_root_hash, parts, new_subtree_hash)


def _graft_recursive(
    store: ObjectStore, tree_hash: str,
    path_parts: list, new_hash: str
) -> str:
    """Recursively descend the tree, replace the target node, rebuild upward."""
    entries = json.loads(store.get(tree_hash))

    target = path_parts[0]
    remaining = path_parts[1:]

    if target not in entries:
        if remaining:
            empty_tree_hash = store.put(json.dumps({}, sort_keys=True).encode())
            child_hash = _graft_recursive(store, empty_tree_hash, remaining, new_hash)
        else:
            child_hash = new_hash
        entries[target] = ["T", child_hash]
    elif remaining:
        child_hash = _graft_recursive(store, entries[target][1], remaining, new_hash)
        entries[target] = ["T", child_hash]
    else:
        entries[target] = ["T", new_hash]

    return store.put(json.dumps(entries, sort_keys=True).encode())


async def async_graft_subtree(
    store: ObjectStore, old_root_hash: str,
    scope_path: str, new_subtree_hash: str
) -> str:
    """Async version of graft_subtree."""
    if not scope_path:
        return new_subtree_hash

    parts = normalize_path(scope_path).split("/")
    return await _async_graft_recursive(store, old_root_hash, parts, new_subtree_hash)


async def _async_graft_recursive(
    store: ObjectStore, tree_hash: str,
    path_parts: list, new_hash: str
) -> str:
    """Async recursive graft."""
    data = await store.async_get(tree_hash)
    entries = json.loads(data)

    target = path_parts[0]
    remaining = path_parts[1:]

    if target not in entries:
        if remaining:
            empty_tree_hash = await store.async_put(json.dumps({}, sort_keys=True).encode())
            child_hash = await _async_graft_recursive(store, empty_tree_hash, remaining, new_hash)
        else:
            child_hash = new_hash
        entries[target] = ["T", child_hash]
    elif remaining:
        child_hash = await _async_graft_recursive(store, entries[target][1], remaining, new_hash)
        entries[target] = ["T", child_hash]
    else:
        entries[target] = ["T", new_hash]

    return await store.async_put(json.dumps(entries, sort_keys=True).encode())


def graft_or_merge_subtree(
    store: ObjectStore, old_root_hash: str,
    scope_path: str, old_scope_hash: str, new_scope_hash: str,
) -> str:
    """Conflict-aware graft: replace or merge the subtree at scope_path.
    
    If the subtree at scope_path in old_root hasn't changed since old_scope_hash,
    this is a simple replacement (fast path). If it has changed (another scope
    modified files in this path), we three-way merge to preserve both changes.
    
    Returns the new root hash.
    """
    if not scope_path:
        return new_scope_hash

    norm = normalize_path(scope_path)

    current_subtree = _navigate_to_hash(store, old_root_hash, norm)

    if current_subtree == old_scope_hash or current_subtree is None:
        return graft_subtree(store, old_root_hash, scope_path, new_scope_hash)

    from mut.core.merge import merge_file_sets
    from mut.core.tree import tree_to_flat

    base_files = _safe_flatten(store, old_scope_hash)
    current_files = _safe_flatten(store, current_subtree)
    new_files = _safe_flatten(store, new_scope_hash)

    merged_files, _ = merge_file_sets(base_files, current_files, new_files)

    merged_hash = _build_tree_from_flat(store, merged_files)
    return graft_subtree(store, old_root_hash, scope_path, merged_hash)


def _navigate_to_hash(store: ObjectStore, root_hash: str, scope_path: str) -> str | None:
    """Navigate from root to a subtree and return its hash."""
    if not scope_path:
        return root_hash
    parts = scope_path.split("/")
    current = root_hash
    for part in parts:
        if not part:
            continue
        try:
            entries = json.loads(store.get(current))
        except Exception:
            return None
        if part not in entries:
            return None
        typ, h = entries[part]
        if typ != "T":
            return None
        current = h
    return current


def _safe_flatten(store: ObjectStore, tree_hash: str) -> dict[str, bytes]:
    """Flatten a tree hash to {path: bytes}, returning empty dict on error."""
    if not tree_hash:
        return {}
    try:
        from mut.core.tree import tree_to_flat
        flat = tree_to_flat(store, tree_hash)
        return {path: store.get(h) for path, h in flat.items()}
    except Exception:
        return {}


def _build_tree_from_flat(store: ObjectStore, files: dict[str, bytes]) -> str:
    """Build a Merkle tree from a flat file dict."""
    nested: dict = {}
    for path, content in files.items():
        parts = path.split("/")
        d = nested
        for p in parts[:-1]:
            d = d.setdefault(p, {})
        blob_hash = store.put(content)
        d[parts[-1]] = ("B", blob_hash)
    return _build_nested(store, nested)


def _build_nested(store: ObjectStore, node: dict) -> str:
    entries: dict = {}
    for name, val in sorted(node.items()):
        if isinstance(val, tuple):
            entries[name] = list(val)
        else:
            sub_hash = _build_nested(store, val)
            entries[name] = ["T", sub_hash]
    return store.put(json.dumps(entries, sort_keys=True).encode())
