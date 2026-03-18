"""API key authentication — recommended default.

Each credential is a random key mapped to (agent, scope, mode).
Credentials are stored in a JSON file outside .mut-server/:

    {
        "mut_a1b2c3d4...": {
            "agent": "agent-A",
            "scope_id": "scope-src",
            "mode": "rw"
        }
    }

Clients send the key as: Authorization: Bearer <key>
"""

from __future__ import annotations

import json
import secrets
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
        tmp.write_text(
            json.dumps(creds, indent=2, ensure_ascii=False), encoding="utf-8",
        )
        tmp.replace(self.credentials_file)

    async def authenticate(self, headers: dict, body: dict) -> dict:
        auth_header = headers.get("authorization", "")
        if not auth_header.startswith("Bearer "):
            raise AuthenticationError("missing or invalid Authorization header")
        key = auth_header[7:]

        creds = self._load_credentials()
        entry = creds.get(key)
        if not isinstance(entry, dict):
            raise AuthenticationError("invalid API key")

        agent = entry.get("agent")
        scope_id = entry.get("scope_id")
        if not agent or not scope_id:
            raise AuthenticationError("malformed credential entry")

        scope = self.scopes.get_by_id(scope_id)
        if scope is None:
            raise PermissionDenied(f"scope '{scope_id}' not found")

        scope["mode"] = entry.get("mode", "rw")
        return {"agent": agent, "_scope": scope}

    def issue(self, agent: str, scope_id: str, mode: str = "rw") -> str:
        """Issue a new API key for an agent+scope. Returns the key string."""
        key = f"mut_{secrets.token_hex(16)}"
        creds = self._load_credentials()
        creds[key] = {"agent": agent, "scope_id": scope_id, "mode": mode}
        self._save_credentials(creds)
        return key
