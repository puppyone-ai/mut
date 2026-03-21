"""Server-side request handlers (sync versions) — one function per endpoint.

Each handler receives (repo, auth_context, body) and returns a dict.
The auth_context is produced by the Authenticator — handlers never
touch credentials directly.

Errors are raised as MutError subclasses; the dispatch layer maps
them to HTTP status codes.
"""

from __future__ import annotations

import base64
import json

from mut.core.merge import merge_file_sets
from mut.core.object_store import ObjectStore
from mut.core.protocol import (
    CloneResponse, PushRequest, PushResponse, PullRequest, PullResponse,
    NegotiateRequest, NegotiateResponse, RollbackRequest, RollbackResponse,
    PullVersionRequest, ScopeInfo, normalize_path,
)
from mut.core.scope import check_path_permission
from mut.core import tree as tree_mod
from mut.foundation.error import (
    PermissionDenied, LockError, ObjectNotFoundError,
)
from mut.server.graft import graft_subtree
from mut.server.repo import ServerRepo


# ── Clone ──────────────────────────────────────

def handle_clone(repo: ServerRepo, auth: dict, _body: dict) -> dict:
    scope = auth["_scope"]

    files_raw = repo.list_scope_files(scope)
    files_b64 = {path: base64.b64encode(data).decode()
                 for path, data in files_raw.items()}

    scope_tree_hash = repo.build_scope_tree(scope)
    scope_hashes = tree_mod.collect_reachable_hashes(repo.store, scope_tree_hash)
    objects_b64 = {h: base64.b64encode(repo.store.get(h)).decode()
                   for h in scope_hashes}

    MAX_CLONE_HISTORY = 200
    latest = repo.get_latest_version()
    history = repo.get_history_since(0, scope_path=scope["path"],
                                     limit=MAX_CLONE_HISTORY)

    repo.record_audit("clone", auth["agent"], {
        "scope": scope["path"],
        "files": len(files_raw),
        "version": latest,
    })

    resp = CloneResponse(
        agent_id=auth["agent"],
        project=repo.get_project_name(),
        files=files_b64,
        objects=objects_b64,
        history=history,
        version=latest,
        scope=ScopeInfo(
            path=scope["path"],
            exclude=scope.get("exclude", []),
            mode=scope.get("mode", "rw"),
        ),
    )
    return resp.to_dict()


# ── Push ───────────────────────────────────────

def handle_push(repo: ServerRepo, auth: dict, body: dict) -> dict:
    scope = auth["_scope"]

    if scope.get("mode", "r") == "r":
        raise PermissionDenied("scope is read-only")

    scope_id = scope["id"]
    if not repo.acquire_lock(scope_id):
        raise LockError("scope is locked by another push, retry later")

    try:
        return _push_locked(repo, scope, auth, body)
    except Exception as exc:
        repo.record_audit("push_error", auth["agent"], {
            "scope": scope["path"], "error": str(exc),
        })
        raise
    finally:
        repo.release_lock(scope_id)


def _push_locked(repo: ServerRepo, scope: dict, auth: dict,
                 body: dict) -> dict:
    req = PushRequest.from_dict(body)

    _store_incoming_objects(repo.store, req.objects)

    if not req.snapshots:
        return PushResponse(
            status="ok", version=repo.get_latest_version(),
        ).to_dict()

    their_root_hash = req.snapshots[-1]["root"]
    their_files = _flatten_tree_to_bytes(repo.store, their_root_hash)
    scope_prefix = normalize_path(scope["path"])

    rejected = _validate_scope_paths(scope, scope_prefix, their_files)
    if rejected:
        repo.record_audit("push_rejected", auth["agent"], {
            "scope": scope["path"], "rejected_paths": rejected,
        })
        raise PermissionDenied(f"paths outside scope: {rejected[:5]}")

    our_files = repo.list_scope_files(scope)
    current_version = repo.get_latest_version()

    merged_files, merge_conflicts = _resolve_conflicts(
        repo, scope, req.base_version, current_version,
        our_files, their_files,
    )

    if merge_conflicts:
        repo.record_audit("merge_conflict", auth["agent"], {
            "scope": scope["path"],
            "base_version": req.base_version,
            "server_version": current_version,
            "conflicts": [
                {"path": c.path, "strategy": c.strategy,
                 "detail": c.detail, "kept": c.kept,
                 "lost_content": c.lost_content, "lost_hash": c.lost_hash}
                for c in merge_conflicts
            ],
        })

    _apply_merged_files(repo, scope, our_files, merged_files)

    new_root = _graft_scope_tree(repo, scope, scope_prefix)

    new_version = current_version + 1
    changes = _compute_changeset(scope_prefix, our_files, merged_files)

    repo.record_history(
        new_version, auth["agent"], req.snapshots[-1].get("message", ""),
        scope["path"], changes,
        conflicts=merge_conflicts, root_hash=new_root,
    )
    repo.set_latest_version(new_version)
    repo.set_root_hash(new_root)

    repo.record_audit("push", auth["agent"], {
        "scope": scope["path"],
        "snapshots": len(req.snapshots),
        "version": new_version,
        "root": new_root,
        "merged": bool(merge_conflicts),
        "conflict_count": len(merge_conflicts),
    })

    return PushResponse(
        status="ok",
        version=new_version,
        pushed=len(req.snapshots),
        root=new_root,
        merged=bool(merge_conflicts),
        conflicts=len(merge_conflicts),
    ).to_dict()


