"""Tests for enhanced ApiKeyAuth — revocation, identity binding, cross-scope rejection."""

import asyncio
import pytest

from mut.server.auth.api_key import ApiKeyAuth
from mut.server.auth.no_auth import NoAuth
from mut.server.scope_manager import ScopeManager, FileSystemScopeBackend
from mut.foundation.error import AuthenticationError, PermissionDenied


def _run(coro):
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


@pytest.fixture
def scopes(tmp_path):
    scopes_dir = tmp_path / "scopes"
    scopes_dir.mkdir()
    sm = ScopeManager(FileSystemScopeBackend(scopes_dir))
    sm.add("scope-docs", "/docs/")
    sm.add("scope-src", "/src/")
    sm.add("scope-root", "/")
    return sm


@pytest.fixture
def auth(scopes, tmp_path):
    return ApiKeyAuth(scopes, tmp_path / "creds.json")


# ── Key Revocation ────────────────────────────────────────────

class TestRevocation:
    def test_revoke_key(self, auth):
        key = auth.issue("alice", "scope-docs", "rw")
        assert auth.revoke(key) is True
        with pytest.raises(AuthenticationError, match="revoked"):
            _run(auth.authenticate({"authorization": f"Bearer {key}"}, {}))

    def test_revoke_nonexistent_key(self, auth):
        assert auth.revoke("mut_doesnotexist") is False

    def test_revoke_by_scope(self, auth):
        k1 = auth.issue("alice", "scope-docs", "rw")
        k2 = auth.issue("bob", "scope-docs", "r")
        k3 = auth.issue("charlie", "scope-src", "rw")

        count = auth.revoke_by_scope("scope-docs")
        assert count == 2

        # docs keys revoked
        with pytest.raises(AuthenticationError, match="revoked"):
            _run(auth.authenticate({"authorization": f"Bearer {k1}"}, {}))
        with pytest.raises(AuthenticationError, match="revoked"):
            _run(auth.authenticate({"authorization": f"Bearer {k2}"}, {}))

        # src key still works
        result = _run(auth.authenticate({"authorization": f"Bearer {k3}"}, {}))
        assert result["agent"] == "charlie"

    def test_revoked_key_cannot_push(self, auth):
        key = auth.issue("alice", "scope-docs", "rw")
        auth.revoke(key)
        with pytest.raises(AuthenticationError):
            _run(auth.authenticate({"authorization": f"Bearer {key}"}, {}))


# ── User Identity Binding ──────────────────────────────────────

class TestIdentityBinding:
    def test_key_with_identity_succeeds_matching(self, auth):
        key = auth.issue("alice", "scope-docs", "rw", user_identity="alice@co.com")
        result = _run(auth.authenticate(
            {"authorization": f"Bearer {key}", "x-mut-user": "alice@co.com"}, {}
        ))
        assert result["agent"] == "alice"

    def test_key_with_identity_rejects_mismatch(self, auth):
        key = auth.issue("alice", "scope-docs", "rw", user_identity="alice@co.com")
        with pytest.raises(AuthenticationError, match="identity mismatch"):
            _run(auth.authenticate(
                {"authorization": f"Bearer {key}", "x-mut-user": "bob@co.com"}, {}
            ))

    def test_key_with_identity_allows_no_header(self, auth):
        """If client doesn't send X-Mut-User, still allowed (backwards compat)."""
        key = auth.issue("alice", "scope-docs", "rw", user_identity="alice@co.com")
        result = _run(auth.authenticate(
            {"authorization": f"Bearer {key}"}, {}
        ))
        assert result["agent"] == "alice"

    def test_key_without_identity_allows_any_user(self, auth):
        """Keys without identity binding accept any user header."""
        key = auth.issue("agent-A", "scope-docs", "rw")
        result = _run(auth.authenticate(
            {"authorization": f"Bearer {key}", "x-mut-user": "anyone@co.com"}, {}
        ))
        assert result["agent"] == "agent-A"


# ── Cross-Scope Rejection ─────────────────────────────────────

class TestCrossScopeRejection:
    def test_key_only_accesses_own_scope(self, auth):
        key_docs = auth.issue("alice", "scope-docs", "rw")
        result = _run(auth.authenticate(
            {"authorization": f"Bearer {key_docs}"}, {}
        ))
        # Key is for scope-docs → /docs/
        assert result["_scope"]["path"] == "/docs/"

    def test_key_for_deleted_scope_fails(self, auth, scopes):
        key = auth.issue("alice", "scope-docs", "rw")
        scopes.delete("scope-docs")
        with pytest.raises(PermissionDenied, match="not found"):
            _run(auth.authenticate(
                {"authorization": f"Bearer {key}"}, {}
            ))

    def test_readonly_key_returns_readonly_mode(self, auth):
        key = auth.issue("reader", "scope-src", "r")
        result = _run(auth.authenticate(
            {"authorization": f"Bearer {key}"}, {}
        ))
        assert result["_scope"]["mode"] == "r"

    def test_multiple_keys_different_scopes(self, auth):
        k_docs = auth.issue("alice", "scope-docs", "rw")
        k_src = auth.issue("alice", "scope-src", "r")

        r1 = _run(auth.authenticate({"authorization": f"Bearer {k_docs}"}, {}))
        r2 = _run(auth.authenticate({"authorization": f"Bearer {k_src}"}, {}))

        assert r1["_scope"]["path"] == "/docs/"
        assert r1["_scope"]["mode"] == "rw"
        assert r2["_scope"]["path"] == "/src/"
        assert r2["_scope"]["mode"] == "r"


# ── Auth Error Cases ──────────────────────────────────────────

class TestAuthErrors:
    def test_missing_authorization_header(self, auth):
        with pytest.raises(AuthenticationError, match="missing"):
            _run(auth.authenticate({}, {}))

    def test_invalid_key(self, auth):
        with pytest.raises(AuthenticationError, match="invalid"):
            _run(auth.authenticate(
                {"authorization": "Bearer mut_fake_key"}, {}
            ))

    def test_malformed_bearer(self, auth):
        with pytest.raises(AuthenticationError, match="missing"):
            _run(auth.authenticate(
                {"authorization": "Basic abc123"}, {}
            ))
