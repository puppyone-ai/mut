"""Server-side request handlers (sync versions) — one function per endpoint.

Each handler receives (repo, auth_context, body) and returns a dict.
The auth_context is produced by the Authenticator — handlers never
touch credentials directly.

Errors are raised as MutError subclasses; the dispatch layer maps
them to HTTP status codes.

Commit identity: each successful push/rollback produces a commit_id
(16-hex SHA256 of scope_path|scope_hash|created_at_iso|who). The
per-scope head_commit_id is advanced alongside scope_hash, and both
are used for CAS.
"""

from __future__ import annotations

import base64
import json
from datetime import datetime, timezone

from mut.core.merge import merge_file_sets
from mut.core.object_store import ObjectStore
from mut.core.protocol import (
    CloneResponse, PushRequest, PushResponse, PullRequest, PullResponse,
    NegotiateRequest, NegotiateResponse, RollbackRequest, RollbackResponse,
    PullCommitRequest, ScopeInfo, normalize_path,
    require_supported_protocol,
)
from mut.core.scope import check_path_permission
from mut.core import tree as tree_mod
from mut.foundation.error import (
    PermissionDenied, LockError, ObjectNotFoundError,
)
from mut.server.history import HistoryManager
from mut.server.repo import ServerRepo


# ── Clone ──────────────────────────────────────

def handle_clone(repo: ServerRepo, auth: dict, body: dict) -> dict:
    require_supported_protocol(body)
    scope = auth["_scope"]

    files_raw = repo.list_scope_files(scope)
    files_b64 = {path: base64.b64encode(data).decode()
                 for path, data in files_raw.items()}

    scope_tree_hash = repo.build_scope_tree(scope)
    scope_hashes = tree_mod.collect_reachable_hashes(repo.store, scope_tree_hash)
    objects_b64 = {h: base64.b64encode(repo.store.get(h)).decode()
                   for h in scope_hashes}

    MAX_CLONE_HISTORY = 200
    # Return the SCOPE-level head so the client's REMOTE_HEAD matches
    # what /pull and /push compare against later. Using the global head
    # here would leak commits from other scopes and make every first
    # pull after clone look "stale" even when nothing changed.
    head_commit_id = repo.get_scope_head_commit_id(scope["path"])
    history = repo.get_history_since("", scope_path=scope["path"],
                                     limit=MAX_CLONE_HISTORY)

    repo.record_audit("clone", auth["agent"], {
        "scope": scope["path"],
        "files": len(files_raw),
        "commit_id": head_commit_id,
    })

    resp = CloneResponse(
        agent_id=auth["agent"],
        project=repo.get_project_name(),
        files=files_b64,
        objects=objects_b64,
        history=history,
        head_commit_id=head_commit_id,
        scope=ScopeInfo(
            path=scope["path"],
            exclude=scope.get("exclude", []),
            mode=scope.get("mode", "rw"),
        ),
    )
    return resp.to_dict()


# ── Push ───────────────────────────────────────

MAX_CAS_RETRIES = 3

