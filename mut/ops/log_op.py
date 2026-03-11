"""mut log — Show snapshot history (newest first)."""

from mut.ops.repo import MutRepo


def log(repo: MutRepo) -> list[dict]:
    repo.check_init()
    return list(reversed(repo.snapshots.load_all()))
