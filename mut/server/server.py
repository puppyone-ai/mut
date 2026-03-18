"""Fully async Mut HTTP API server built on asyncio.

Auth-agnostic: the server accepts a pluggable Authenticator that
resolves credentials into an auth context (agent + scope + mode).
MUT core never touches credentials directly.

Endpoints:
  POST /clone      — Agent requests scope files + history
  POST /push       — Agent pushes local commits (with auto-merge)
  POST /pull       — Agent pulls changes since a version
  POST /negotiate  — Hash negotiation for object dedup
  GET  /health     — Health check

Push never fails — conflicts are resolved server-side via three-way merge + LWW.
"""

from __future__ import annotations

import asyncio
import base64
import json

from mut.foundation.config import normalize_path, HASH_LEN
from mut.core.protocol import PROTOCOL_VERSION
from mut.core.merge import merge_file_sets
from mut.core.scope import check_path_permission
from mut.core import tree as tree_mod
from mut.foundation.error import (
    MutError, PermissionDenied, LockError,
    ObjectNotFoundError, PayloadTooLargeError,
)
from mut.server.auth.base import Authenticator
from mut.server.repo import ServerRepo
from mut.server.graft import async_graft_subtree


MAX_BODY_SIZE = 256 * 1024 * 1024  # 256 MB
MAX_CLONE_HISTORY = 200


_STATUS_TEXT = {
    200: "OK", 400: "Bad Request", 401: "Unauthorized", 403: "Forbidden",
    404: "Not Found", 409: "Conflict", 413: "Payload Too Large",
    422: "Unprocessable Entity", 500: "Internal Server Error", 502: "Bad Gateway",
}


# ── Async HTTP primitives ─────────────────────

async def _read_request(reader: asyncio.StreamReader) -> tuple[str, str, dict]:
    request_line = await reader.readline()
    if not request_line:
        raise ConnectionError("empty request")
    parts = request_line.decode().strip().split(" ", 2)
    if len(parts) < 2:
        raise ConnectionError("malformed request line")
    method, path = parts[0], parts[1]

    headers: dict[str, str] = {}
    while True:
        line = await reader.readline()
        if line in (b"\r\n", b"\n", b""):
            break
        decoded = line.decode().strip()
        if ": " in decoded:
            key, value = decoded.split(": ", 1)
            headers[key.lower()] = value
    return method, path, headers


async def _read_body(reader: asyncio.StreamReader, headers: dict) -> dict:
    length = int(headers.get("content-length", 0))
    if length == 0:
        return {}
    if length > MAX_BODY_SIZE:
        raise PayloadTooLargeError(f"request body too large ({length} bytes)")
    data = await reader.readexactly(length)
    return json.loads(data)


def _build_response(data: dict, status: int = 200) -> bytes:
    body = json.dumps(data, ensure_ascii=False).encode()
    status_text = _STATUS_TEXT.get(status, "Unknown")
    header = (
        f"HTTP/1.1 {status} {status_text}\r\n"
        f"Content-Type: application/json\r\n"
        f"Content-Length: {len(body)}\r\n"
        f"Connection: close\r\n"
        f"\r\n"
    ).encode()
    return header + body


async def _send(writer: asyncio.StreamWriter, data: dict, status: int = 200):
    writer.write(_build_response(data, status))
    await writer.drain()


async def _send_error(writer: asyncio.StreamWriter, status: int, message: str):
    await _send(writer, {"error": message}, status)


# ── Handlers ──────────────────────────────────

async def _handle_clone(repo: ServerRepo, auth: dict, _body: dict) -> dict:
    scope = auth["_scope"]

    files_raw = await repo.async_list_scope_files(scope)
    files_b64 = {path: base64.b64encode(data).decode()
                 for path, data in files_raw.items()}

    scope_tree_hash = await repo.async_build_scope_tree(scope)
    scope_hashes = tree_mod.collect_reachable_hashes(repo.store, scope_tree_hash)
    objects_b64 = {h: base64.b64encode(repo.store.get(h)).decode()
                   for h in scope_hashes}

    latest = await repo.async_get_latest_version()
    history = await repo.async_get_history_since(0, scope_path=scope["path"],
                                                 limit=MAX_CLONE_HISTORY)

    await repo.async_record_audit("clone", auth["agent"], {
        "scope": scope["path"],
        "files": len(files_raw),
        "version": latest,
    })

    return {
        "agent_id": auth["agent"],
        "project": repo.get_project_name(),
        "files": files_b64,
        "objects": objects_b64,
        "history": history,
        "version": latest,
        "scope": {
            "path": scope["path"],
            "exclude": scope.get("exclude", []),
            "mode": scope.get("mode", "rw"),
        },
    }


