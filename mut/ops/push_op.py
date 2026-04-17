"""mut push — Push unpushed local snapshots to the server.

1. Reconcile REMOTE_HEAD with the server (Bug #6 self-heal)
2. Find unpushed snapshots (rebuilt from id 1 if the server lost history)
3. Collect new objects that the server might not have
4. POST /push with objects + snapshots
5. Update REMOTE_HEAD + mark pushed
"""

from __future__ import annotations

from mut.ops.repo import MutRepo
from mut.foundation.config import REMOTE_HEAD_FILE, load_config, get_client_credential
from mut.foundation.fs import read_text, write_text
from mut.foundation.transport import MutClient
from mut.core import manifest as manifest_mod
from mut.core import tree as tree_mod
from mut.core.diff import diff_manifests
from mut.core.protocol import NegotiateResponse


def _reconcile_with_server(repo: MutRepo, client: MutClient,
                           remote_head_path) -> dict:
    """Ask the server whether our local ``REMOTE_HEAD`` still exists in
    history and realign local state when it doesn't.

    This is the Git-style "reference discovery" step — per
    ``docs/design/mut-git-alignment.md``, the client must treat the
    server's current head as the single source of truth about what's
    been pushed. A non-empty local REMOTE_HEAD that the server can't
    find means the server was truncated / restored / manually cleaned,
    and the local "pushed" watermark is lying. In that case we reset
    the watermark, write the server's actual head back to REMOTE_HEAD,
    and let ``get_unpushed()`` surface every local commit again.

    Returns a dict with diagnostic info (``reset`` — number of
    snapshots whose pushed flag was cleared; ``server_head`` — the
    server's current head after reconciliation).
    """
    local_remote_head = (read_text(remote_head_path).strip()
                         if remote_head_path.exists() else "")

    probe = client.negotiate(hashes=[], remote_head=local_remote_head)
    resp = NegotiateResponse.from_dict(probe)

    info = {
        "reset": 0,
        "server_head": resp.server_head_commit_id,
        "recognized": resp.remote_head_recognized,
    }

    if local_remote_head and not resp.remote_head_recognized:
        info["reset"] = repo.snapshots.reset_pushed_watermark()
        write_text(remote_head_path, resp.server_head_commit_id)
    return info


def push(repo: MutRepo) -> dict:
    repo.check_init()

    config = load_config(repo.mut_root)
    server_url = config.get("server")

    if not server_url:
        return _local_push(repo)

    credential, user_identity = get_client_credential(repo.mut_root, repo.workdir)
    client = MutClient(server_url, credential, user_identity=user_identity)
    remote_head_path = repo.mut_root / REMOTE_HEAD_FILE

    reconcile_info = _reconcile_with_server(repo, client, remote_head_path)

    unpushed = repo.snapshots.get_unpushed()
    if not unpushed:
        old_manifest = manifest_mod.load(repo.mut_root)
        cur_manifest = manifest_mod.generate(repo.workdir, repo.ignore)
        dirty = diff_manifests(old_manifest, cur_manifest)
        if dirty:
            return {
                "status": "dirty",
                "pushed": 0,
                "uncommitted": len(dirty),
            }
        result: dict = {"status": "up-to-date", "pushed": 0}
        if reconcile_info["reset"]:
            result["watermark_reset"] = reconcile_info["reset"]
        return result

    base_commit_id = (read_text(remote_head_path).strip()
                      if remote_head_path.exists() else "")

    relevant_hashes: set[str] = set()
    for s in unpushed:
        relevant_hashes.update(
            tree_mod.collect_reachable_hashes(repo.store, s["root"])
        )
    relevant_list = list(relevant_hashes)

    negotiate_resp = client.negotiate(relevant_list)
    missing_hashes = set(negotiate_resp.get("missing", relevant_list))

    objects: dict[str, bytes] = {
        h: repo.store.get(h) for h in relevant_hashes if h in missing_hashes
    }

    snap_data = [
        {"id": s["id"], "root": s["root"], "message": s.get("message", ""),
         "who": s.get("who", ""), "time": s.get("time", "")}
        for s in unpushed
    ]

    resp = client.push(base_commit_id, snap_data, objects)

    server_commit_id = resp.get("commit_id", base_commit_id)
    latest_id = unpushed[-1]["id"]
    merged = resp.get("merged", False)

    # Fast-forward: local snapshot content equals server commit content,
    # so stamping server_commit_id onto local snapshots is accurate.
    # Merged: local content and server commit content DIFFER, so we
    # only flip the pushed flag. The next pull will create a fresh
    # local snapshot tagged with the merged commit id.
    if merged:
        repo.snapshots.mark_pushed(latest_id)
        write_text(remote_head_path, base_commit_id)
    else:
        repo.snapshots.mark_pushed(latest_id, server_commit_id=server_commit_id)
        write_text(remote_head_path, server_commit_id)

    result: dict = {
        "status": "pushed",
        "pushed": len(unpushed),
        "latest_id": latest_id,
        "server_commit_id": server_commit_id,
    }
    if merged:
        result["merged"] = True
        result["conflicts"] = resp.get("conflicts", 0)
    if reconcile_info["reset"]:
        result["watermark_reset"] = reconcile_info["reset"]
    return result


def _local_push(repo: MutRepo) -> dict:
    unpushed = repo.snapshots.get_unpushed()
    if not unpushed:
        return {"status": "up-to-date", "pushed": 0}

    latest = unpushed[-1]
    repo.snapshots.mark_pushed(latest["id"])
    write_text(repo.mut_root / REMOTE_HEAD_FILE, "")

    return {
        "status": "pushed",
        "pushed": len(unpushed),
        "latest_id": latest["id"],
        "message": "(local-only mode — no server configured)",
    }
