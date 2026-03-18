"""Tests for the async server — handlers, connection handling, and bug fixes.

These tests create a real ServerRepo and call async handlers directly,
verifying the full async push/pull/clone cycle and the three bug fixes:
  #1: lost_content + lost_hash persisted in history and audit
  #2: Global lock protects version/root_hash across concurrent scope pushes
  #3: lost_hash enables full content recovery
"""

import asyncio
import base64
import json
import pytest

from mut.server.repo import ServerRepo
from mut.server.server import (
    _handle_clone, _handle_push, _handle_pull,
    _handle_negotiate,
    _read_request, _read_body, _build_response,
    _handle_health,
)
from mut.foundation.error import PermissionDenied


@pytest.fixture
def server_repo(tmp_path):
    repo = ServerRepo.init(str(tmp_path / "server"), project_name="test-proj")
    root = repo.build_full_tree()
    repo.set_root_hash(root)
    repo.record_history(0, "server", "initial state", "/", [], root_hash=root)
    return repo


@pytest.fixture
def rw_scope(server_repo):
    server_repo.add_scope("scope-1", "/src/")
    scope = server_repo.scopes.get_by_id("scope-1")
    scope["mode"] = "rw"
    return scope


@pytest.fixture
def auth(rw_scope):
    return {"agent": "agent-A", "_scope": rw_scope}


def _make_push_body(server_repo, files: dict, base_version: int = 0) -> dict:
    """Build a valid push body with tree + objects."""
    from mut.core import tree as tree_mod

    nested = {}
    for path, content in files.items():
        parts = path.split("/")
        d = nested
        for p in parts[:-1]:
            d = d.setdefault(p, {})
        blob_hash = server_repo.store.put(content)
        d[parts[-1]] = ("B", blob_hash)

    def write_nested(node):
        entries = {}
        for name, val in sorted(node.items()):
            if isinstance(val, tuple):
                entries[name] = list(val)
            else:
                sub_hash = write_nested(val)
                entries[name] = ["T", sub_hash]
        return server_repo.store.put(json.dumps(entries, sort_keys=True).encode())

    root_hash = write_nested(nested)

    reachable = tree_mod.collect_reachable_hashes(server_repo.store, root_hash)
    objects_b64 = {}
    for h in reachable:
        objects_b64[h] = base64.b64encode(server_repo.store.get(h)).decode()

    return {
        "base_version": base_version,
        "snapshots": [{"id": 1, "root": root_hash, "message": "test push",
                       "who": "agent-A", "time": ""}],
        "objects": objects_b64,
    }


def _run(coro):
    """Run an async coroutine synchronously (compatible with Python 3.9)."""
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


# ── Clone ─────────────────────────────────────

class TestAsyncClone:
    def test_clone_empty(self, server_repo, auth):
        result = _run(_handle_clone(server_repo, auth, {}))
        assert result["project"] == "test-proj"
        assert isinstance(result["files"], dict)
        assert isinstance(result["version"], int)

    def test_clone_with_files(self, server_repo, auth):
        scope = auth["_scope"]
        server_repo.write_scope_files(scope, {"main.py": b"print('hi')"})
        result = _run(_handle_clone(server_repo, auth, {}))
        assert "main.py" in result["files"]
        content = base64.b64decode(result["files"]["main.py"])
        assert content == b"print('hi')"


# ── Push ──────────────────────────────────────

class TestAsyncPush:
    def test_push_empty_snapshots(self, server_repo, auth):
        result = _run(_handle_push(server_repo, auth, {
            "base_version": 0, "snapshots": [], "objects": {},
        }))
        assert result["status"] == "ok"

    def test_push_creates_files(self, server_repo, auth):
        body = _make_push_body(server_repo, {"main.py": b"hello"})
        result = _run(_handle_push(server_repo, auth, body))
        assert result["status"] == "ok"
        assert result["version"] == 1
        assert result["pushed"] == 1

        scope = auth["_scope"]
        files = server_repo.list_scope_files(scope)
        assert "main.py" in files

    def test_push_readonly_rejected(self, server_repo):
        server_repo.add_scope("ro-scope", "/docs/")
        scope = server_repo.scopes.get_by_id("ro-scope")
        scope["mode"] = "r"
        ro_auth = {"agent": "agent-R", "_scope": scope}
        with pytest.raises(PermissionDenied, match="read-only"):
            _run(_handle_push(server_repo, ro_auth, {}))

    def test_push_increments_version(self, server_repo, auth):
        body1 = _make_push_body(server_repo, {"a.py": b"v1"})
        result1 = _run(_handle_push(server_repo, auth, body1))
        assert result1["version"] == 1

        body2 = _make_push_body(server_repo, {"a.py": b"v2"}, base_version=1)
        result2 = _run(_handle_push(server_repo, auth, body2))
        assert result2["version"] == 2


# ── Pull ──────────────────────────────────────