async def _handle_push(repo: ServerRepo, auth: dict, body: dict) -> dict:
    scope = auth["_scope"]

    if scope.get("mode", "r") == "r":
        raise PermissionDenied("scope is read-only")

    scope_id = scope["id"]
    scope_lock = repo._get_scope_lock(scope_id)

    if scope_lock.locked():
        raise LockError("scope is locked by another push, retry later")

    async with scope_lock:
        return await _push_locked(repo, scope, auth, body)


async def _push_locked(repo: ServerRepo, scope: dict, auth: dict,
                       body: dict) -> dict:
    objects_b64 = body.get("objects", {})
    snapshots = body.get("snapshots", [])
    base_version = body.get("base_version", 0)

    for _h, b64data in objects_b64.items():
        await repo.store.async_put(base64.b64decode(b64data))

    if not snapshots:
        version = await repo.async_get_latest_version()
        return {"status": "ok", "version": version}

    their_root_hash = snapshots[-1]["root"]
    their_files = await _async_flatten_tree(repo, their_root_hash)
    scope_prefix = normalize_path(scope["path"])

    rejected = _validate_scope_paths(scope, scope_prefix, their_files)
    if rejected:
        await repo.async_record_audit("push_rejected", auth["agent"], {
            "scope": scope["path"], "rejected_paths": rejected,
        })
        raise PermissionDenied(f"paths outside scope: {rejected[:5]}")

    our_files = await repo.async_list_scope_files(scope)
    current_version = await repo.async_get_latest_version()

    merged_files, merge_conflicts = _resolve_conflicts(
        repo, scope, base_version, current_version, our_files, their_files,
    )

    if merge_conflicts:
        await repo.async_record_audit("merge_conflict", auth["agent"], {
            "scope": scope["path"],
            "base_version": base_version,
            "server_version": current_version,
            "conflicts": [
                {"path": c.path, "strategy": c.strategy,
                 "detail": c.detail, "kept": c.kept,
                 "lost_content": c.lost_content, "lost_hash": c.lost_hash}
                for c in merge_conflicts
            ],
        })

    await _async_apply_merged_files(repo, scope, our_files, merged_files)

    new_scope_tree_hash = await repo.async_build_scope_tree(scope)

    async with repo._ensure_global_lock():
        current_version = await repo.async_get_latest_version()
        old_root = await repo.async_get_root_hash()

        if old_root and await repo.store.async_exists(old_root):
            new_root = await async_graft_subtree(
                repo.store, old_root, scope_prefix, new_scope_tree_hash,
            )
        else:
            new_root = await repo.async_build_full_tree()

        new_version = current_version + 1
        changes = _compute_changeset(scope_prefix, our_files, merged_files)

        await repo.async_record_history(
            new_version, auth["agent"], snapshots[-1].get("message", ""),
            scope["path"], changes,
            conflicts=merge_conflicts, root_hash=new_root,
        )
        await repo.async_set_latest_version(new_version)
        await repo.async_set_root_hash(new_root)

    await repo.async_record_audit("push", auth["agent"], {
        "scope": scope["path"],
        "snapshots": len(snapshots),
        "version": new_version,
        "root": new_root,
        "merged": bool(merge_conflicts),
        "conflict_count": len(merge_conflicts),
    })

    result = {
        "status": "ok",
        "version": new_version,
        "pushed": len(snapshots),
        "root": new_root,
    }
    if merge_conflicts:
        result["merged"] = True
        result["conflicts"] = len(merge_conflicts)
    return result


def _is_valid_hash(h: str) -> bool:
    return isinstance(h, str) and 0 < len(h) <= HASH_LEN and h.isalnum()


