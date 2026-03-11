"""mut commit — Local-only snapshot of the working directory.

1. Scan workdir → build Merkle tree (store new objects)
2. Create snapshot entry (pushed=False)
3. Update manifest and HEAD
"""

from mut.ops.repo import MutRepo
from mut.core import tree as tree_mod
from mut.core import manifest as manifest_mod
from mut.foundation.config import HEAD_FILE
from mut.foundation.fs import write_text


def commit(repo: MutRepo, message: str, who: str = "anonymous"):
    repo.check_init()

    root_hash = tree_mod.scan_dir(repo.store, repo.workdir, repo.ignore)
    snap = repo.snapshots.create(root_hash, who, message, pushed=False)
    if snap is None:
        return None

    new_manifest = tree_mod.tree_to_flat(repo.store, root_hash)
    manifest_mod.save(repo.mut_root, new_manifest)
    write_text(repo.mut_root / HEAD_FILE, str(snap["id"]))

    return snap