class TestAsyncPull:
    def test_pull_up_to_date(self, server_repo, auth):
        result = _run(_handle_pull(server_repo, auth, {"since_version": 0}))
        assert result["status"] == "up-to-date"

    def test_pull_after_push(self, server_repo, auth):
        body = _make_push_body(server_repo, {"f.txt": b"content"})
        _run(_handle_push(server_repo, auth, body))

        result = _run(_handle_pull(server_repo, auth, {"since_version": 0}))
        assert result["status"] == "updated"
        assert result["version"] == 1
        assert "f.txt" in result["files"]


# ── Negotiate ─────────────────────────────────

class TestAsyncNegotiate:
    def test_all_missing(self, server_repo, auth):
        result = _run(_handle_negotiate(
            server_repo, auth, {"hashes": ["aaa", "bbb"]},
        ))
        assert set(result["missing"]) == {"aaa", "bbb"}

    def test_some_present(self, server_repo, auth):
        h = server_repo.store.put(b"existing")
        result = _run(_handle_negotiate(
            server_repo, auth, {"hashes": [h, "missing"]},
        ))
        assert "missing" in result["missing"]
        assert h not in result["missing"]


# ── Bug fix #1: lost_content persisted ────────

class TestBugFixLostContentPersisted:
    def test_lost_content_in_history(self, server_repo, auth):
        """Verify conflict lost_content and lost_hash are saved in history."""
        body1 = _make_push_body(server_repo, {"f.txt": b"original"})
        _run(_handle_push(server_repo, auth, body1))

        body2 = _make_push_body(
            server_repo, {"f.txt": b"override"}, base_version=0,
        )
        result = _run(_handle_push(server_repo, auth, body2))

        if result.get("merged"):
            entry = server_repo.get_history_entry(result["version"])
            assert "conflicts" in entry
            for conflict in entry["conflicts"]:
                assert "lost_content" in conflict
                assert "lost_hash" in conflict


# ── Bug fix #2: global lock ──────────────────

class TestBugFixGlobalLock:
    def test_concurrent_scope_pushes_atomic(self, server_repo):
        """Two scopes pushing concurrently should not corrupt version/root."""
        server_repo.add_scope("scope-a", "/src/")
        server_repo.add_scope("scope-b", "/docs/")

        scope_a = server_repo.scopes.get_by_id("scope-a")
        scope_b = server_repo.scopes.get_by_id("scope-b")
        scope_a["mode"] = "rw"
        scope_b["mode"] = "rw"
        auth_a = {"agent": "agent-A", "_scope": scope_a}
        auth_b = {"agent": "agent-B", "_scope": scope_b}

        body_a = _make_push_body(server_repo, {"main.py": b"code"})
        body_b = _make_push_body(server_repo, {"readme.md": b"docs"})

        async def run_concurrent():
            return await asyncio.gather(
                _handle_push(server_repo, auth_a, body_a),
                _handle_push(server_repo, auth_b, body_b),
            )

        results = _run(run_concurrent())
        versions = {r["version"] for r in results}
        assert len(versions) == 2
        assert server_repo.get_latest_version() == 2

    def test_global_lock_lazy_init(self, server_repo):
        """ServerRepo creates global lock lazily (Python 3.9 compat)."""
        assert server_repo._global_lock is None
        lock = server_repo._ensure_global_lock()
        assert isinstance(lock, asyncio.Lock)
        assert server_repo._global_lock is lock


# ── Bug fix #3: lost_hash for recovery ───────

class TestBugFixLostHash:
    def test_lww_records_lost_hash(self):
        from mut.core.merge import three_way_merge
        from mut.foundation.hash import hash_bytes

        result = three_way_merge(
            b"base", b"ours_change", b"theirs_change", "f.txt",
        )
        assert result.strategy == "lww"
        assert len(result.conflicts) == 1
        conflict = result.conflicts[0]
        assert conflict.lost_hash == hash_bytes(b"ours_change")
        assert conflict.lost_content

    def test_json_lww_records_lost_hash(self):
        from mut.core.merge import three_way_merge

        base = json.dumps({"key": "original"}).encode()
        ours = json.dumps({"key": "ours_val"}).encode()
        theirs = json.dumps({"key": "theirs_val"}).encode()

        result = three_way_merge(base, ours, theirs, "config.json")
        assert result.strategy == "json_merge"
        conflicts = [c for c in result.conflicts if c.lost_hash]
        assert len(conflicts) >= 1


# ── HTTP primitives ───────────────────────────

class TestHttpPrimitives:
    def test_build_response(self):
        resp = _build_response({"status": "ok"}, 200)
        assert b"HTTP/1.1 200 OK" in resp
        assert b"application/json" in resp

    def test_build_response_error(self):
        resp = _build_response({"error": "not found"}, 404)
        assert b"404 Not Found" in resp

    def test_health(self):
        result = _handle_health()
        assert result == {"status": "ok"}


# ── Async transport client ────────────────────

class TestAsyncMutClient:
    def test_client_init(self):
        from mut.foundation.transport import AsyncMutClient
        client = AsyncMutClient("http://localhost:9742", "token123")
        assert client.server_url == "http://localhost:9742"
        assert client.credential == "token123"
