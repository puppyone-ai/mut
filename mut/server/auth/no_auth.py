"""No authentication — for development and testing.

The credential value is treated as the scope ID directly.
No signature verification, no key lookup. Everything is read-write.

Usage: mut clone http://localhost:9742 --credential scope-src
"""

from __future__ import annotations

from mut.server.auth.base import Authenticator
from mut.server.scope_manager import ScopeManager
from mut.foundation.error import AuthenticationError, PermissionDenied


class NoAuth(Authenticator):
    """Pass-through auth for development. Credential = scope ID."""

    def __init__(self, scopes: ScopeManager):
        self.scopes = scopes

    async def authenticate(self, headers: dict, body: dict) -> dict:
        auth_header = headers.get("authorization", "")
        scope_id = auth_header[7:] if auth_header.startswith("Bearer ") else ""
        if not scope_id:
            raise AuthenticationError(
                "NoAuth mode: send scope ID as Bearer credential"
            )

        scope = self.scopes.get_by_id(scope_id)
        if scope is None:
            raise PermissionDenied(f"scope '{scope_id}' not found")

        scope["mode"] = "rw"
        return {"agent": "dev", "_scope": scope}
