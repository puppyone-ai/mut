"""Unit tests for mut.server.auth — ApiKeyAuth and NoAuth."""

import asyncio
import pytest

from mut.server.auth.api_key import ApiKeyAuth
from mut.server.auth.no_auth import NoAuth
from mut.server.scope_manager import ScopeManager
from mut.foundation.error import AuthenticationError, PermissionDenied


def _run(coro):
    """Run async coroutine from sync test."""
    try:
        return asyncio.run(coro)
    finally:
        # Reset event loop so ServerRepo (asyncio.Lock) works in other tests (Python 3.9)
        asyncio.set_event_loop(None)


@pytest.fixture
def scope_manager(tmp_path):
    """ScopeManager with a test scope defined."""
    scopes_dir = tmp_path / "scopes"
    scopes_dir.mkdir()
    sm = ScopeManager(scopes_dir)
    sm.add("scope-src", "/src/")
    return sm


@pytest.fixture
def api_key_auth(scope_manager, tmp_path):
    """ApiKeyAuth with credentials file in tmp_path."""
    creds_file = tmp_path / "credentials.json"
    return ApiKeyAuth(scope_manager, creds_file)


@pytest.fixture
def no_auth(scope_manager):
    """NoAuth with the shared scope manager."""
    return NoAuth(scope_manager)


# ─── ApiKeyAuth ───────────────────────────────────────────────────────────


def test_api_key_valid_key_returns_auth_context(api_key_auth):
    key = api_key_auth.issue("agent-A", "scope-src", "rw")
    result = _run(
        api_key_auth.authenticate(
            {"authorization": f"Bearer {key}"},
            {},
        )
    )
    assert result["agent"] == "agent-A"
    assert result["_scope"]["id"] == "scope-src"
    assert result["_scope"]["path"] == "/src/"
    assert result["_scope"]["mode"] == "rw"


def test_api_key_invalid_key_raises_authentication_error(api_key_auth):
    with pytest.raises(AuthenticationError, match="invalid API key"):
        _run(
            api_key_auth.authenticate(
                {"authorization": "Bearer mut_invalid_key_12345"},
                {},
            )
        )


def test_api_key_missing_header_raises_error(api_key_auth):
    with pytest.raises(AuthenticationError, match="missing or invalid"):
        _run(api_key_auth.authenticate({}, {}))


def test_api_key_wrong_header_format_raises_error(api_key_auth):
    with pytest.raises(AuthenticationError, match="missing or invalid"):
        _run(
            api_key_auth.authenticate(
                {"authorization": "Basic abc123"},
                {},
            )
        )


def test_api_key_scope_not_found_raises_permission_denied(scope_manager, tmp_path):
    """Key for unknown scope_id raises PermissionDenied."""
    creds_file = tmp_path / "creds.json"
    auth = ApiKeyAuth(scope_manager, creds_file)
    key = auth.issue("agent-X", "nonexistent-scope", "rw")
    # Scope was never added to scope_manager
    with pytest.raises(PermissionDenied, match="scope.*not found"):
        _run(auth.authenticate({"authorization": f"Bearer {key}"}, {}))


# ─── NoAuth ───────────────────────────────────────────────────────────────


def test_no_auth_scope_id_as_credential_works(no_auth):
    result = _run(
        no_auth.authenticate(
            {"authorization": "Bearer scope-src"},
            {},
        )
    )
    assert result["agent"] == "dev"
    assert result["_scope"]["id"] == "scope-src"
    assert result["_scope"]["path"] == "/src/"
    assert result["_scope"]["mode"] == "rw"


def test_no_auth_invalid_scope_id_raises_permission_denied(no_auth):
    with pytest.raises(PermissionDenied, match="scope.*not found"):
        _run(
            no_auth.authenticate(
                {"authorization": "Bearer nonexistent-scope"},
                {},
            )
        )


def test_no_auth_missing_header_raises_error(no_auth):
    with pytest.raises(AuthenticationError, match="send scope ID"):
        _run(no_auth.authenticate({}, {}))


def test_no_auth_empty_bearer_raises_error(no_auth):
    with pytest.raises(AuthenticationError, match="send scope ID"):
        _run(
            no_auth.authenticate(
                {"authorization": "Bearer "},
                {},
            )
        )
