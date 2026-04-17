"""Tests for the async server — handlers, connection handling, and bug fixes.

These tests create a real ServerRepo and call async handlers directly,
verifying the full async push/pull/clone cycle and the three bug fixes:
  #1: lost_content + lost_hash persisted in history and audit
  #2: Per-scope queue protects commit_id/scope state across concurrent pushes
  #3: lost_hash enables full content recovery
"""

import asyncio
import base64
import json
import pytest

from mut.server.repo import ServerRepo
from mut.server.server import _build_response, _handle_health
from tests._handlers_shim import (
    _handle_clone, _handle_push, _handle_pull,
    _handle_negotiate,
)
from mut.foundation.error import PermissionDenied


_SEED = "seedseedseedseed"


@pytest.fixture
def server_repo(tmp_path):
    repo = ServerRepo.init(str(tmp_path / "server"), project_name="test-proj")
    root = repo.build_full_tree()
    repo.set_root_hash(root)
    repo.record_history(_SEED, "server", "initial state", "/", [],
                        root_hash=root)
    repo.set_head_commit_id(_SEED)
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


def _make_push_body(server_repo, files: dict,
                    base_commit_id: str = "") -> dict:
    """Build a valid push body with tree + objects."""
    from mut.core import tree as tree_mod

    nested: dict = {}
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
        return server_repo.store.put(
            json.dumps(entries, sort_keys=True).encode()
        )

    root_hash = write_nested(nested)

    reachable = tree_mod.collect_reachable_hashes(
        server_repo.store, root_hash,
    )
    objects_b64 = {
        h: base64.b64encode(server_repo.store.get(h)).decode()
        for h in reachable
    }

    return {
        "base_commit_id": base_commit_id,
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
        assert "head_commit_id" in result

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
            "base_commit_id": "", "snapshots": [], "objects": {},
        }))
        assert result["status"] == "ok"

    def test_push_creates_files(self, server_repo, auth):
        body = _make_push_body(server_repo, {"main.py": b"hello"})
        result = _run(_handle_push(server_repo, auth, body))
        assert result["status"] == "ok"
        assert len(result["commit_id"]) == 16
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

    def test_push_returns_new_commit_id(self, server_repo, auth):
        body1 = _make_push_body(server_repo, {"a.py": b"v1"})
        result1 = _run(_handle_push(server_repo, auth, body1))
        cid1 = result1["commit_id"]

        body2 = _make_push_body(server_repo, {"a.py": b"v2"},
                                base_commit_id=cid1)
        result2 = _run(_handle_push(server_repo, auth, body2))
        cid2 = result2["commit_id"]

        assert cid1 != cid2
        assert len(cid1) == 16 and len(cid2) == 16


# ── Pull ──────────────────────────────────────

class TestAsyncPull:
    def test_pull_up_to_date(self, server_repo, auth):
        server_repo.history.set_scope_head_commit_id("/src/", _SEED)
        result = _run(_handle_pull(server_repo, auth,
                                   {"since_commit_id": _SEED}))
        assert result["status"] == "up-to-date"

    def test_pull_after_push(self, server_repo, auth):
        body = _make_push_body(server_repo, {"f.txt": b"content"})
        push_res = _run(_handle_push(server_repo, auth, body))

        result = _run(_handle_pull(server_repo, auth,
                                   {"since_commit_id": ""}))
        assert result["status"] == "updated"
        assert result["head_commit_id"] == push_res["commit_id"]
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
        """Verify conflict lost_content and lost_hash are saved in history.

        Push 1 and push 2 touch the same file with different content; the
        second push uses an empty base_commit_id to simulate a stale
        client. The server MUST trigger a three-way merge and persist
        lost_content + lost_hash for the overwritten version.
        """
        body1 = _make_push_body(server_repo, {"f.txt": b"original"})
        _run(_handle_push(server_repo, auth, body1))

        body2 = _make_push_body(
            server_repo, {"f.txt": b"override"}, base_commit_id="",
        )
        result = _run(_handle_push(server_repo, auth, body2))

        # Unconditional check — previously wrapped in `if result.get("merged"):`
        # which silently passed when the merge was (buggily) skipped.
        assert result.get("merged"), (
            "stale base_commit_id must trigger server-side merge"
        )
        entry = server_repo.get_history_entry(result["commit_id"])
        assert "conflicts" in entry and entry["conflicts"], \
            "merge must record at least one conflict entry"
        for conflict in entry["conflicts"]:
            assert "lost_content" in conflict
            assert "lost_hash" in conflict


