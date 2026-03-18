"""Tests for core/protocol.py — typed protocol models and normalize_path."""

import pytest

from mut.core.protocol import (
    PROTOCOL_VERSION,
    normalize_path,
    CloneRequest, CloneResponse, ScopeInfo,
    PushRequest, PushResponse,
    PullRequest, PullResponse,
    NegotiateRequest, NegotiateResponse,
    ErrorResponse,
)


# ── normalize_path ─────────────────────────────

class TestNormalizePath:
    def test_strips_leading_slash(self):
        assert normalize_path("/src/") == "src"

    def test_strips_trailing_slash(self):
        assert normalize_path("src/") == "src"

    def test_strips_both(self):
        assert normalize_path("/src/components/") == "src/components"

    def test_empty_string(self):
        assert normalize_path("") == ""

    def test_root_slash(self):
        assert normalize_path("/") == ""

    def test_no_slashes(self):
        assert normalize_path("src") == "src"

    def test_multiple_slashes(self):
        # strip only strips outermost chars, inner slashes are preserved
        # but leading/trailing are stripped one layer at a time by strip("/")
        assert normalize_path("///src///") == "src"

    def test_nested_path(self):
        assert normalize_path("/a/b/c/d/") == "a/b/c/d"


# ── CloneRequest ───────────────────────────────

class TestCloneRequest:
    def test_default_version(self):
        req = CloneRequest()
        assert req.protocol_version == PROTOCOL_VERSION

    def test_to_dict(self):
        d = CloneRequest().to_dict()
        assert d["protocol_version"] == PROTOCOL_VERSION

    def test_from_dict_empty(self):
        req = CloneRequest.from_dict({})
        assert req.protocol_version == 1

    def test_from_dict_with_version(self):
        req = CloneRequest.from_dict({"protocol_version": 2})
        assert req.protocol_version == 2


# ── ScopeInfo ──────────────────────────────────

class TestScopeInfo:
    def test_defaults(self):
        s = ScopeInfo()
        assert s.path == "/"
        assert s.exclude == []
        assert s.mode == "rw"

    def test_roundtrip(self):
        s = ScopeInfo(path="/src/", exclude=["/src/vendor/"], mode="r")
        d = s.to_dict()
        s2 = ScopeInfo.from_dict(d)
        assert s2.path == s.path
        assert s2.exclude == s.exclude
        assert s2.mode == s.mode


# ── PushRequest / PushResponse ─────────────────

class TestPush:
    def test_push_request_roundtrip(self):
        req = PushRequest(base_version=3, snapshots=[{"id": 1}], objects={"abc": "base64"})
        d = req.to_dict()
        req2 = PushRequest.from_dict(d)
        assert req2.base_version == 3
        assert req2.snapshots == [{"id": 1}]
        assert req2.objects == {"abc": "base64"}
        assert req2.protocol_version == PROTOCOL_VERSION

    def test_push_response_no_merge(self):
        resp = PushResponse(status="ok", version=5, pushed=2, root="aabb")
        d = resp.to_dict()
        assert "merged" not in d
        assert d["status"] == "ok"
        assert d["version"] == 5

    def test_push_response_with_merge(self):
        resp = PushResponse(status="ok", version=5, pushed=2, root="aabb",
                            merged=True, conflicts=3)
        d = resp.to_dict()
        assert d["merged"] is True
        assert d["conflicts"] == 3


# ── PullRequest / PullResponse ─────────────────

class TestPull:
    def test_pull_request_roundtrip(self):
        req = PullRequest(since_version=7, have_hashes=["aaa", "bbb"])
        d = req.to_dict()
        req2 = PullRequest.from_dict(d)
        assert req2.since_version == 7
        assert req2.have_hashes == ["aaa", "bbb"]

    def test_pull_request_omits_empty_hashes(self):
        req = PullRequest(since_version=0)
        d = req.to_dict()
        # Empty have_hashes is omitted from the dict to save bandwidth
        assert "have_hashes" not in d

    def test_pull_response(self):
        resp = PullResponse(status="updated", version=10,
                            files={"a.txt": "b64"}, objects={"h": "b64"},
                            history=[{"id": 1}])
        d = resp.to_dict()
        assert d["status"] == "updated"
        assert d["version"] == 10
        assert "a.txt" in d["files"]


# ── NegotiateRequest / NegotiateResponse ───────

class TestNegotiate:
    def test_roundtrip(self):
        req = NegotiateRequest(hashes=["aaa", "bbb", "ccc"])
        d = req.to_dict()
        req2 = NegotiateRequest.from_dict(d)
        assert req2.hashes == ["aaa", "bbb", "ccc"]

    def test_response(self):
        resp = NegotiateResponse(missing=["bbb"])
        d = resp.to_dict()
        assert d["missing"] == ["bbb"]
        assert d["protocol_version"] == PROTOCOL_VERSION


# ── ErrorResponse ──────────────────────────────

class TestErrorResponse:
    def test_to_dict(self):
        resp = ErrorResponse(error="not found", code=404)
        d = resp.to_dict()
        assert d["error"] == "not found"
        assert d["protocol_version"] == PROTOCOL_VERSION

    def test_default_code(self):
        resp = ErrorResponse(error="boom")
        assert resp.code == 500
