"""Client-side rollback operation — request server to revert to a commit."""

from __future__ import annotations

from mut.foundation.config import load_config, get_client_credential
from mut.foundation.transport import MutClient
from mut.core.protocol import RollbackRequest
from mut.ops.repo import MutRepo


def rollback(repo: MutRepo, target_commit_id: str) -> dict:
    """Request the server to rollback the current scope to a historical commit.

    The server creates a new commit whose tree matches the target snapshot,
    preserving history linearly (no rewrite). Returns the server response dict.
    """
    repo.check_init()
    config = load_config(repo.mut_root)
    server_url = config.get("server")
    if not server_url:
        raise ValueError("no server configured — rollback requires a remote")
    credential, user_identity = get_client_credential(repo.mut_root, repo.workdir)
    client = MutClient(server_url, credential, user_identity=user_identity)

    req = RollbackRequest(target_commit_id=target_commit_id)
    resp = client.post("/rollback", req.to_dict())

    if resp.get("error"):
        from mut.foundation.error import MutError
        raise MutError(resp["error"])

    return resp
