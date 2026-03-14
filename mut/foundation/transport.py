"""HTTP transport layer for agent → server communication.

Uses only stdlib (urllib for sync, asyncio for async) — no external dependencies.
All payloads are JSON. Binary objects are base64-encoded inside JSON.
"""

from __future__ import annotations

import asyncio
import base64
import json
import urllib.request
import urllib.error

from mut.foundation.error import NetworkError


# ── Sync transport ────────────────────────────

def _make_request(url: str, data: dict = None, token: str = None,
                  method: str = None) -> dict:
    """Send an HTTP request, return parsed JSON response."""
    headers = {"Content-Type": "application/json"}
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


def post_clone(server_url: str, token: str) -> dict:
    """POST /clone — request scope files and history."""
    return _make_request(f"{server_url}/clone", data={}, token=token)


def post_push(server_url: str, token: str, base_version: int,
              snapshots: list, objects: dict) -> dict:
    """POST /push — send unpushed snapshots and new objects."""
    return _make_request(f"{server_url}/push", token=token, data={
        "base_version": base_version,
        "snapshots": snapshots,
        "objects": {h: base64.b64encode(data).decode() for h, data in objects.items()},
    })


def post_negotiate(server_url: str, token: str, hashes: list) -> dict:
    """POST /negotiate — ask server which objects it needs."""
    return _make_request(f"{server_url}/negotiate", token=token, data={
        "hashes": hashes,
    })


def post_pull(server_url: str, token: str, since_version: int,
              have_hashes: list = None) -> dict:
    """POST /pull — request changes since a version."""
    data = {"since_version": since_version}
    if have_hashes:
        data["have_hashes"] = have_hashes
    return _make_request(f"{server_url}/pull", token=token, data=data)


# ── Async transport ───────────────────────────

class AsyncMutClient:
    """Async HTTP client for a single Mut server.

    Uses asyncio.open_connection to send raw HTTP requests — zero external deps.
    Falls back to asyncio.to_thread(urllib) for simplicity and reliability.
    """

    def __init__(self, server_url: str, token: str):
        self.server_url = server_url.rstrip("/")
        self.token = token

    async def _post(self, endpoint: str, data: dict) -> dict:
        url = f"{self.server_url}{endpoint}"
        return await asyncio.to_thread(
            _make_request, url, data, self.token,
        )

    async def clone(self) -> dict:
        return await self._post("/clone", {})

    async def push(self, base_version: int, snapshots: list,
                   objects: dict[str, bytes]) -> dict:
        return await self._post("/push", {
            "base_version": base_version,
            "snapshots": snapshots,
            "objects": {h: base64.b64encode(data).decode()
                        for h, data in objects.items()},
        })

    async def negotiate(self, hashes: list[str]) -> dict:
        return await self._post("/negotiate", {"hashes": hashes})

    async def pull(self, since_version: int,
                   have_hashes: list[str] | None = None) -> dict:
        data = {"since_version": since_version}
        if have_hashes:
            data["have_hashes"] = have_hashes
        return await self._post("/pull", data)
