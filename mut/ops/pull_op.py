"""mut pull — Pull changes from the server since REMOTE_HEAD.

1. Check for local dirty state (uncommitted changes)
2. POST /pull with since_version + have_hashes
3. Receive new files + objects (server skips objects we already have)
4. Update working directory, objects, snapshots, manifest, HEAD, REMOTE_HEAD
"""

import base64

from mut.ops.repo import MutRepo
from mut.foundation.config import (
    REMOTE_HEAD_FILE, TOKEN_FILE, HEAD_FILE, load_config,
)
from mut.foundation.error import DirtyWorkdirError
from mut.foundation.fs import read_text, write_text, mkdir_p
from mut.foundation.transport import post_pull
from mut.core import tree as tree_mod
from mut.core import manifest as manifest_mod
from mut.core.diff import diff_manifests


def pull(repo: MutRepo, force: bool = False) -> dict:
    repo.check_init()

    config = load_config(repo.mut_root)
    server_url = config.get("server")

    if not server_url:
        return {"status": "up-to-date", "pulled": 0,
                "message": "(local-only mode — no server configured)"}

    if not force:
        old_manifest = manifest_mod.load(repo.mut_root)
        cur_manifest = manifest_mod.generate(repo.workdir, repo.ignore)
        dirty = diff_manifests(old_manifest, cur_manifest)
        if dirty:
            raise DirtyWorkdirError(
                f"pull would overwrite {len(dirty)} uncommitted change(s). "
                "Commit first, or use --force."
            )

    token = read_text(repo.mut_root / TOKEN_FILE)

    remote_head_path = repo.mut_root / REMOTE_HEAD_FILE
    since_version = int(read_text(remote_head_path)) if remote_head_path.exists() else 0

    have_hashes = repo.store.all_hashes()
    resp = post_pull(server_url, token, since_version, have_hashes=have_hashes)

    if resp["status"] == "up-to-date":
        return {"status": "up-to-date", "pulled": 0}

    files_b64 = resp.get("files", {})
    objects_b64 = resp.get("objects", {})
    server_version = resp.get("version", since_version)

    for h, b64data in objects_b64.items():
        data = base64.b64decode(b64data)
        repo.store.put(data)

    server_paths = set(files_b64.keys())
    for rel_path, b64data in files_b64.items():
        target = repo.workdir / rel_path
        mkdir_p(target.parent)
        target.write_bytes(base64.b64decode(b64data))

    old_manifest = manifest_mod.load(repo.mut_root)
    for old_path in old_manifest:
        if old_path not in server_paths:
            victim = repo.workdir / old_path
            if victim.exists():
                victim.unlink()
                parent = victim.parent
                while parent != repo.workdir and parent.exists() and not any(parent.iterdir()):
                    parent.rmdir()
                    parent = parent.parent

    root_hash = tree_mod.scan_dir(repo.store, repo.workdir, repo.ignore)

    repo.snapshots.create(root_hash, "pull", f"pulled from server (v{server_version})",
                          pushed=True)

    new_manifest = tree_mod.tree_to_flat(repo.store, root_hash)
    manifest_mod.save(repo.mut_root, new_manifest)

    latest_snap = repo.snapshots.latest()
    write_text(repo.mut_root / HEAD_FILE, str(latest_snap["id"]))
    write_text(remote_head_path, str(server_version))

    return {
        "status": "updated",
        "pulled": len(files_b64),
        "server_version": server_version,
    }
