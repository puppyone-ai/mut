"""Mut HTTP API server.

Endpoints:
  POST /clone   — Agent requests scope files + history
  POST /push    — Agent pushes local commits to server (with auto-merge)
  POST /pull    — Agent pulls changes since a version

All requests carry Authorization: Bearer <token>.
Push never fails — conflicts are resolved server-side via three-way merge + LWW.
"""

import base64
import json
from http.server import HTTPServer, BaseHTTPRequestHandler

from mut.core.auth import verify_token
from mut.core.object_store import ObjectStore
from mut.core.merge import merge_file_sets
from mut.core.scope import check_path_permission
from mut.core import tree as tree_mod
from mut.foundation.error import ObjectNotFoundError, PermissionDenied
from mut.server.repo import ServerRepo
from mut.server.graft import graft_subtree


class MutHandler(BaseHTTPRequestHandler):

    server_repo: ServerRepo = None  # set by serve()

    MAX_BODY_SIZE = 256 * 1024 * 1024  # 256 MB

    def _read_body(self) -> dict:
        length = int(self.headers.get("Content-Length", 0))
        if length == 0:
            return {}
        if length > self.MAX_BODY_SIZE:
            self._send_error(413, f"request body too large ({length} bytes)")
            return None
        return json.loads(self.rfile.read(length).decode())

    def _send_json(self, data: dict, status: int = 200):
        body = json.dumps(data, ensure_ascii=False).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _send_error(self, status: int, message: str):
        self._send_json({"error": message}, status)

    def _auth(self) -> dict:
        """Verify token, return payload or None (sends error response)."""
        auth = self.headers.get("Authorization", "")
        if not auth.startswith("Bearer "):
            self._send_error(401, "missing or invalid Authorization header")
            return None
        token = auth[7:]
        try:
            payload = verify_token(token, self.server_repo.get_secret())
        except (PermissionDenied, ValueError) as e:
            self._send_error(403, str(e))
            return None
        scope = self.server_repo.get_scope_for_agent(payload["agent"])
        if scope is None:
            self._send_error(403, f"no scope for agent '{payload['agent']}'")
            return None
        payload["_scope"] = scope
        return payload

    # ── Route dispatch ────────────────────────────

    def do_POST(self):
        if self.path == "/clone":
            self._handle_clone()
        elif self.path == "/push":
            self._handle_push()
        elif self.path == "/pull":
            self._handle_pull()
        elif self.path == "/negotiate":
            self._handle_negotiate()
        else:
            self._send_error(404, f"unknown endpoint: {self.path}")

    def do_GET(self):
        if self.path == "/health":
            self._send_json({"status": "ok"})
        else:
            self._send_error(404, f"unknown endpoint: {self.path}")

    # ── Clone ─────────────────────────────────────

    def _handle_clone(self):
        auth = self._auth()
        if auth is None:
            return
        repo = self.server_repo
        scope = auth["_scope"]

        files_raw = repo.list_scope_files(scope)
        files_b64 = {path: base64.b64encode(data).decode()
                     for path, data in files_raw.items()}

        scope_tree_hash = repo.build_scope_tree(scope)

        scope_hashes = tree_mod.collect_reachable_hashes(repo.store, scope_tree_hash)
        objects_b64 = {}
        for h in scope_hashes:
            objects_b64[h] = base64.b64encode(repo.store.get(h)).decode()

        latest = repo.get_latest_version()
        history = repo.get_history_since(0, scope_path=scope["path"])

        repo.record_audit("clone", auth["agent"], {
            "scope": scope["path"],
            "files": len(files_raw),
            "version": latest,
        })

        self._send_json({
            "files": files_b64,
            "objects": objects_b64,
            "history": history,
            "version": latest,
            "scope": {
                "path": scope["path"],
                "exclude": scope.get("exclude", []),
                "mode": scope.get("mode", "rw"),
            },
        })

    # ── Push (with conflict detection + auto-merge) ──

    def _handle_push(self):
        auth = self._auth()
        if auth is None:
            return
        repo = self.server_repo
        scope = auth["_scope"]
        body = self._read_body()
        if body is None:
            return

        if scope.get("mode", "r") == "r":
            self._send_error(403, "scope is read-only")
            return

        scope_id = scope["id"]
        if not repo.acquire_lock(scope_id):
            self._send_error(409, "scope is locked by another push, retry later")
            return

        try:
            self._push_locked(repo, scope, auth, body)
        except Exception as exc:
            repo.record_audit("push_error", auth["agent"], {
                "scope": scope["path"], "error": str(exc),
            })
            self._send_error(500, f"internal error: {exc}")
        finally:
            repo.release_lock(scope_id)

    def _push_locked(self, repo, scope, auth, body):
        """Core push logic, called while holding the scope lock."""
        objects_b64 = body.get("objects", {})
        snapshots = body.get("snapshots", [])
        base_version = body.get("base_version", 0)

        _store_incoming_objects(repo.store, objects_b64)

        if not snapshots:
            self._send_json({"status": "ok", "version": repo.get_latest_version()})
            return

        their_root_hash = snapshots[-1]["root"]
        their_files = _flatten_tree_to_bytes(repo.store, their_root_hash)
        scope_prefix = scope["path"].strip("/")

        rejected = _validate_scope_paths(scope, scope_prefix, their_files)
        if rejected:
            repo.record_audit("push_rejected", auth["agent"], {
                "scope": scope["path"], "rejected_paths": rejected,
            })
            self._send_error(403, f"paths outside scope: {rejected[:5]}")
            return

        our_files = repo.list_scope_files(scope)
        current_version = repo.get_latest_version()

        merged_files, merge_conflicts = _resolve_conflicts(
            repo, scope, base_version, current_version, our_files, their_files,
        )

        if merge_conflicts:
            repo.record_audit("merge_conflict", auth["agent"], {
                "scope": scope["path"],
                "base_version": base_version,
                "server_version": current_version,
                "conflicts": [
                    {"path": c.path, "strategy": c.strategy,
                     "detail": c.detail, "kept": c.kept}
                    for c in merge_conflicts
                ],
            })

        _apply_merged_files(repo, scope, our_files, merged_files)

        new_root = _graft_scope_tree(repo, scope, scope_prefix)

        new_version = current_version + 1
        changes = _compute_changeset(scope_prefix, our_files, merged_files)

        repo.record_history(
            new_version, auth["agent"], snapshots[-1].get("message", ""),
            scope["path"], changes,
            conflicts=merge_conflicts, root_hash=new_root,
        )
        repo.set_latest_version(new_version)
        repo.set_root_hash(new_root)

        repo.record_audit("push", auth["agent"], {
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

        self._send_json(result)

    # ── Negotiate (hash negotiation) ────────────

    def _handle_negotiate(self):
        auth = self._auth()
        if auth is None:
            return
        body = self._read_body()
        if body is None:
            return
        offered = body.get("hashes", [])
        missing = [h for h in offered if not self.server_repo.store.exists(h)]
        self._send_json({"missing": missing})

    # ── Pull ──────────────────────────────────────

    def _handle_pull(self):
        auth = self._auth()
        if auth is None:
            return
        repo = self.server_repo
        scope = auth["_scope"]
        body = self._read_body()
        if body is None:
            return

        since_version = body.get("since_version", 0)
        latest = repo.get_latest_version()

        if since_version >= latest:
            self._send_json({
                "status": "up-to-date",
                "version": latest,
                "files": {},
                "objects": {},
                "history": [],
            })
            return

        files_raw = repo.list_scope_files(scope)
        files_b64 = {p: base64.b64encode(d).decode() for p, d in files_raw.items()}

        scope_tree_hash = repo.build_scope_tree(scope)
        scope_hashes = tree_mod.collect_reachable_hashes(repo.store, scope_tree_hash)
        have_hashes = set(body.get("have_hashes", []))
        objects_b64 = {}
        for h in scope_hashes:
            if h not in have_hashes:
                objects_b64[h] = base64.b64encode(repo.store.get(h)).decode()

        history = repo.get_history_since(since_version, scope_path=scope["path"])

        repo.record_audit("pull", auth["agent"], {
            "scope": scope["path"],
            "since": since_version,
            "version": latest,
        })

        self._send_json({
            "status": "updated",
            "version": latest,
            "files": files_b64,
            "objects": objects_b64,
            "history": history,
        })

    # ── Logging ───────────────────────────────────

    def log_message(self, format, *args):
        print(f"[mut-server] {args[0]}")


def _store_incoming_objects(store: ObjectStore, objects_b64: dict):
    """Decode and store all base64-encoded objects from the push payload."""
    for h, b64data in objects_b64.items():
        store.put(base64.b64decode(b64data))


def _validate_scope_paths(scope: dict, scope_prefix: str, files: dict) -> list:
    """Check all file paths against scope permissions. Returns list of rejected paths."""
    rejected = []
    for rel_path in files:
        full_path = f"{scope_prefix}/{rel_path}" if scope_prefix else rel_path
        if not check_path_permission(scope, full_path, "write"):
            rejected.append(full_path)
    return rejected


def _resolve_conflicts(repo, scope, base_version, current_version,
                       our_files, their_files):
    """Run three-way merge if base_version is behind, else take theirs."""
    if base_version < current_version:
        base_files = _get_base_files(repo, scope, base_version)
        return merge_file_sets(base_files, our_files, their_files)
    return their_files, []


def _apply_merged_files(repo, scope, old_scope_files, merged_files):
    """Write merged files to current/, delete removed files."""
    for old_path in old_scope_files:
        if old_path not in merged_files:
            repo.delete_scope_file(scope, old_path)
    repo.write_scope_files(scope, merged_files)


def _graft_scope_tree(repo, scope, scope_prefix) -> str:
    """Rebuild the full project tree with the updated scope subtree."""
    new_scope_tree_hash = repo.build_scope_tree(scope)
    old_root = repo.get_root_hash()

    if old_root and repo.store.exists(old_root):
        return graft_subtree(
            repo.store, old_root, scope_prefix, new_scope_tree_hash
        )
    return repo.build_full_tree()


def _compute_changeset(scope_prefix, old_files, merged_files) -> list:
    """Compute the list of file changes for history recording."""
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


def _flatten_tree_to_bytes(store: ObjectStore, tree_hash: str,
                           prefix: str = "") -> dict:
    """Flatten tree into {relative_path: bytes_content}.

    Uses core/tree.py tree_to_flat for hash lookup, then resolves each
    hash to bytes.
    """
    flat_hashes = tree_mod.tree_to_flat(store, tree_hash)
    return {path: store.get(h) for path, h in flat_hashes.items()}


def _navigate_tree(store: ObjectStore, tree_hash: str, parts: list) -> str:
    """Navigate into a tree by path parts, return the subtree hash."""
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


def _get_base_files(repo: ServerRepo, scope: dict, base_version: int) -> dict:
    """Try to reconstruct scope files at base_version.

    If we have a recorded root hash for that version, use it.
    Otherwise fall back to empty (treats all current server files as 'new').
    """
    entry = repo.get_history_entry(base_version)
    if entry and entry.get("root") and repo.store.exists(entry["root"]):
        scope_prefix = scope["path"].strip("/")
        try:
            parts = scope_prefix.split("/") if scope_prefix else []
            subtree_hash = _navigate_tree(repo.store, entry["root"], parts)
            if subtree_hash:
                return _flatten_tree_to_bytes(repo.store, subtree_hash)
        except (KeyError, json.JSONDecodeError) as exc:
            print(f"[mut-server] warning: failed to read base v{base_version}: {exc}")
        except ObjectNotFoundError as exc:
            print(f"[mut-server] warning: missing object for base v{base_version}: {exc}")
    return {}


def serve(repo_root: str, host: str = "127.0.0.1", port: int = 9742):
    """Start the Mut HTTP server."""
    server_repo = ServerRepo(repo_root)
    server_repo.check_init()

    # Build initial root tree and record version 0 if first start
    if not server_repo.get_root_hash():
        root = server_repo.build_full_tree()
        server_repo.set_root_hash(root)
        if server_repo.get_latest_version() == 0:
            server_repo.record_history(
                0, "server", "initial state", "/", [], root_hash=root,
            )

    MutHandler.server_repo = server_repo

    httpd = HTTPServer((host, port), MutHandler)
    print(f"Mut server listening on http://{host}:{port}")
    print(f"  repo: {server_repo.root}")
    print(f"  version: {server_repo.get_latest_version()}")
    print(f"  root: {server_repo.get_root_hash()[:16]}")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nShutting down.")
        httpd.server_close()
