"""mut cat — Read file content from the latest snapshot's Merkle tree."""

from __future__ import annotations

from mut.ops.repo import MutRepo
from mut.core import tree as tree_mod
from mut.foundation.config import normalize_path
from mut.foundation.error import ObjectNotFoundError


def cat(repo: MutRepo, path: str) -> bytes:
    """Return raw bytes of the file at *path* in the latest snapshot.

    Navigates the Merkle tree to locate the blob.
    Raises ``ObjectNotFoundError`` if the path does not exist.
    """
    repo.check_init()

    snap = repo.snapshots.latest()
    if snap is None:
        raise ObjectNotFoundError("no snapshots exist — nothing to read")

    parts = [p for p in normalize_path(path).split("/") if p]
    if not parts:
        raise ObjectNotFoundError("empty path")

    current_hash = snap["root"]
    for i, part in enumerate(parts):
        entries = tree_mod.read_tree(repo.store, current_hash)
        if part not in entries:
            raise ObjectNotFoundError(f"path '{path}' not found in latest snapshot")
        typ, h = entries[part]
        if i < len(parts) - 1:
            if typ != "T":
                raise ObjectNotFoundError(f"'{part}' is not a directory")
            current_hash = h
        else:
            if typ == "T":
                raise ObjectNotFoundError(
                    f"'{path}' is a directory, not a file")
            return repo.store.get(h)

    # Should not reach here, but satisfy type checker
    raise ObjectNotFoundError(f"path '{path}' not found")
