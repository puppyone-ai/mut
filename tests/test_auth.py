"""Unit tests for core/auth.py — JWT-style token signing/verification."""

import time
import pytest

from mut.core.auth import sign_token, verify_token
from mut.foundation.error import PermissionDenied


SECRET = "test-secret-key-1234567890abcdef"


def test_sign_and_verify():
    token = sign_token(SECRET, "agent-A", "/src/", "rw")
    payload = verify_token(token, SECRET)
    assert payload["agent"] == "agent-A"
    assert payload["scope"] == "/src/"
    assert payload["mode"] == "rw"


def test_wrong_secret():
    token = sign_token(SECRET, "agent-A", "/src/", "rw")
    with pytest.raises(PermissionDenied, match="invalid token signature"):
        verify_token(token, "wrong-secret")


def test_malformed_token():
    with pytest.raises(PermissionDenied, match="malformed"):
        verify_token("not.a.valid.token.format", SECRET)


def test_expired_token():
    token = sign_token(SECRET, "agent-A", "/src/", "rw", expiry_seconds=1)
    time.sleep(1.5)
    with pytest.raises(PermissionDenied, match="expired"):
        verify_token(token, SECRET)


def test_no_expiry():
    token = sign_token(SECRET, "agent-A", "/src/", "rw", expiry_seconds=0)
    payload = verify_token(token, SECRET)
    assert payload["exp"] == 0


def test_token_roundtrip_preserves_scope():
    token = sign_token(SECRET, "agent-X", "/docs/api/", "r", expiry_seconds=3600)
    payload = verify_token(token, SECRET)
    assert payload["scope"] == "/docs/api/"
    assert payload["mode"] == "r"
