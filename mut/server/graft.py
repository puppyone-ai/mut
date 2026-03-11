"""Subtree grafting engine — the core of Mut's server-side merge.

Grafting replaces a subtree at a given path in the full project tree,
then recalculates hashes upward to the root.

Example:
  Old root tree has /src/ with hash AAA.
  Agent-A pushes a new /src/ subtree with hash BBB.
  Graft replaces the /src/ node, rebuilds parent hashes → new root hash.
"""

import json

from mut.core.object_store import ObjectStore


def graft_subtree(store: ObjectStore, old_root_hash: str,
                  scope_path: str, new_subtree_hash: str) -> str:
    """Replace the subtree at scope_path with new_subtree_hash.

    Returns the new root hash with all intermediate hashes recomputed.

    scope_path: e.g. "src" or "src/components" (no leading/trailing slash)
    If scope_path is empty, the new_subtree_hash *is* the new root.
    """
    if not scope_path:
        return new_subtree_hash

    parts = scope_path.strip("/").split("/")
    return _graft_recursive(store, old_root_hash, parts, new_subtree_hash)


def _graft_recursive(store: ObjectStore, tree_hash: str,
                     path_parts: list, new_hash: str) -> str:
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
