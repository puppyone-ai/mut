"""mut link access — Bind a local .mut/ repo to a PuppyOne Access Point.

Usage:
    mut link access <access_point_url> [root_dir_name]

Requires ``mut init`` to have been run first (`.mut/` must exist).

If *root_dir_name* is provided:
  - Creates the directory locally
  - Pushes an initial commit with the empty directory to the server
  - This ensures the server is not empty and has a scope to bind auth to

If *root_dir_name* is omitted:
  - Binds to the server as-is (server must already have content)
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

from mut.foundation.config import CONFIG_FILE, load_config, save_config
from mut.foundation.error import MutError
from mut.foundation.transport import MutClient
from mut.ops.init_op import CONFIG_VERSION
from mut.ops.repo import MutRepo


def _extract_credential(url: str) -> str:
    """Extract the access_key from an AP URL like /mut/ap/{key}/."""
    parts = url.rstrip("/").split("/")
    # URL pattern: .../mut/ap/{access_key}
    for i, p in enumerate(parts):
        if p == "ap" and i + 1 < len(parts):
            return parts[i + 1]
    # Fallback: last segment
    return parts[-1] if parts else ""


def link_access(
    repo: MutRepo,
    access_point_url: str,
    root_dir_name: str | None = None,
    credential_override: str | None = None,
) -> dict:
    """Link the local repo to a PuppyOne Access Point.

    Args:
        repo: An initialized MutRepo (must have .mut/).
        access_point_url: Full URL to the access point.
        root_dir_name: Optional directory name to create as scope.
        credential_override: Explicit credential (overrides URL extraction).

    Returns:
        dict with status info: server_version, scope_created, etc.
    """
    credential = credential_override or _extract_credential(access_point_url)
    client = MutClient(access_point_url, credential)

    # 1. Verify connection
    try:
        clone_resp = client.clone()
    except Exception as e:
        raise MutError(f"Cannot connect to server: {e}") from e

    server_version = clone_resp.get("version", 0)

    # 2. Update config
    save_config(repo.mut_root, {
        "version": CONFIG_VERSION,
        "server": access_point_url,
        "credential": credential,
    })

    # 3. Save credential globally
    from mut.foundation.credentials import save_credential
    save_credential(access_point_url, credential)

    # 4. Write REMOTE_HEAD
    from mut.foundation.config import REMOTE_HEAD_FILE
    from mut.foundation.fs import write_text
    write_text(repo.mut_root / REMOTE_HEAD_FILE, str(server_version))

    result = {
        "status": "linked",
        "server": access_point_url,
        "server_version": server_version,
        "scope_created": False,
    }

    # 5. If root_dir_name specified, create directory + push
    if root_dir_name:
        root_dir = Path(repo.workdir) / root_dir_name
        root_dir.mkdir(parents=True, exist_ok=True)

        # Create a .keep file so the directory is not empty in Merkle tree
        keep_file = root_dir / ".keep"
        if not keep_file.exists():
            keep_file.write_text("")

        # Build minimal tree with just this directory
        keep_content = b""
        keep_hash = hashlib.sha256(keep_content).hexdigest()[:16]

        inner_tree = json.dumps(
            {".keep": ["B", keep_hash]}, sort_keys=True
        ).encode()
        inner_hash = hashlib.sha256(inner_tree).hexdigest()[:16]

        root_tree = json.dumps(
            {root_dir_name: ["T", inner_hash]}, sort_keys=True
        ).encode()
        root_hash = hashlib.sha256(root_tree).hexdigest()[:16]

        # MutClient.push expects values as raw bytes (it base64-encodes internally)
        objects = {
            keep_hash: keep_content,
            inner_hash: inner_tree,
            root_hash: root_tree,
        }

        # Push to server
        try:
            push_resp = client.push(
                base_version=server_version,
                snapshots=[{
                    "id": 1,
                    "root": root_hash,
                    "message": f"init scope: {root_dir_name}/",
                    "who": "mut-init",
                    "time": "",
                }],
                objects=objects,
            )
            result["scope_created"] = True
            result["server_version"] = push_resp.get("version", server_version + 1)
        except Exception as e:
            # Push may fail if server already has content — that's OK
            result["scope_push_error"] = str(e)

    return result