async def _handle_negotiate(repo: ServerRepo, _auth: dict, body: dict) -> dict:
    offered = body.get("hashes", [])
    missing = []
    for h in offered:
        if not _is_valid_hash(h):
            raise ValueError(f"invalid hash: {h!r}")
        if not await repo.store.async_exists(h):
            missing.append(h)
    return {"missing": missing}


async def _handle_pull(repo: ServerRepo, auth: dict, body: dict) -> dict:
    scope = auth["_scope"]
    since_version = body.get("since_version", 0)
    latest = await repo.async_get_latest_version()

    if since_version >= latest:
        return {
            "status": "up-to-date",
            "version": latest,
            "files": {},
            "objects": {},
            "history": [],
        }

    files_raw = await repo.async_list_scope_files(scope)
    files_b64 = {p: base64.b64encode(d).decode() for p, d in files_raw.items()}

    scope_tree_hash = await repo.async_build_scope_tree(scope)
    scope_hashes = tree_mod.collect_reachable_hashes(repo.store, scope_tree_hash)
    have_hashes = set(body.get("have_hashes", []))
    objects_b64 = {h: base64.b64encode(repo.store.get(h)).decode()
                   for h in scope_hashes if h not in have_hashes}

    history = await repo.async_get_history_since(since_version,
                                                 scope_path=scope["path"])

    await repo.async_record_audit("pull", auth["agent"], {
        "scope": scope["path"],
        "since": since_version,
        "version": latest,
    })

    return {
        "status": "updated",
        "version": latest,
        "files": files_b64,
        "objects": objects_b64,
        "history": history,
    }


def _handle_health() -> dict:
    return {"status": "ok"}


# ── Route dispatch ────────────────────────────

_POST_ROUTES = {
    "/clone": _handle_clone,
    "/push": _handle_push,
    "/pull": _handle_pull,
    "/negotiate": _handle_negotiate,
}


def _check_protocol_version(body: dict):
    client_version = body.get("protocol_version", 1)
    if client_version > PROTOCOL_VERSION:
        raise ValueError(
            f"unsupported protocol version {client_version} "
            f"(server supports up to {PROTOCOL_VERSION})"
        )


async def _dispatch(repo: ServerRepo, authenticator: Authenticator,
                    method: str, path: str,
                    headers: dict, body: dict) -> tuple[dict, int]:
    if method == "GET" and path == "/health":
        return _handle_health(), 200

    if method != "POST":
        return {"error": f"unknown endpoint: {path}"}, 404

    handler = _POST_ROUTES.get(path)
    if handler is None:
        return {"error": f"unknown endpoint: {path}"}, 404

    _check_protocol_version(body)
    auth = await authenticator.authenticate(headers, body)
    return await handler(repo, auth, body), 200


# ── Connection handler ────────────────────────

async def _handle_connection(repo: ServerRepo, authenticator: Authenticator,
                             reader: asyncio.StreamReader,
                             writer: asyncio.StreamWriter):
    try:
        method, path, headers = await _read_request(reader)
        body = await _read_body(reader, headers)
        result, status = await _dispatch(repo, authenticator,
                                         method, path, headers, body)
        await _send(writer, result, status)
    except MutError as e:
        await _send_error(writer, e.http_status, str(e))
    except (ValueError, KeyError) as e:
        await _send_error(writer, 400, str(e))
    except ConnectionError:
        pass
    except Exception as e:
        await _send_error(writer, 500, f"internal error: {e}")
    finally:
        writer.close()
        try:
            await writer.wait_closed()
        except ConnectionError:
            pass


# ── Shared helpers ────────────────────────────

def _validate_scope_paths(scope: dict, scope_prefix: str, files: dict) -> list:
    rejected = []
    for rel_path in files:
        full_path = f"{scope_prefix}/{rel_path}" if scope_prefix else rel_path
        if not check_path_permission(scope, full_path, "write"):
            rejected.append(full_path)
    return rejected


def _resolve_conflicts(repo, scope, base_version, current_version,
                       our_files, their_files):
    if base_version < current_version:
        base_files = _get_base_files(repo, scope, base_version)
        return merge_file_sets(base_files, our_files, their_files)
    return their_files, []


