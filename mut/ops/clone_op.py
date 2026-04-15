"""mut clone — First-time connection to server, download scope files.

1. POST /clone with credential
2. Receive files + objects + history + agent_id
3. Create workdir with .mut/ structure
"""

import base64
from pathlib import Path

from mut.foundation.config import (
    MUT_DIR, OBJECTS_DIR, SNAPSHOTS_FILE,
    HEAD_FILE, REMOTE_HEAD_FILE, CONFIG_FILE,
)
from mut.foundation.fs import write_json, write_text, mkdir_p, is_safe_path
from mut.foundation.transport import MutClient
from mut.core import tree as tree_mod
from mut.core import manifest as manifest_mod
from mut.ops.repo import MutRepo


def clone(server_url: str, credential: str, workdir: str = None) -> MutRepo:
    """Clone a scope from the server into workdir."""
    client = MutClient(server_url, credential)
    resp = client.clone()

    files_b64 = resp["files"]
    objects_b64 = resp["objects"]
    version = resp["version"]
    project_name = resp.get("project", "project")

    if workdir is None:
        workdir = project_name

    root = Path(workdir).resolve()
    mut = root / MUT_DIR

    if mut.exists():
        raise FileExistsError(f"already initialized: {mut}")

    mkdir_p(root)
    for rel_path, b64data in files_b64.items():
        target = root / rel_path
        if not is_safe_path(root, target):
            raise ValueError(f"server sent unsafe path: {rel_path}")
        mkdir_p(target.parent)
        target.write_bytes(base64.b64decode(b64data))

    mkdir_p(mut / OBJECTS_DIR)
    for h, b64data in objects_b64.items():
        obj_path = mut / OBJECTS_DIR / h[:2] / h[2:]
        mkdir_p(obj_path.parent)
        obj_path.write_bytes(base64.b64decode(b64data))

    from mut.ops.init_op import CONFIG_VERSION
    write_json(mut / CONFIG_FILE, {
        "version": CONFIG_VERSION,
        "server": server_url,
        "credential": credential,
    })

    # Also save to global credential store for reuse across repos
    from mut.foundation.credentials import save_credential
    save_credential(server_url, credential)

    repo = MutRepo(workdir)

    root_hash = tree_mod.scan_dir(repo.store, root, repo.ignore)

    snap_entry = {
        "id": 1,
        "root": root_hash,
        "parent": None,
        "who": "clone",
        "message": f"cloned from {server_url}",
        "time": "",
        "pushed": True,
    }
    write_json(mut / SNAPSHOTS_FILE, [snap_entry])

    new_manifest = tree_mod.tree_to_flat(repo.store, root_hash)
    manifest_mod.save(mut, new_manifest)

    write_text(mut / HEAD_FILE, "1")
    write_text(mut / REMOTE_HEAD_FILE, str(version))

    return repo
