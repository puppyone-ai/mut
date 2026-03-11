"""mut status — Compare working directory against the last snapshot's manifest."""

from mut.ops.repo import MutRepo
from mut.core import manifest as manifest_mod
from mut.core.diff import diff_manifests


def status(repo: MutRepo) -> dict:
    """Return {changes: [...], unpushed: int}."""
    repo.check_init()

    old_manifest = manifest_mod.load(repo.mut_root)
    if not old_manifest and repo.snapshots.count() == 0:
        changes = [{"path": ".", "op": "new repo (no snapshots yet)"}]
    else:
        current_manifest = manifest_mod.generate(repo.workdir, repo.ignore)
        changes = diff_manifests(old_manifest, current_manifest)

    unpushed = len(repo.snapshots.get_unpushed())

    return {"changes": changes, "unpushed": unpushed}
