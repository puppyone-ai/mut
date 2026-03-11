"""mut diff — Compare two snapshots."""

from mut.ops.repo import MutRepo
from mut.core.diff import diff_trees
from mut.foundation.error import SnapshotNotFoundError


def diff(repo: MutRepo, id1: int, id2: int) -> list[dict]:
    repo.check_init()
    s1 = repo.snapshots.get(id1)
    s2 = repo.snapshots.get(id2)
    if s1 is None:
        raise SnapshotNotFoundError(f"snapshot #{id1} not found")
    if s2 is None:
        raise SnapshotNotFoundError(f"snapshot #{id2} not found")
    return diff_trees(repo.store, s1["root"], s2["root"])
