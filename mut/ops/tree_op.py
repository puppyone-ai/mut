"""mut tree — Show the Merkle tree structure of a snapshot."""

from mut.ops.repo import MutRepo
from mut.core import tree as tree_mod
from mut.foundation.error import SnapshotNotFoundError


def tree(repo: MutRepo, snap_id: int) -> str:
    repo.check_init()
    snap = repo.snapshots.get(snap_id)
    if snap is None:
        raise SnapshotNotFoundError(f"snapshot #{snap_id} not found")
    lines = tree_mod.format_tree(repo.store, snap["root"])
    return "\n".join(lines)
