"""mut show — Display file content or tree entries at a given snapshot."""

import json

from mut.ops.repo import MutRepo
from mut.core import tree as tree_mod
from mut.foundation.error import SnapshotNotFoundError, MutError


def show(repo: MutRepo, snap_id: int, filepath: str) -> str:
    repo.check_init()
    snap = repo.snapshots.get(snap_id)
    if snap is None:
        raise SnapshotNotFoundError(f"snapshot #{snap_id} not found")

    parts = [p for p in filepath.split("/") if p]
    current_hash = snap["root"]

    for i, part in enumerate(parts):
        entries = tree_mod.read_tree(repo.store, current_hash)
        if part not in entries:
            raise MutError(f"path '{filepath}' not found in snapshot #{snap_id}")
        typ, h = entries[part]
        if i < len(parts) - 1:
            if typ != "T":
                raise MutError(f"'{part}' is not a directory")
            current_hash = h
        else:
            if typ == "T":
                return json.dumps(tree_mod.read_tree(repo.store, h), indent=2)
            return repo.store.get(h).decode()

    return json.dumps(tree_mod.read_tree(repo.store, current_hash), indent=2)
