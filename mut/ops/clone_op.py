"""mut clone — First-time connection to server, download scope files.

1. POST /clone with token
2. Receive files + objects + history
3. Create workdir with .mut/ structure
"""

import base64
import json
from pathlib import Path

from mut.foundation.config import (
    MUT_DIR, OBJECTS_DIR, SNAPSHOTS_FILE, MANIFEST_FILE,
    HEAD_FILE, REMOTE_HEAD_FILE, CONFIG_FILE, TOKEN_FILE,
)
from mut.foundation.fs import write_json, write_text, mkdir_p, is_safe_path
from mut.foundation.transport import post_clone
from mut.core import tree as tree_mod
from mut.core import manifest as manifest_mod
from mut.ops.repo import MutRepo


def _extract_agent_id(token: str) -> str:
    """Read agent ID from token payload without signature verification."""
    try:
        payload_b64 = token.split(".")[1]
        padding = 4 - len(payload_b64) % 4
        if padding != 4:
            payload_b64 += "=" * padding
        payload = json.loads(base64.urlsafe_b64decode(payload_b64))
        return payload.get("agent", "anonymous")
    except Exception:
        return "anonymous"


def clone(server_url: str, token: str, workdir: str = None) -> MutRepo:
    """Clone a scope from the server into workdir.

    If workdir is None, the project name returned by the server is used
    as the directory name (like ``git clone`` derives it from the URL).
    """
    resp = post_clone(server_url, token)

    files_b64 = resp["files"]
    objects_b64 = resp["objects"]
    version = resp["version"]
    scope_info = resp["scope"]
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

    agent_id = _extract_agent_id(token)
    write_json(mut / CONFIG_FILE, {
        "server": server_url,
        "scope": scope_info["path"],
        "project": project_name,
        "agent_id": agent_id,
    })
    write_text(mut / TOKEN_FILE, token)

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
    write_text(mut / REMOTE_HEAD_FILE, str(version))  # server version, not snapshot ID

    return repo
