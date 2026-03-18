"""Pluggable authentication for Mut server.

MUT core protocol is auth-agnostic — it only knows about scopes.
This module provides the Authenticator interface and built-in implementations:

  - ApiKeyAuth: Recommended default. One API key per scope credential.
  - NoAuth:     Development/testing. Credential = scope ID, no verification.

Custom auth (OAuth, mTLS, etc.) can be added by implementing Authenticator.
"""

from mut.server.auth.base import Authenticator
from mut.server.auth.api_key import ApiKeyAuth
from mut.server.auth.no_auth import NoAuth

__all__ = ["Authenticator", "ApiKeyAuth", "NoAuth"]
