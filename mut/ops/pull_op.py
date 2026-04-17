"""mut pull — Sync local state with the server.

If unpushed commits exist, push them first (server-side merge), then pull
the merged result. This keeps conflict resolution entirely on the server.

Steps:
1. Reject uncommitted changes (user must commit or --force)
2. If unpushed commits exist → auto-push (server merges)
3. POST /pull with since_commit_id + have_hashes
4. Overwrite working directory with server result
5. Update objects, snapshots, manifest, HEAD, REMOTE_HEAD
"""

from __future__ import annotations

import base64

from mut.ops.repo import MutRepo
from mut.foundation.config import (
    REMOTE_HEAD_FILE, HEAD_FILE, load_config, get_client_credential,
)
from mut.foundation.error import DirtyWorkdirError
from mut.foundation.fs import read_text, write_text, mkdir_p
from mut.foundation.transport import MutClient
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

    push_result = _auto_push_if_needed(repo)

    credential, user_identity = get_client_credential(repo.mut_root, repo.workdir)
    client = MutClient(server_url, credential, user_identity=user_identity)

    remote_head_path = repo.mut_root / REMOTE_HEAD_FILE
    since_commit_id = (read_text(remote_head_path).strip()
                       if remote_head_path.exists() else "")

    have_hashes = repo.store.all_hashes()
    resp = client.pull(since_commit_id, have_hashes=have_hashes)

    if resp["status"] == "up-to-date":
        result: dict = {"status": "up-to-date", "pulled": 0}
        if push_result:
            result["push"] = push_result
        return result

    files_b64 = resp.get("files", {})
    objects_b64 = resp.get("objects", {})
    head_commit_id = resp.get("head_commit_id", since_commit_id)

    for h, b64data in objects_b64.items():
        repo.store.put(base64.b64decode(b64data))

    server_paths = set(files_b64.keys())
    for rel_path, b64data in files_b64.items():
        target = repo.workdir / rel_path
        mkdir_p(target.parent)
        target.write_bytes(base64.b64decode(b64data))

    _remove_deleted_files(repo, server_paths)

    root_hash = tree_mod.scan_dir(repo.store, repo.workdir, repo.ignore)

    label = head_commit_id if head_commit_id else "(empty)"
    repo.snapshots.create(root_hash, "pull",
                          f"pulled from server (#{label})",
                          pushed=True,
                          server_commit_id=head_commit_id)

    new_manifest = tree_mod.tree_to_flat(repo.store, root_hash)
    manifest_mod.save(repo.mut_root, new_manifest)

    latest_snap = repo.snapshots.latest()
    write_text(repo.mut_root / HEAD_FILE, str(latest_snap["id"]))
    write_text(remote_head_path, head_commit_id)

    result = {
        "status": "updated",
        "pulled": len(files_b64),
        "server_commit_id": head_commit_id,
    }
    if push_result:
        result["push"] = push_result
    return result


def _auto_push_if_needed(repo: MutRepo) -> dict | None:
    """Push unpushed commits before pulling. Returns push result or None."""
    unpushed = repo.snapshots.get_unpushed()
    if not unpushed:
        return None

    from mut.ops import push_op
    return push_op.push(repo)


def _remove_deleted_files(repo: MutRepo, server_paths: set[str]) -> None:
    old_manifest = manifest_mod.load(repo.mut_root)
    for old_path in old_manifest:
        if old_path not in server_paths:
            victim = repo.workdir / old_path
            if victim.exists():
                victim.unlink()
                _clean_empty_parents(victim.parent, repo.workdir)


def _clean_empty_parents(parent, stop_at) -> None:
    while parent != stop_at and parent.exists() and not any(parent.iterdir()):
        parent.rmdir()
        parent = parent.parent
