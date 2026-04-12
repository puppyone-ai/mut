"""mut ls — List entries in the latest snapshot's Merkle tree (or workdir)."""

from __future__ import annotations

import os

from mut.ops.repo import MutRepo
from mut.core import tree as tree_mod
from mut.foundation.config import normalize_path


def ls(repo: MutRepo, path: str = "") -> list[dict]:
    """List entries at *path* in the latest snapshot's tree.

    Returns a list of dicts: ``[{"name": ..., "type": "file"|"dir", "hash": ...}]``.
    If no snapshot exists, lists the working directory directly.
    """
    repo.check_init()

    snap = repo.snapshots.latest()
    if snap is None:
        return _ls_workdir(repo, path)

    root_hash = snap["root"]
    current_hash = root_hash

    if path:
        parts = [p for p in normalize_path(path).split("/") if p]
        for part in parts:
            entries = tree_mod.read_tree(repo.store, current_hash)
            if part not in entries:
                from mut.foundation.error import ObjectNotFoundError
                raise ObjectNotFoundError(f"path '{path}' not found in latest snapshot")
            typ, h = entries[part]
            if typ != "T":
                from mut.foundation.error import ObjectNotFoundError
                raise ObjectNotFoundError(f"'{part}' is not a directory")
            current_hash = h

    entries = tree_mod.read_tree(repo.store, current_hash)
    result = []
    for name, (typ, h) in sorted(entries.items()):
        result.append({
            "name": name,
            "type": "dir" if typ == "T" else "file",
            "hash": h,
        })
    return result


def _ls_workdir(repo: MutRepo, path: str) -> list[dict]:
    """Fallback: list files from the working directory when no snapshot exists."""
    target = repo.workdir
    if path:
        target = target / path
    if not target.is_dir():
        from mut.foundation.error import ObjectNotFoundError
        raise ObjectNotFoundError(f"path '{path}' not found in working directory")

    result = []
    for child in sorted(target.iterdir()):
        if repo.ignore.should_ignore(child.name):
            continue
        result.append({
            "name": child.name,
            "type": "dir" if child.is_dir() else "file",
            "hash": "",
        })
    return result
