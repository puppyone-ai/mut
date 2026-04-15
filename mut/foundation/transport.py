"""HTTP transport layer for agent → server communication.

Uses only stdlib (urllib for sync, asyncio for async) — no external dependencies.
All payloads are JSON. Binary objects are base64-encoded inside JSON.

MutClient encapsulates server URL + credential so that individual ops
don't duplicate transport boilerplate. The credential is an opaque string
— could be an API key, token, or anything the server's auth layer accepts.
"""

from __future__ import annotations

import asyncio
import base64
import json
import os
import time
import urllib.request
import urllib.error
from typing import Optional

from mut.foundation.error import NetworkError
from mut.core.protocol import (
    PushRequest, PullRequest, NegotiateRequest, CloneRequest,
)


_DEFAULT_TIMEOUT = int(os.environ.get("MUT_TIMEOUT", "60"))
_MAX_RETRIES = 3
_RETRY_BACKOFF = 1.0
_RETRYABLE_CODES = {408, 429, 500, 502, 503, 504}


def _parse_http_error(e: urllib.error.HTTPError) -> str:
    try:
        detail = json.loads(e.read().decode())
        return detail.get("error", str(e))
    except Exception:
        return str(e)


def _retry_delay(e: urllib.error.HTTPError | None, attempt: int) -> float:
    if isinstance(e, urllib.error.HTTPError):
        retry_after = e.headers.get("Retry-After")
        if retry_after:
            try:
                return float(retry_after)
            except (ValueError, TypeError):
                pass  # non-numeric Retry-After (e.g. HTTP-date); fall through
    return _RETRY_BACKOFF * (2 ** attempt)


def _do_request(url: str, body: bytes | None, headers: dict,
                method: str, timeout: int) -> dict:
    req = urllib.request.Request(url, data=body, headers=headers, method=method)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode())


def _is_retryable(e: Exception) -> bool:
    if isinstance(e, urllib.error.HTTPError):
        return e.code in _RETRYABLE_CODES
    return isinstance(e, urllib.error.URLError)


def _make_request(url: str, data: dict | None = None,
                  credential: str | None = None, method: str | None = None,
                  timeout: int = _DEFAULT_TIMEOUT,
                  user_identity: str | None = None) -> dict:
    """Send an HTTP request with retry on transient failures."""
    headers: dict[str, str] = {"Content-Type": "application/json"}
    if credential:
        headers["Authorization"] = f"Bearer {credential}"
    if user_identity:
        headers["X-Mut-User"] = user_identity

    body = json.dumps(data).encode() if data is not None else None
    if method is None:
        method = "POST" if body else "GET"

    last_exc: Exception | None = None
    for attempt in range(_MAX_RETRIES):
        try:
            return _do_request(url, body, headers, method, timeout)
        except urllib.error.URLError as e:
            last_exc = e
            if _is_retryable(e) and attempt < _MAX_RETRIES - 1:
                time.sleep(_retry_delay(e, attempt))
                continue
            _raise_as_network_error(e)

    raise NetworkError(f"request failed after {_MAX_RETRIES} retries: {last_exc}")


def _raise_as_network_error(e: Exception):
    if isinstance(e, urllib.error.HTTPError):
        raise NetworkError(f"server error ({e.code}): {_parse_http_error(e)}")
    if isinstance(e, urllib.error.URLError):
        raise NetworkError(f"cannot reach server: {e.reason}")
    raise NetworkError(str(e))


class MutClient:
    """HTTP client for a single Mut server.

    Credential is an opaque string — the server's auth layer
    decides how to interpret it (API key, token, scope ID, etc.).
    user_identity is sent as X-Mut-User header for identity binding.
    """

    def __init__(self, server_url: str, credential: str,
                 user_identity: str = ""):
        self.server_url = server_url.rstrip("/")
        self.credential = credential
        self.user_identity = user_identity

    def _post(self, endpoint: str, data: dict) -> dict:
        return _make_request(
            f"{self.server_url}{endpoint}", data=data,
            credential=self.credential,
            user_identity=self.user_identity or None,
        )

    def post(self, endpoint: str, data: dict) -> dict:
        """Public post method for custom endpoints (e.g. /rollback)."""
        return self._post(endpoint, data)

    def clone(self) -> dict:
        return self._post("/clone", CloneRequest().to_dict())

    def push(self, base_version: int, snapshots: list,
             objects: dict[str, bytes]) -> dict:
        req = PushRequest(
            base_version=base_version,
            snapshots=snapshots,
            objects={h: base64.b64encode(data).decode()
                     for h, data in objects.items()},
        )
        return self._post("/push", req.to_dict())

    def negotiate(self, hashes: list[str]) -> dict:
        req = NegotiateRequest(hashes=hashes)
        return self._post("/negotiate", req.to_dict())

    def pull(self, since_version: int,
             have_hashes: Optional[list[str]] = None) -> dict:
        req = PullRequest(
            since_version=since_version,
            have_hashes=have_hashes or [],
        )
        return self._post("/pull", req.to_dict())

    def pull_version(self, version: int) -> dict:
        from mut.core.protocol import PullVersionRequest
        req = PullVersionRequest(version=version)
        return self._post("/pull-version", req.to_dict())

    def rollback(self, target_version: int) -> dict:
        from mut.core.protocol import RollbackRequest
        req = RollbackRequest(target_version=target_version)
        return self._post("/rollback", req.to_dict())


class AsyncMutClient:
    """Async HTTP client for a single Mut server."""

    def __init__(self, server_url: str, credential: str,
                 user_identity: str = ""):
        self.server_url = server_url.rstrip("/")
        self.credential = credential
        self.user_identity = user_identity

    async def _post(self, endpoint: str, data: dict) -> dict:
        url = f"{self.server_url}{endpoint}"
        return await asyncio.to_thread(
            _make_request, url, data, self.credential,
            None, _DEFAULT_TIMEOUT, self.user_identity or None,
        )

    async def clone(self) -> dict:
        return await self._post("/clone", CloneRequest().to_dict())

    async def push(self, base_version: int, snapshots: list,
                   objects: dict[str, bytes]) -> dict:
        req = PushRequest(
            base_version=base_version,
            snapshots=snapshots,
            objects={h: base64.b64encode(data).decode()
                     for h, data in objects.items()},
        )
        return await self._post("/push", req.to_dict())

    async def negotiate(self, hashes: list[str]) -> dict:
        req = NegotiateRequest(hashes=hashes)
        return await self._post("/negotiate", req.to_dict())

    async def pull(self, since_version: int,
                   have_hashes: list[str] | None = None) -> dict:
        req = PullRequest(
            since_version=since_version,
            have_hashes=have_hashes or [],
        )
        return await self._post("/pull", req.to_dict())
