"""MutRepo: the central handle for all operations on a .mut/ repository."""

from pathlib import Path

from mut.foundation.config import MUT_DIR, OBJECTS_DIR, SNAPSHOTS_FILE
from mut.foundation.error import NotARepoError
from mut.core.object_store import ObjectStore
from mut.core.snapshot import SnapshotChain
from mut.core.ignore import IgnoreRules


class MutRepo:
    """Encapsulates all paths and core objects for one repository."""

    def __init__(self, workdir: str = "."):
        self.workdir = Path(workdir).resolve()
        self.mut_root = self.workdir / MUT_DIR
        self.store = ObjectStore(self.mut_root / OBJECTS_DIR)
        self.snapshots = SnapshotChain(self.mut_root / SNAPSHOTS_FILE)
        self.ignore = IgnoreRules(self.workdir)

    def check_init(self):
        if not self.mut_root.exists():
            raise NotARepoError("not a mut repository (run 'mut init' first)")
