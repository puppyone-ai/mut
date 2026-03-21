"""API key authentication — recommended default.

Each credential is a random key mapped to (agent, scope, mode) with
optional user identity binding and revocation support.

Credentials are stored in a JSON file outside .mut-server/:

    {
        "mut_a1b2c3d4...": {
            "agent": "agent-A",
            "scope_id": "scope-src",
            "mode": "rw",
            "user_identity": "alice@company.com",
            "revoked_at": null
        }
    }

Clients send the key as: Authorization: Bearer <key>
User identity (optional): X-Mut-User: alice@company.com
"""

from __future__ import annotations

import json
import secrets
from datetime import datetime, timezone
from pathlib import Path

from mut.server.auth.base import Authenticator
from mut.server.scope_manager import ScopeManager
from mut.foundation.error import AuthenticationError, PermissionDenied


class ApiKeyAuth(Authenticator):
    """Authenticate requests via pre-issued API keys."""

    def __init__(self, scopes: ScopeManager, credentials_file: Path):
        self.scopes = scopes
        self.credentials_file = Path(credentials_file)

    def _load_credentials(self) -> dict:
        if not self.credentials_file.exists():
            return {}
        try:
            data = json.loads(
                self.credentials_file.read_text(encoding="utf-8")
            )
        except (json.JSONDecodeError, OSError):
            return {}
        return data if isinstance(data, dict) else {}

    def _save_credentials(self, creds: dict):
        self.credentials_file.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.credentials_file.with_suffix(".tmp")
        try:
            tmp.write_text(
                json.dumps(creds, indent=2, ensure_ascii=False), encoding="utf-8",
            )
            tmp.replace(self.credentials_file)
        except OSError:
            # Clean up temp file on failure to avoid stale .tmp files
            if tmp.exists():
                tmp.unlink()
            raise

    async def authenticate(self, headers: dict, body: dict) -> dict:
        auth_header = headers.get("authorization", "")
        if not auth_header.startswith("Bearer "):
            raise AuthenticationError("missing or invalid Authorization header")
        key = auth_header[7:]

        creds = self._load_credentials()
        entry = creds.get(key)
        if not isinstance(entry, dict):
            raise AuthenticationError("invalid API key")

        # Check revocation
        if entry.get("revoked_at"):
            raise AuthenticationError("API key has been revoked")

        agent = entry.get("agent")
        scope_id = entry.get("scope_id")
        if not agent or not scope_id:
            raise AuthenticationError("malformed credential entry")

        # Verify user identity if key has one bound
        bound_identity = entry.get("user_identity")
        if bound_identity:
            request_identity = headers.get("x-mut-user", "")
            if request_identity and request_identity != bound_identity:
                raise AuthenticationError(
                    "user identity mismatch: key is bound to a different user"
                )

        scope = self.scopes.get_by_id(scope_id)
        if scope is None:
            raise PermissionDenied(f"scope '{scope_id}' not found")

        scope["mode"] = entry.get("mode", "rw")
        return {"agent": agent, "_scope": scope}

    def issue(self, agent: str, scope_id: str, mode: str = "rw",
              user_identity: str = "") -> str:
        """Issue a new API key for an agent+scope. Returns the key string."""
        key = f"mut_{secrets.token_hex(16)}"
        creds = self._load_credentials()
        entry = {"agent": agent, "scope_id": scope_id, "mode": mode}
        if user_identity:
            entry["user_identity"] = user_identity
        creds[key] = entry
        self._save_credentials(creds)
        return key

    def revoke(self, key: str) -> bool:
        """Revoke an API key. Returns True if key existed."""
        creds = self._load_credentials()
        entry = creds.get(key)
        if not isinstance(entry, dict):
            return False
        entry["revoked_at"] = datetime.now(timezone.utc).isoformat()
        self._save_credentials(creds)
        return True

    def revoke_by_scope(self, scope_id: str) -> int:
        """Revoke all keys for a given scope. Returns count of revoked keys."""
        creds = self._load_credentials()
        now = datetime.now(timezone.utc).isoformat()
        count = 0
        for entry in creds.values():
            if (isinstance(entry, dict)
                    and entry.get("scope_id") == scope_id
                    and not entry.get("revoked_at")):
                entry["revoked_at"] = now
                count += 1
        if count:
            self._save_credentials(creds)
        return count