# ── Negotiate ──────────────────────────────────

def handle_negotiate(repo: ServerRepo, _auth: dict, body: dict) -> dict:
    req = NegotiateRequest.from_dict(body)
    missing = [h for h in req.hashes if not repo.store.exists(h)]
    return NegotiateResponse(missing=missing).to_dict()


# ── Pull ───────────────────────────────────────

def handle_pull(repo: ServerRepo, auth: dict, body: dict) -> dict:
    scope = auth["_scope"]
    req = PullRequest.from_dict(body)

    latest = repo.get_latest_version()

    if req.since_version >= latest:
        return PullResponse(status="up-to-date", version=latest).to_dict()

    files_raw = repo.list_scope_files(scope)
    files_b64 = {p: base64.b64encode(d).decode()
                 for p, d in files_raw.items()}

    scope_tree_hash = repo.build_scope_tree(scope)
    scope_hashes = tree_mod.collect_reachable_hashes(repo.store, scope_tree_hash)
    have_hashes = set(req.have_hashes)
    objects_b64 = {h: base64.b64encode(repo.store.get(h)).decode()
                   for h in scope_hashes if h not in have_hashes}

    MAX_PULL_HISTORY = 200
    history = repo.get_history_since(req.since_version,
                                     scope_path=scope["path"],
                                     limit=MAX_PULL_HISTORY)

    repo.record_audit("pull", auth["agent"], {
        "scope": scope["path"],
        "since": req.since_version,
        "version": latest,
    })

    return PullResponse(
        status="updated",
        version=latest,
        files=files_b64,
        objects=objects_b64,
        history=history,
    ).to_dict()


# ── Pull Version ──────────────────────────────

def handle_pull_version(repo: ServerRepo, auth: dict, body: dict) -> dict:
    """Pull files at a specific historical version (not just latest)."""
    scope = auth["_scope"]
    req = PullVersionRequest.from_dict(body)
    target_version = req.version

    current = repo.get_latest_version()
    if target_version <= 0 or target_version > current:
        raise ValueError(
            f"invalid version {target_version} (current: {current})"
        )

    entry = repo.get_history_entry(target_version)
    if not entry or not entry.get("root"):
        raise ValueError(f"version {target_version} has no root hash")

    target_root = entry["root"]
    if not repo.store.exists(target_root):
        raise ObjectNotFoundError(
            f"root object for version {target_version} not found"
        )

    scope_prefix = normalize_path(scope["path"])
    parts = scope_prefix.split("/") if scope_prefix else []
    subtree_hash = _navigate_tree(repo.store, target_root, parts)

    files_b64 = {}
    objects_b64 = {}
    if subtree_hash:
        from mut.core import tree as tree_mod
        flat = tree_mod.tree_to_flat(repo.store, subtree_hash)
        for path, h in flat.items():
            files_b64[path] = base64.b64encode(repo.store.get(h)).decode()
        reachable = tree_mod.collect_reachable_hashes(repo.store, subtree_hash)
        objects_b64 = {h: base64.b64encode(repo.store.get(h)).decode()
                       for h in reachable}

    repo.record_audit("pull_version", auth["agent"], {
        "scope": scope["path"],
        "version": target_version,
    })

    return {
        "status": "ok",
        "version": target_version,
        "files": files_b64,
        "objects": objects_b64,
    }


# ── Rollback ──────────────────────────────────