class TestBugFixEmptyBasePreservesServerFiles:
    """Regression for Bug #1: empty base_commit_id must NOT delete
    server-only files. The buggy short-circuit in _resolve_conflicts
    was returning their_files verbatim, causing _apply_merged_files to
    drop every file not in the incoming push.
    """

    def test_empty_base_preserves_untouched_server_files(
        self, server_repo, auth,
    ):
        """Push A, then push B with empty base. A must still exist."""
        body1 = _make_push_body(server_repo, {"a.py": b"first"})
        _run(_handle_push(server_repo, auth, body1))

        body2 = _make_push_body(
            server_repo, {"b.py": b"second"}, base_commit_id="",
        )
        _run(_handle_push(server_repo, auth, body2))

        scope = auth["_scope"]
        files = server_repo.list_scope_files(scope)
        assert "a.py" in files, "server-only file was silently deleted"
        assert "b.py" in files, "pushed file must be persisted"
        assert files["a.py"] == b"first"
        assert files["b.py"] == b"second"

    def test_stale_base_preserves_concurrent_server_additions(
        self, server_repo, auth,
    ):
        """Known-but-stale base. Realistic clients always push a FULL
        workdir tree; the test mirrors that by keeping pre-existing files
        in every subsequent push body. The 3-way merge must preserve
        files that a concurrent pusher added in between.
        """
        body1 = _make_push_body(server_repo, {"a.py": b"first"})
        r1 = _run(_handle_push(server_repo, auth, body1))
        cid1 = r1["commit_id"]

        # Agent B rebuilt its workdir and also pushed a new file b.py.
        body2 = _make_push_body(
            server_repo,
            {"a.py": b"first", "b.py": b"second"},
            base_commit_id=cid1,
        )
        _run(_handle_push(server_repo, auth, body2))

        # Agent A pushes with a stale base (hasn't seen B's b.py yet),
        # adding c.py alongside the unchanged a.py.
        body3 = _make_push_body(
            server_repo,
            {"a.py": b"first", "c.py": b"third"},
            base_commit_id=cid1,
        )
        _run(_handle_push(server_repo, auth, body3))

        scope = auth["_scope"]
        files = server_repo.list_scope_files(scope)
        assert set(files.keys()) == {"a.py", "b.py", "c.py"}
        assert files["b.py"] == b"second", (
            "B's file must survive A's stale push via 3-way merge"
        )
        assert files["c.py"] == b"third"


# ── Bug fix: clone returns scope-level head ───

class TestBugFixCloneReturnsScopeHead:
    """Regression for Bug #2: clone must return scope_head_commit_id,
    NOT the global head. Otherwise the client stores a cross-scope
    commit in REMOTE_HEAD and /pull, /push misbehave."""

    def test_clone_returns_scope_head_not_global(self, server_repo):
        # Seed two scopes; push to /docs/ so the global head points there.
        server_repo.add_scope("scope-src", "/src/")
        server_repo.add_scope("scope-docs", "/docs/")
        src_scope = server_repo.scopes.get_by_id("scope-src")
        docs_scope = server_repo.scopes.get_by_id("scope-docs")
        src_scope["mode"] = "rw"
        docs_scope["mode"] = "rw"

        # Push to /docs/ — this advances the GLOBAL head to docs's commit.
        body = _make_push_body(server_repo, {"readme.md": b"hi"})
        docs_res = _run(_handle_push(
            server_repo, {"agent": "a", "_scope": docs_scope}, body,
        ))
        docs_cid = docs_res["commit_id"]

        # Clone /src/ — should return /src/'s scope head (empty),
        # not the global head (= docs_cid).
        src_clone = _run(_handle_clone(
            server_repo, {"agent": "b", "_scope": src_scope}, {},
        ))
        assert src_clone["head_commit_id"] != docs_cid
        assert src_clone["head_commit_id"] == \
            server_repo.history.get_scope_head_commit_id("/src/")


# ── Bug fix #2: per-scope serialization ──────

class TestBugFixPerScopeSerialization:
    def test_concurrent_scope_pushes_atomic(self, server_repo):
        """Two scopes pushing concurrently should each land a unique commit."""
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
        commit_ids = {r["commit_id"] for r in results}
        assert len(commit_ids) == 2  # two distinct commits


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
