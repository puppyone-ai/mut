"""mut stats — Repository statistics."""

from mut.ops.repo import MutRepo


def stats(repo: MutRepo) -> dict:
    repo.check_init()
    n, size = repo.store.count()
    return {
        "objects": n,
        "bytes": size,
        "snapshots": repo.snapshots.count(),
    }
