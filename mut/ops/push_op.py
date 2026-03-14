"""mut push — Push unpushed local snapshots to the server.

1. Find unpushed snapshots
2. Collect new objects that server might not have
3. POST /push with objects + snapshots
4. Update REMOTE_HEAD + mark pushed
"""

from __future__ import annotations

from mut.ops.repo import MutRepo
from mut.foundation.config import REMOTE_HEAD_FILE, TOKEN_FILE, load_config
from mut.foundation.fs import read_text, write_text
from mut.foundation.transport import MutClient
from mut.core import manifest as manifest_mod
from mut.core.diff import diff_manifests


def push(repo: MutRepo) -> dict:
    repo.check_init()

    config = load_config(repo.mut_root)
    server_url = config.get("server")

    if not server_url:
        return _local_push(repo)

    token = read_text(repo.mut_root / TOKEN_FILE)
    client = MutClient(server_url, token)

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
        return {"status": "up-to-date", "pushed": 0}

    remote_head_path = repo.mut_root / REMOTE_HEAD_FILE
    base_version = int(read_text(remote_head_path)) if remote_head_path.exists() else 0

    all_hashes = repo.store.all_hashes()
    negotiate_resp = client.negotiate(all_hashes)
    missing_hashes = set(negotiate_resp.get("missing", all_hashes))

    objects: dict[str, bytes] = {
        h: repo.store.get(h) for h in all_hashes if h in missing_hashes
    }

    snap_data = [
        {"id": s["id"], "root": s["root"], "message": s.get("message", ""),
         "who": s.get("who", ""), "time": s.get("time", "")}
        for s in unpushed
    ]

    resp = client.push(base_version, snap_data, objects)

    server_version = resp.get("version", base_version)
    latest_id = unpushed[-1]["id"]
    repo.snapshots.mark_pushed(latest_id)

    merged = resp.get("merged", False)
    others_pushed = server_version > base_version + 1

    if merged or others_pushed:
        # Our local working directory doesn't reflect the full server state.
        # Either: (a) server merged our changes with others' (merged=True),
        # or (b) other agents pushed between our base_version and now.
        # Keep REMOTE_HEAD at base_version so the next pull fetches
        # the complete server state including other agents' changes.
        write_text(remote_head_path, str(base_version))
    else:
        write_text(remote_head_path, str(server_version))

    result: dict = {
        "status": "pushed",
        "pushed": len(unpushed),
        "latest_id": latest_id,
        "server_version": server_version,
    }
    if merged:
        result["merged"] = True
        result["conflicts"] = resp.get("conflicts", 0)
    return result


def _local_push(repo: MutRepo) -> dict:
    """Fallback for repos without a server (local-only mode)."""
    unpushed = repo.snapshots.get_unpushed()
    if not unpushed:
        return {"status": "up-to-date", "pushed": 0}

    latest = unpushed[-1]
    repo.snapshots.mark_pushed(latest["id"])
    write_text(repo.mut_root / REMOTE_HEAD_FILE, str(latest["id"]))

    return {
        "status": "pushed",
        "pushed": len(unpushed),
        "latest_id": latest["id"],
        "message": "(local-only mode — no server configured)",
    }
