"""Authenticator interface — the contract between auth and MUT core.

Every authenticator must produce an auth context dict with:
  - "agent":  str   — operator identity (for audit trail)
  - "_scope": dict  — resolved scope with mode injected:
      {"id": "...", "path": "/src/", "exclude": [...], "mode": "rw"}

Handlers receive this dict and never touch auth internals.
"""

from __future__ import annotations

from abc import ABC, abstractmethod


class Authenticator(ABC):
    """Base class for pluggable server authentication.

    Subclasses resolve an incoming request into an auth context
    that maps the caller to a specific scope with a specific mode.
    MUT core never sees credentials — only the resolved context.
    """

    @abstractmethod
    async def authenticate(self, headers: dict, body: dict) -> dict:
        """Authenticate a request and return the auth context.

        Returns:
            {"agent": str, "_scope": {"id", "path", "exclude", "mode"}}

        Raises:
            AuthenticationError: on invalid / missing credentials
            PermissionDenied:    on valid credentials but no scope access
        """

    async def close(self):
        """Optional cleanup hook called on server shutdown."""
