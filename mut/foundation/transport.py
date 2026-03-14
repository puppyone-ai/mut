"""HTTP transport layer for agent → server communication.

Uses only stdlib (urllib) — no external dependencies.
All payloads are JSON.  Binary objects are base64-encoded inside JSON.

MutClient encapsulates authenticated requests so that individual ops
don't duplicate auth/transport boilerplate.
"""

from __future__ import annotations

import base64
import json
import urllib.request
import urllib.error
from typing import Optional

from mut.foundation.error import NetworkError
from mut.core.protocol import (
    PROTOCOL_VERSION,
    PushRequest, PullRequest, NegotiateRequest, CloneRequest,
)


def _make_request(url: str, data: dict | None = None,
                  token: str | None = None, method: str | None = None) -> dict:
    """Send an HTTP request, return parsed JSON response."""
    headers: dict[str, str] = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"

    body = json.dumps(data).encode() if data is not None else None
    if method is None:
        method = "POST" if body else "GET"

    req = urllib.request.Request(url, data=body, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            return json.loads(resp.read().decode())
    except urllib.error.HTTPError as e:
        try:
            detail = json.loads(e.read().decode())
            msg = detail.get("error", str(e))
        except Exception:
            msg = str(e)
        raise NetworkError(f"server error ({e.code}): {msg}")
    except urllib.error.URLError as e:
        raise NetworkError(f"cannot reach server: {e.reason}")


class MutClient:
    """Authenticated HTTP client for a single Mut server.

    Encapsulates server URL + token so that ops don't repeat
    the same auth/request boilerplate.
    """

    def __init__(self, server_url: str, token: str):
        self.server_url = server_url.rstrip("/")
        self.token = token

    def _post(self, endpoint: str, data: dict) -> dict:
        return _make_request(
            f"{self.server_url}{endpoint}", data=data, token=self.token,
        )

    def clone(self) -> dict:
        """POST /clone — request scope files and history."""
        return self._post("/clone", CloneRequest().to_dict())

    def push(self, base_version: int, snapshots: list,
             objects: dict[str, bytes]) -> dict:
        """POST /push — send unpushed snapshots and new objects."""
        req = PushRequest(
            base_version=base_version,
            snapshots=snapshots,
            objects={h: base64.b64encode(data).decode()
                     for h, data in objects.items()},
        )
        return self._post("/push", req.to_dict())

    def negotiate(self, hashes: list[str]) -> dict:
        """POST /negotiate — ask server which objects it needs."""
        req = NegotiateRequest(hashes=hashes)
        return self._post("/negotiate", req.to_dict())

    def pull(self, since_version: int,
             have_hashes: Optional[list[str]] = None) -> dict:
        """POST /pull — request changes since a version."""
        req = PullRequest(
            since_version=since_version,
            have_hashes=have_hashes or [],
        )
        return self._post("/pull", req.to_dict())


# ── Legacy function-based API (thin wrappers for backward compat) ──

def post_clone(server_url: str, token: str) -> dict:
    return MutClient(server_url, token).clone()


def post_push(server_url: str, token: str, base_version: int,
              snapshots: list, objects: dict) -> dict:
    return MutClient(server_url, token).push(base_version, snapshots, objects)


def post_negotiate(server_url: str, token: str, hashes: list) -> dict:
    return MutClient(server_url, token).negotiate(hashes)


def post_pull(server_url: str, token: str, since_version: int,
              have_hashes: list = None) -> dict:
    return MutClient(server_url, token).pull(since_version, have_hashes)