async def _async_apply_merged_files(repo, scope, old_scope_files, merged_files):
    for old_path in old_scope_files:
        if old_path not in merged_files:
            await repo.async_delete_scope_file(scope, old_path)
    await repo.async_write_scope_files(scope, merged_files)


def _compute_changeset(scope_prefix, old_files, merged_files) -> list:
    changes = []
    for rel_path in merged_files:
        full = f"{scope_prefix}/{rel_path}" if scope_prefix else rel_path
        action = "add" if rel_path not in old_files else "update"
        changes.append({"path": full, "action": action})
    for old_path in old_files:
        if old_path not in merged_files:
            full = f"{scope_prefix}/{old_path}" if scope_prefix else old_path
            changes.append({"path": full, "action": "delete"})
    return changes


async def _async_flatten_tree(repo, tree_hash: str) -> dict:
    flat_hashes = tree_mod.tree_to_flat(repo.store, tree_hash)
    result = {}
    for path, h in flat_hashes.items():
        result[path] = await repo.store.async_get(h)
    return result


def _navigate_tree(store, tree_hash: str, parts: list) -> str | None:
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


def _get_base_files(repo, scope, base_version) -> dict:
    entry = repo.get_history_entry(base_version)
    if entry and entry.get("root") and repo.store.exists(entry["root"]):
        scope_prefix = normalize_path(scope["path"])
        try:
            parts = scope_prefix.split("/") if scope_prefix else []
            subtree_hash = _navigate_tree(repo.store, entry["root"], parts)
            if subtree_hash:
                flat_hashes = tree_mod.tree_to_flat(repo.store, subtree_hash)
                return {path: repo.store.get(h)
                        for path, h in flat_hashes.items()}
        except (KeyError, json.JSONDecodeError) as exc:
            print(f"[mut-server] warning: failed to read base "
                  f"v{base_version}: {exc}")
        except ObjectNotFoundError as exc:
            print(f"[mut-server] warning: missing object for base "
                  f"v{base_version}: {exc}")
    return {}


# ── Server entry point ────────────────────────

_GRACEFUL_TIMEOUT = 10


async def async_serve(repo_root: str, host: str = "127.0.0.1",
                      port: int = 9742,
                      authenticator: Authenticator | None = None):
    """Start the async Mut HTTP server with pluggable auth."""
    from mut.server.auth import NoAuth

    repo = ServerRepo(repo_root)
    repo.check_init()

    if authenticator is None:
        authenticator = NoAuth(repo.scopes)

    if not repo.get_root_hash():
        root = repo.build_full_tree()
        repo.set_root_hash(root)
        if repo.get_latest_version() == 0:
            repo.record_history(
                0, "server", "initial state", "/", [], root_hash=root,
            )

    active_tasks: set[asyncio.Task] = set()

    async def on_connection(reader, writer):
        task = asyncio.current_task()
        active_tasks.add(task)
        try:
            await _handle_connection(repo, authenticator, reader, writer)
        finally:
            active_tasks.discard(task)

    server = await asyncio.start_server(on_connection, host, port)
    auth_name = type(authenticator).__name__
    print(f"Mut server listening on http://{host}:{port}")
    print(f"  repo: {repo.root}")
    print(f"  auth: {auth_name}")
    print(f"  version: {repo.get_latest_version()}")
    print(f"  root: {repo.get_root_hash()[:16]}")

    try:
        async with server:
            await server.serve_forever()
    except asyncio.CancelledError:
        server.close()
        await server.wait_closed()
        if active_tasks:
            print(f"Waiting for {len(active_tasks)} in-flight request(s)...")
            await asyncio.wait(active_tasks, timeout=_GRACEFUL_TIMEOUT)
        print("Server stopped.")
        raise
    finally:
        await authenticator.close()
        if server.is_serving():
            server.close()
            await server.wait_closed()


def serve(repo_root: str, host: str = "127.0.0.1", port: int = 9742,
          authenticator: Authenticator | None = None):
    """Start the Mut HTTP server (blocking entry point for CLI)."""
    try:
        asyncio.run(async_serve(repo_root, host, port, authenticator))
    except KeyboardInterrupt:
        print("\nShutting down.")