def handle_push(repo: ServerRepo, auth: dict, body: dict) -> dict:
    require_supported_protocol(body)
    scope = auth["_scope"]

    if scope.get("mode", "r") == "r":
        raise PermissionDenied("scope is read-only")

    req = PushRequest.from_dict(body)
    _store_incoming_objects(repo.store, req.objects)

    if not req.snapshots:
        return PushResponse(
            status="ok", commit_id=repo.get_scope_head_commit_id(scope["path"]),
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

    for attempt in range(MAX_CAS_RETRIES + 1):
        result = _push_cas_attempt(
            repo, scope, auth, req, their_files, scope_prefix, attempt,
        )
        if result is not None:
            return result

    repo.record_audit("push_error", auth["agent"], {
        "scope": scope["path"], "error": "CAS failed after max retries",
    })
    raise LockError(f"concurrent push conflict after {MAX_CAS_RETRIES} retries, try again")


def _push_cas_attempt(
    repo: ServerRepo, scope: dict, auth: dict, req: PushRequest,
    their_files: dict, scope_prefix: str, attempt: int,
) -> dict | None:
    """Single CAS push attempt. Returns result dict on success, None on CAS failure."""
    old_scope_hash = repo.get_scope_hash(scope["path"])
    our_files = repo.list_scope_files(scope)
    current_head_commit = repo.get_scope_head_commit_id(scope["path"])

    merged_files, merge_conflicts = _resolve_conflicts(
        repo, scope, req.base_commit_id, current_head_commit,
        our_files, their_files,
    )

    if merge_conflicts:
        repo.record_audit("merge_conflict", auth["agent"], {
            "scope": scope["path"],
            "base_commit_id": req.base_commit_id,
            "server_commit_id": current_head_commit,
            "attempt": attempt,
            "conflicts": [
                {"path": c.path, "strategy": c.strategy,
                 "detail": c.detail, "kept": c.kept,
                 "lost_content": c.lost_content, "lost_hash": c.lost_hash}
                for c in merge_conflicts
            ],
        })

    _apply_merged_files(repo, scope, our_files, merged_files)

    new_scope_hash = repo.build_scope_tree(scope)

    # Compute commit_id BEFORE the CAS so the atomic swap can write
    # both ``scope_hash`` and ``head_commit_id`` in a single DB
    # statement. This closes a race where a losing pusher could have
    # later overwritten the winner's head pointer.
    created_at_iso = datetime.now(timezone.utc).isoformat(timespec="microseconds")
    new_commit_id = HistoryManager.compute_commit_id(
        scope_path=scope["path"],
        scope_hash=new_scope_hash,
        created_at_iso=created_at_iso,
        who=auth["agent"],
    )

    if not repo.cas_update_scope(
        scope["path"], old_scope_hash, new_scope_hash,
        head_commit_id=new_commit_id,
    ):
        return None

    changes = _compute_changeset(scope_prefix, our_files, merged_files)
    merged_changes = _compute_merged_changes(our_files, merged_files, their_files, scope_prefix)

    repo.record_history(
        new_commit_id, auth["agent"], req.snapshots[-1].get("message", ""),
        scope["path"], changes,
        conflicts=merge_conflicts,
        scope_hash=new_scope_hash,
        created_at_iso=created_at_iso,
    )
    repo.set_head_commit_id(new_commit_id)

    repo.record_audit("push", auth["agent"], {
        "scope": scope["path"],
        "snapshots": len(req.snapshots),
        "commit_id": new_commit_id,
        "scope_hash": new_scope_hash,
        "merged": bool(merge_conflicts),
        "conflict_count": len(merge_conflicts),
        "cas_attempts": attempt + 1,
    })

    return PushResponse(
        status="ok",
        commit_id=new_commit_id,
        pushed=len(req.snapshots),
        root=new_scope_hash,
        merged=bool(merge_conflicts),
        conflicts=len(merge_conflicts),
        merged_changes=merged_changes,
    ).to_dict()


# ── Negotiate ──────────────────────────────────

def handle_negotiate(repo: ServerRepo, auth: dict, body: dict) -> dict:
    """Object dedup + REMOTE_HEAD self-heal probe (Bug #6).

    Always reports back the server's current scope head so a client that
    has lost its REMOTE_HEAD (truncated history, restored backup, or a
    fresh clone race) can re-anchor without an extra round-trip. When
    the client sends the commit_id it *thinks* the server is at via
    ``remote_head``, we additionally set ``remote_head_recognized=False``
    if that commit no longer exists in our history — the signal that
    tells :meth:`SnapshotChain.reset_pushed_watermark` to clear the
    "already pushed" flag so the next push re-uploads.
    """
    require_supported_protocol(body)
    req = NegotiateRequest.from_dict(body)
    scope = auth.get("_scope") or {}

    missing = [h for h in req.hashes if not repo.store.exists(h)]

    scope_path = scope.get("path", "")
    server_head = (repo.get_scope_head_commit_id(scope_path)
                   if scope_path else "") or repo.get_head_commit_id()

    # An empty remote_head means "client did not send one" — treat as
    # recognised so `mut push` doesn't reset the watermark gratuitously.
    recognised = True
    if req.remote_head:
        recognised = repo.get_history_entry(req.remote_head) is not None

    return NegotiateResponse(
        missing=missing,
        server_head_commit_id=server_head,
        remote_head_recognized=recognised,
    ).to_dict()


# ── Pull ───────────────────────────────────────

def handle_pull(repo: ServerRepo, auth: dict, body: dict) -> dict:
    require_supported_protocol(body)
    scope = auth["_scope"]
    req = PullRequest.from_dict(body)

    head_commit_id = repo.get_scope_head_commit_id(scope["path"])

    if req.since_commit_id and req.since_commit_id == head_commit_id:
        return PullResponse(status="up-to-date", head_commit_id=head_commit_id).to_dict()

    files_raw = repo.list_scope_files(scope)
    files_b64 = {p: base64.b64encode(d).decode()
                 for p, d in files_raw.items()}

    scope_tree_hash = repo.build_scope_tree(scope)
    scope_hashes = tree_mod.collect_reachable_hashes(repo.store, scope_tree_hash)
    have_hashes = set(req.have_hashes)
    objects_b64 = {h: base64.b64encode(repo.store.get(h)).decode()
                   for h in scope_hashes if h not in have_hashes}

    MAX_PULL_HISTORY = 200
    history = repo.get_history_since(req.since_commit_id,
                                     scope_path=scope["path"],
                                     limit=MAX_PULL_HISTORY)

    repo.record_audit("pull", auth["agent"], {
        "scope": scope["path"],
        "since_commit_id": req.since_commit_id,
        "commit_id": head_commit_id,
    })

    return PullResponse(
        status="updated",
        head_commit_id=head_commit_id,
        files=files_b64,
        objects=objects_b64,
        history=history,
    ).to_dict()


# ── Pull Commit ────────────────────────────────

def _resolve_scope_tree_hash(repo: ServerRepo, entry: dict,
                             scope_path: str) -> str | None:
    """Resolve the scope-level tree hash from a history entry.

    Prefers scope_hash (canonical), falls back to navigating root.
    """
    scope_hash = entry.get("scope_hash", "")
    if scope_hash and repo.store.exists(scope_hash):
        return scope_hash
    root = entry.get("root", "")
    if root and repo.store.exists(root):
        parts = normalize_path(scope_path).split("/") if normalize_path(scope_path) else []
        return _navigate_tree(repo.store, root, parts)
    return None


def handle_pull_commit(repo: ServerRepo, auth: dict, body: dict) -> dict:
    """Pull files at a specific historical commit (not just HEAD)."""
    require_supported_protocol(body)
    scope = auth["_scope"]
    req = PullCommitRequest.from_dict(body)
    target_commit_id = req.commit_id

    if not target_commit_id:
        raise ValueError("commit_id is required")

    entry = repo.get_history_entry(target_commit_id)
    if not entry:
        raise ValueError(f"commit {target_commit_id} not found")

    subtree_hash = _resolve_scope_tree_hash(repo, entry, scope["path"])
    if subtree_hash is None:
        raise ObjectNotFoundError(
            f"no tree data for commit {target_commit_id}"
        )

    files_b64 = {}
    objects_b64 = {}
    if subtree_hash:
        flat = tree_mod.tree_to_flat(repo.store, subtree_hash)
        for path, h in flat.items():
            files_b64[path] = base64.b64encode(repo.store.get(h)).decode()
        reachable = tree_mod.collect_reachable_hashes(repo.store, subtree_hash)
        objects_b64 = {h: base64.b64encode(repo.store.get(h)).decode()
                       for h in reachable}

    repo.record_audit("pull_commit", auth["agent"], {
        "scope": scope["path"],
        "commit_id": target_commit_id,
    })

    return {
        "status": "ok",
        "commit_id": target_commit_id,
        "files": files_b64,
        "objects": objects_b64,
    }


# Deprecated alias — old callers expected "pull_version"
handle_pull_version = handle_pull_commit


# ── Rollback ──────────────────────────────────

def handle_rollback(repo: ServerRepo, auth: dict, body: dict) -> dict:
    """Rollback to a historical commit by creating a revert commit.

    The commit chain continues forward — rollback creates a NEW commit
    whose content equals the target historical commit's snapshot.

    Uses CAS (compare-and-swap) on scope_hash to prevent concurrent
    rollbacks from silently overwriting each other — same safety as push.
    """
    require_supported_protocol(body)
    scope = auth["_scope"]
    if scope.get("mode", "r") == "r":
        raise PermissionDenied("scope is read-only")

    req = RollbackRequest.from_dict(body)
    target_commit_id = req.target_commit_id
    current_head = repo.get_scope_head_commit_id(scope["path"])

    if not target_commit_id:
        raise ValueError("target_commit_id is required")
    if target_commit_id == current_head:
        return RollbackResponse(
            status="already-at-commit",
            new_commit_id=current_head,
            target_commit_id=target_commit_id,
        ).to_dict()

    target_entry = repo.get_history_entry(target_commit_id)
    if not target_entry:
        raise ValueError(f"commit {target_commit_id} not found")

    scope_prefix = normalize_path(scope["path"])
    subtree_hash = _resolve_scope_tree_hash(repo, target_entry, scope["path"])

    if subtree_hash is None:
        raise ObjectNotFoundError(
            f"no tree data for commit {target_commit_id}"
        )

    target_files = _flatten_tree_to_bytes(repo.store, subtree_hash)

    for attempt in range(MAX_CAS_RETRIES + 1):
        result = _rollback_cas_attempt(
            repo, scope, auth, target_commit_id, target_files,
            scope_prefix, attempt,
        )
        if result is not None:
            return result

    repo.record_audit("rollback_error", auth["agent"], {
        "scope": scope["path"],
        "target_commit_id": target_commit_id,
        "error": "CAS failed after max retries",
    })
    raise LockError(
        f"concurrent rollback conflict after {MAX_CAS_RETRIES} retries, try again"
    )


def _rollback_cas_attempt(
    repo: ServerRepo, scope: dict, auth: dict,
    target_commit_id: str, target_files: dict,
    scope_prefix: str, attempt: int,
) -> dict | None:
    """Single CAS rollback attempt. Returns result dict on success, None on CAS failure.

    Symmetric with :func:`_push_cas_attempt`: we derive ``new_commit_id``
    *before* the CAS call so the atomic swap updates ``scope_hash`` and
    ``head_commit_id`` together.  Skipping this step left a window where
    a DB-backed ``cas_update_scope`` that always writes ``head_commit_id``
    could blank out the head between CAS success and the follow-up
    ``set_scope_head_commit_id`` write.
    """
    old_scope_hash = repo.get_scope_hash(scope["path"])
    current_files = repo.list_scope_files(scope)
    changes = _compute_changeset(scope_prefix, current_files, target_files)

    _apply_merged_files(repo, scope, current_files, target_files)
    new_scope_hash = repo.build_scope_tree(scope)

    created_at_iso = datetime.now(timezone.utc).isoformat(timespec="microseconds")
    new_commit_id = HistoryManager.compute_commit_id(
        scope_path=scope["path"],
        scope_hash=new_scope_hash,
        created_at_iso=created_at_iso,
        who=auth["agent"],
    )

    if not repo.cas_update_scope(
        scope["path"], old_scope_hash, new_scope_hash,
        head_commit_id=new_commit_id,
    ):
        return None

    repo.record_history(
        new_commit_id, auth["agent"],
        f"rollback to #{target_commit_id}",
        scope["path"], changes,
        scope_hash=new_scope_hash,
        created_at_iso=created_at_iso,
    )
    repo.set_head_commit_id(new_commit_id)

    repo.record_audit("rollback", auth["agent"], {
        "scope": scope["path"],
        "target_commit_id": target_commit_id,
        "new_commit_id": new_commit_id,
        "scope_hash": new_scope_hash,
        "cas_attempts": attempt + 1,
    })

    return RollbackResponse(
        status="rolled-back",
        new_commit_id=new_commit_id,
        target_commit_id=target_commit_id,
        root=new_scope_hash,
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


def _resolve_conflicts(repo, scope, base_commit_id, current_head_commit,
                       our_files, their_files):
    """Three-way merge whenever the client's base diverges from server head.

    Empty ``base_commit_id`` means the client never synced (or lost its
    REMOTE_HEAD). We MUST still run the merge — otherwise any file that
    exists on the server but isn't in the push would be silently deleted
    by :func:`_apply_merged_files`.
    """
    if base_commit_id == current_head_commit:
        return their_files, []
    base_files = (_get_base_files(repo, scope, base_commit_id)
                  if base_commit_id else {})
    return merge_file_sets(base_files, our_files, their_files)


def _apply_merged_files(repo, scope, old_scope_files, merged_files):
    for old_path in old_scope_files:
        if old_path not in merged_files:
            repo.delete_scope_file(scope, old_path)
    repo.write_scope_files(scope, merged_files)


def _compute_changeset(scope_prefix, old_files, merged_files):
    changes = []
    for rel_path, new_data in merged_files.items():
        full = f"{scope_prefix}/{rel_path}" if scope_prefix else rel_path
        if rel_path not in old_files:
            changes.append({"path": full, "action": "add"})
        elif old_files[rel_path] != new_data:
            changes.append({"path": full, "action": "update"})
    for old_path in old_files:
        if old_path not in merged_files:
            full = (f"{scope_prefix}/{old_path}"
                    if scope_prefix else old_path)
            changes.append({"path": full, "action": "delete"})
    return changes


def _compute_merged_changes(our_files, merged_files, their_files, scope_prefix):
    """Compute files that were merged from server state but not in client's push.

    These are files the client needs to know about to keep its local state in sync.
    """
    merged_changes = []
    for rel_path, content in merged_files.items():
        if rel_path not in their_files and rel_path in our_files:
            full = f"{scope_prefix}/{rel_path}" if scope_prefix else rel_path
            merged_changes.append({"path": full, "action": "merged_from_server"})
        elif rel_path in their_files and rel_path in our_files:
            if content != their_files[rel_path] and content != our_files.get(rel_path):
                full = f"{scope_prefix}/{rel_path}" if scope_prefix else rel_path
                merged_changes.append({"path": full, "action": "content_merged"})
    return merged_changes


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


def _get_base_files(repo, scope, base_commit_id):
    """Get scope files at a historical commit for three-way merge base."""
    entry = repo.get_history_entry(base_commit_id)
    if not entry:
        return {}
    try:
        scope_hash = entry.get("scope_hash", "")
        if scope_hash and repo.store.exists(scope_hash):
            return _flatten_tree_to_bytes(repo.store, scope_hash)

        root = entry.get("root", "")
        if root and repo.store.exists(root):
            scope_prefix = normalize_path(scope["path"])
            parts = scope_prefix.split("/") if scope_prefix else []
            subtree_hash = _navigate_tree(repo.store, root, parts)
            if subtree_hash:
                return _flatten_tree_to_bytes(repo.store, subtree_hash)
    except (KeyError, json.JSONDecodeError) as exc:
        print(f"[mut-server] warning: failed to read base "
              f"#{base_commit_id}: {exc}")
    except ObjectNotFoundError as exc:
        print(f"[mut-server] warning: missing object for base "
              f"#{base_commit_id}: {exc}")
    return {}
