"""JWT-style token signing and verification using HMAC-SHA256.

Token format:  base64url(header).base64url(payload).base64url(signature)

Payload fields:
  - agent: agent ID string
  - scope: path prefix this agent can access  (e.g. "/src/")
  - mode:  "r" or "rw"
  - exp:   expiry timestamp (seconds since epoch), 0 = never expires
"""

import base64
import hashlib
import hmac
import json
import time

from mut.foundation.error import AuthenticationError


def _b64url_encode(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def _b64url_decode(s: str) -> bytes:
    padding = 4 - len(s) % 4
    if padding != 4:
        s += "=" * padding
    return base64.urlsafe_b64decode(s)


_HEADER = _b64url_encode(json.dumps({"alg": "HS256", "typ": "MUT"}).encode())


def sign_token(
    secret: str,
    agent_id: str,
    scope: str,
    mode: str = "rw",
    expiry_seconds: int = 0
) -> str:
    """Issue a token.  expiry_seconds=0 means no expiry."""
    payload = {
        "agent": agent_id,
        "scope": scope,
        "mode": mode,
        "exp": int(time.time()) + expiry_seconds if expiry_seconds > 0 else 0,
    }
    payload_b64 = _b64url_encode(json.dumps(payload, sort_keys=True).encode())
    signing_input = f"{_HEADER}.{payload_b64}"
    sig = hmac.new(secret.encode(), signing_input.encode(), hashlib.sha256).digest()
    return f"{signing_input}.{_b64url_encode(sig)}"


def verify_token(token: str, secret: str) -> dict:
    """Verify signature and expiry.  Returns payload dict or raises AuthenticationError."""
    parts = token.split(".")
    if len(parts) != 3:
        raise AuthenticationError("malformed token")

    signing_input = f"{parts[0]}.{parts[1]}"
    expected_sig = hmac.new(secret.encode(), signing_input.encode(), hashlib.sha256).digest()
    actual_sig = _b64url_decode(parts[2])

    if not hmac.compare_digest(expected_sig, actual_sig):
        raise AuthenticationError("invalid token signature")

    payload = json.loads(_b64url_decode(parts[1]))

    if payload.get("exp", 0) > 0 and payload["exp"] < time.time():
        raise AuthenticationError("token expired")

    return payload
