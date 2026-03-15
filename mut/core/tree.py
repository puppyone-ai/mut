"""Merkle tree operations: scan directories, read/write tree objects, restore files.

Tree object format (JSON stored as bytes in object store):
  {
    "filename": ["B", "<blob_hash>"],      // B = blob (file)
    "dirname":  ["T", "<tree_hash>"],      // T = tree (subdirectory)
    ...
  }
"""

import json
from pathlib import Path

from mut.core.object_store import ObjectStore
from mut.core.ignore import IgnoreRules
from mut.foundation.fs import rmtree

MAX_DEPTH = 100


def write_blob(store: ObjectStore, content: bytes) -> str:
    return store.put(content)


def write_tree(store: ObjectStore, entries: dict) -> str:
    return store.put(json.dumps(entries, sort_keys=True).encode())


def read_tree(store: ObjectStore, h: str) -> dict:
    return json.loads(store.get(h))


def scan_dir(store: ObjectStore, dirpath: Path, ignore: IgnoreRules,
             _depth: int = 0) -> str:
    """Recursively scan a directory, store all blobs/trees, return root tree hash."""
    if _depth > MAX_DEPTH:
        raise RecursionError(f"directory nesting exceeds {MAX_DEPTH} levels")
    entries = {}
    for child in sorted(dirpath.iterdir()):
        if ignore.should_ignore(child.name):
            continue
        if child.is_file():
            blob_hash = write_blob(store, child.read_bytes())
            entries[child.name] = ["B", blob_hash]
        elif child.is_dir():
            tree_hash = scan_dir(store, child, ignore, _depth + 1)
            entries[child.name] = ["T", tree_hash]
    return write_tree(store, entries)


def _cleanup_removed(dirpath: Path, existing: set, ignore: IgnoreRules):
    """Remove files/dirs in dirpath that are not in existing and not ignored."""
    if not dirpath.exists():
        return
    for child in dirpath.iterdir():
        if child.name in existing or ignore.should_ignore(child.name):
            continue
        if child.is_file():
            child.unlink()
        elif child.is_dir():
            rmtree(child)


def restore_tree(store: ObjectStore, tree_hash: str, dirpath: Path, ignore: IgnoreRules):
    """Restore files from a tree object into a real directory (iterative)."""
    stack = [(tree_hash, dirpath)]
    while stack:
        th, dp = stack.pop()
        entries = read_tree(store, th)
        existing = set(entries.keys())
        for name, (typ, h) in entries.items():
            target = dp / name
            if typ == "T":
                target.mkdir(exist_ok=True)
                stack.append((h, target))
            else:
                target.write_bytes(store.get(h))
        _cleanup_removed(dp, existing, ignore)


def tree_to_flat(store: ObjectStore, tree_hash: str, prefix: str = "") -> dict:
    """Flatten a tree into {relative_path: blob_hash} (iterative)."""
    result = {}
    stack = [(tree_hash, prefix)]
    while stack:
        th, pfx = stack.pop()
        entries = read_tree(store, th)
        for name, (typ, h) in entries.items():
            path = f"{pfx}{name}" if not pfx else f"{pfx}/{name}"
            if typ == "T":
                stack.append((h, path))
            else:
                result[path] = h
    return result


def collect_reachable_hashes(store: ObjectStore, tree_hash: str) -> set:
    """Collect all object hashes reachable from a tree (iterative)."""
    result = set()
    visited_trees: set[str] = set()
    stack = [tree_hash]
    while stack:
        th = stack.pop()
        if th in visited_trees:
            continue
        visited_trees.add(th)
        result.add(th)
        entries = read_tree(store, th)
        for _name, (typ, h) in entries.items():
            result.add(h)
            if typ == "T" and h not in visited_trees:
                stack.append(h)
    return result


def format_tree(store: ObjectStore, h: str, prefix: str = "", name: str = "") -> list[str]:
    """Pretty-print a tree structure. Returns list of lines."""
    entries = read_tree(store, h)
    lines = []
    if name:
        lines.append(f"{prefix}{name}/  ({h})")
    else:
        lines.append(f"{prefix}.  ({h})")
    items = sorted(entries.items())
    for i, (child_name, (typ, child_h)) in enumerate(items):
        is_last = (i == len(items) - 1)
        connector = "└── " if is_last else "├── "
        if typ == "T":
            lines.extend(format_tree(store, child_h, prefix + connector, child_name))
        else:
            lines.append(f"{prefix}{connector}{child_name}  ({child_h})")
    return lines
