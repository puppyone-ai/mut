"""mut checkout — Restore working directory to a specific snapshot."""

from mut.ops.repo import MutRepo
from mut.core import tree as tree_mod
from mut.core import manifest as manifest_mod
from mut.foundation.config import HEAD_FILE
from mut.foundation.fs import write_text
from mut.foundation.error import SnapshotNotFoundError


def checkout(repo: MutRepo, snap_id: int) -> dict:
    repo.check_init()
    snap = repo.snapshots.get(snap_id)
    if snap is None:
        raise SnapshotNotFoundError(f"snapshot #{snap_id} not found")

    tree_mod.restore_tree(repo.store, snap["root"], repo.workdir, repo.ignore)

    new_manifest = tree_mod.tree_to_flat(repo.store, snap["root"])
    manifest_mod.save(repo.mut_root, new_manifest)
    write_text(repo.mut_root / HEAD_FILE, str(snap["id"]))

    return snap
