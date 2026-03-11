"""HTTP transport layer for agent → server communication.

Uses only stdlib (urllib) — no external dependencies.
All payloads are JSON.  Binary objects are base64-encoded inside JSON.
"""

import base64
import json
import urllib.request
import urllib.error

from mut.foundation.error import NetworkError


def _make_request(url: str, data: dict = None, token: str = None, method: str = None):
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
    """POST /push — send unpushed snapshots and new objects.

    objects: {hash: base64(bytes)} — only objects the server doesn't have.
    """
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
    """POST /pull — request changes since a version.

    have_hashes: list of object hashes the client already has, so the server
    can skip sending them.
    """
    data = {"since_version": since_version}
    if have_hashes:
        data["have_hashes"] = have_hashes
    return _make_request(f"{server_url}/pull", token=token, data=data)