def handle_rollback(repo: ServerRepo, auth: dict, body: dict) -> dict:
    """Rollback to a historical version by creating a revert commit.

    The version chain continues forward — rollback creates a NEW version
    whose content equals the target historical version's snapshot.
    """
    scope = auth["_scope"]
    if scope.get("mode", "r") == "r":
        raise PermissionDenied("scope is read-only")

    req = RollbackRequest.from_dict(body)
    target_version = req.target_version
    current_version = repo.get_latest_version()

    if target_version <= 0 or target_version > current_version:
        raise ValueError(
            f"invalid target version {target_version} "
            f"(current: {current_version})"
        )
    if target_version == current_version:
        return RollbackResponse(
            status="already-at-version",
            new_version=current_version,
            target_version=target_version,
        ).to_dict()

    target_entry = repo.get_history_entry(target_version)
    if not target_entry or not target_entry.get("root"):
        raise ValueError(f"version {target_version} has no root hash")

    target_root = target_entry["root"]
    if not repo.store.exists(target_root):
        raise ObjectNotFoundError(
            f"root object for version {target_version} not found"
        )

    # Compute changes between current and target for the audit trail
    scope_prefix = normalize_path(scope["path"])
    parts = scope_prefix.split("/") if scope_prefix else []

    current_files = repo.list_scope_files(scope)

    target_subtree = _navigate_tree(repo.store, target_root, parts)
    target_files = {}
    if target_subtree:
        target_files = _flatten_tree_to_bytes(repo.store, target_subtree)

    # Compute changeset
    changes = _compute_changeset(scope_prefix, current_files, target_files)

    # Apply target files to current/
    _apply_merged_files(repo, scope, current_files, target_files)

    # Graft and update version
    new_root = _graft_scope_tree(repo, scope, scope_prefix)
    new_version = current_version + 1

    repo.record_history(
        new_version, auth["agent"],
        f"rollback to v{target_version}",
        scope["path"], changes,
        root_hash=new_root,
    )
    repo.set_latest_version(new_version)
    repo.set_root_hash(new_root)

    repo.record_audit("rollback", auth["agent"], {
        "scope": scope["path"],
        "target_version": target_version,
        "new_version": new_version,
    })

    return RollbackResponse(
        status="rolled-back",
        new_version=new_version,
        target_version=target_version,
        changes=changes,
    ).to_dict()


# ── Shared helpers ─────────────────────────────

def _store_incoming_objects(store: ObjectStore, objects_b64: dict) -> None:
    for h, b64data in objects_b64.items():
        store.put(base64.b64decode(b64data))


def _validate_scope_paths(scope: dict, scope_prefix: str,
                          files: dict) -> list[str]:
    rejected: list[str] = []
    for rel_path in files:
        full_path = (f"{scope_prefix}/{rel_path}"
                     if scope_prefix else rel_path)
        if not check_path_permission(scope, full_path, "write"):
            rejected.append(full_path)
    return rejected


def _resolve_conflicts(repo, scope, base_version, current_version,
                       our_files, their_files):
    if base_version < current_version:
        base_files = _get_base_files(repo, scope, base_version)
        return merge_file_sets(base_files, our_files, their_files)
    return their_files, []


def _apply_merged_files(repo, scope, old_scope_files, merged_files):
    for old_path in old_scope_files:
        if old_path not in merged_files:
            repo.delete_scope_file(scope, old_path)
    repo.write_scope_files(scope, merged_files)


def _graft_scope_tree(repo, scope, scope_prefix):
    new_scope_tree_hash = repo.build_scope_tree(scope)
    old_root = repo.get_root_hash()

    if old_root and repo.store.exists(old_root):
        return graft_subtree(
            repo.store, old_root, scope_prefix, new_scope_tree_hash,
        )
    return repo.build_full_tree()


def _compute_changeset(scope_prefix, old_files, merged_files):
    changes = []
    for rel_path in merged_files:
        full = f"{scope_prefix}/{rel_path}" if scope_prefix else rel_path
        action = "add" if rel_path not in old_files else "update"
        changes.append({"path": full, "action": action})
    for old_path in old_files:
        if old_path not in merged_files:
            full = (f"{scope_prefix}/{old_path}"
                    if scope_prefix else old_path)
            changes.append({"path": full, "action": "delete"})
    return changes


def _flatten_tree_to_bytes(store, tree_hash):
    flat_hashes = tree_mod.tree_to_flat(store, tree_hash)
    return {path: store.get(h) for path, h in flat_hashes.items()}


def _navigate_tree(store, tree_hash, parts):
    if not parts:
        return tree_hash
    entries = tree_mod.read_tree(store, tree_hash)
    target = parts[0]
    if target not in entries:
        return None
    typ, h = entries[target]
    if typ != "T":
        return None
    return _navigate_tree(store, h, parts[1:])


def _get_base_files(repo, scope, base_version):
    entry = repo.get_history_entry(base_version)
    if entry and entry.get("root") and repo.store.exists(entry["root"]):
        scope_prefix = normalize_path(scope["path"])
        try:
            parts = scope_prefix.split("/") if scope_prefix else []
            subtree_hash = _navigate_tree(repo.store, entry["root"], parts)
            if subtree_hash:
                return _flatten_tree_to_bytes(repo.store, subtree_hash)
        except (KeyError, json.JSONDecodeError) as exc:
            print(f"[mut-server] warning: failed to read base "
                  f"v{base_version}: {exc}")
        except ObjectNotFoundError as exc:
            print(f"[mut-server] warning: missing object for base "
                  f"v{base_version}: {exc}")
    return {}
