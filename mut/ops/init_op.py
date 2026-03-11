"""mut init — Initialize a new .mut/ repository."""

from pathlib import Path

from mut.foundation.config import MUT_DIR, OBJECTS_DIR, SNAPSHOTS_FILE, MANIFEST_FILE, HEAD_FILE, CONFIG_FILE
from mut.foundation.fs import write_json, write_text
from mut.ops.repo import MutRepo


def init(workdir: str = ".") -> MutRepo:
    root = Path(workdir).resolve()
    mut = root / MUT_DIR
    if mut.exists():
        raise FileExistsError(f"already initialized: {mut}")

    (mut / OBJECTS_DIR).mkdir(parents=True)
    write_json(mut / SNAPSHOTS_FILE, [])
    write_json(mut / MANIFEST_FILE, {})
    write_json(mut / CONFIG_FILE, {"server": None, "scope": "/"})
    write_text(mut / HEAD_FILE, "0")

    return MutRepo(workdir)
