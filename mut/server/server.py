"""Mut HTTP API server — thin dispatch layer.

Routes requests to handler functions in handlers.py.
Errors raised as MutError subclasses are automatically mapped to
the correct HTTP status code via error.http_status.

Endpoints:
  POST /clone      — Agent requests scope files + history
  POST /push       — Agent pushes local commits (auto-merge)
  POST /pull       — Agent pulls changes since a version
  POST /negotiate  — Hash negotiation for minimal transfer
  POST /invite/<id>— Register via invite
  GET  /health     — Health check
"""

from __future__ import annotations

import json
from http.server import HTTPServer, BaseHTTPRequestHandler

from mut.core.auth import verify_token
from mut.foundation.error import (
    MutError, AuthenticationError, PermissionDenied, PayloadTooLargeError,
)
from mut.server.handlers import (
    handle_clone, handle_push, handle_pull, handle_negotiate, handle_register,
)
from mut.server.repo import ServerRepo


class MutHandler(BaseHTTPRequestHandler):

    server_repo: ServerRepo | None = None  # set by serve()

    MAX_BODY_SIZE = 256 * 1024 * 1024  # 256 MB

    # ── HTTP helpers ──────────────────────────────

    def _read_body(self) -> dict | None:
        length = int(self.headers.get("Content-Length", 0))
        if length == 0:
            return {}
        if length > self.MAX_BODY_SIZE:
            raise PayloadTooLargeError(f"request body too large ({length} bytes)")
        return json.loads(self.rfile.read(length).decode())

    def _send_json(self, data: dict, status: int = 200) -> None:
        body = json.dumps(data, ensure_ascii=False).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _send_error(self, status: int, message: str) -> None:
        self._send_json({"error": message}, status)

    # ── Auth ──────────────────────────────────────

    def _auth(self) -> dict:
        """Verify token, return payload. Raises on failure."""
        auth = self.headers.get("Authorization", "")
        if not auth.startswith("Bearer "):
            raise AuthenticationError("missing or invalid Authorization header")
        token = auth[7:]
        payload = verify_token(token, self.server_repo.get_secret())
        scope = self.server_repo.get_scope_for_agent(payload["agent"])
        if scope is None:
            raise PermissionDenied(f"no scope for agent '{payload['agent']}'")
        payload["_scope"] = scope
        return payload

    # ── Route dispatch (dict-based) ───────────────

    def do_POST(self) -> None:
        # Invite has a dynamic path segment — handle separately
        if self.path.startswith("/invite/"):
            self._dispatch_register()
            return

        post_routes: dict[str, callable] = {
            "/clone": handle_clone,
            "/push": handle_push,
            "/pull": handle_pull,
            "/negotiate": handle_negotiate,
        }
        handler = post_routes.get(self.path)
        if handler is None:
            self._send_error(404, f"unknown endpoint: {self.path}")
            return
        self._dispatch_authed(handler)

    def do_GET(self) -> None:
        get_routes: dict[str, callable] = {
            "/health": lambda: {"status": "ok"},
        }
        handler = get_routes.get(self.path)
        if handler is None:
            self._send_error(404, f"unknown endpoint: {self.path}")
            return
        try:
            self._send_json(handler())
        except MutError as e:
            self._send_error(e.http_status, str(e))

    # ── Dispatch helpers ──────────────────────────

    def _dispatch_authed(self, handler) -> None:
        """Auth → read body → call handler → send response."""
        try:
            auth = self._auth()
            body = self._read_body()
            result = handler(self.server_repo, auth, body)
            self._send_json(result)
        except MutError as e:
            self._send_error(e.http_status, str(e))
        except ValueError as e:
            self._send_error(400, str(e))

    def _dispatch_register(self) -> None:
        """Handle invite registration (no auth required)."""
        invite_id = self.path.split("/invite/", 1)[1].strip("/")
        if not invite_id:
            self._send_error(400, "missing invite ID")
            return
        try:
            result = handle_register(self.server_repo, invite_id)
            self._send_json(result)
        except MutError as e:
            self._send_error(e.http_status, str(e))
        except ValueError as e:
            self._send_error(403, str(e))

    # ── Logging ───────────────────────────────────

    def log_message(self, format, *args) -> None:
        print(f"[mut-server] {args[0]}")


def serve(repo_root: str, host: str = "127.0.0.1", port: int = 9742) -> None:
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
