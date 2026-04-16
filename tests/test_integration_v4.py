"""Integration tests for v4 features (commit-id identity)."""

import asyncio
import base64
import json
import pytest

from mut.server.repo import ServerRepo
from mut.server.auth.api_key import ApiKeyAuth
from mut.server.server import (
    _handle_clone, _handle_push, _handle_pull, _handle_rollback,
)
from mut.foundation.error import PermissionDenied, AuthenticationError


_SEED = "seedseedseedseed"


def _run(coro):
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


@pytest.fixture
def server_repo(tmp_path):
    repo = ServerRepo.init(str(tmp_path / "server"),
                           project_name="integration-test")
    root = repo.build_full_tree()
    repo.set_root_hash(root)
    repo.record_history(_SEED, "server", "initial state", "/", [],
                        root_hash=root)
    repo.set_head_commit_id(_SEED)
    return repo


@pytest.fixture
def api_auth(server_repo, tmp_path):
    """ApiKeyAuth with multiple scopes configured."""
    server_repo.add_scope("scope-docs", "/docs/")
    server_repo.add_scope("scope-src", "/src/")
    server_repo.add_scope("scope-all", "/")
    return ApiKeyAuth(server_repo.scopes, tmp_path / "creds.json")


def _make_push_body(repo, files: dict, base_commit_id: str = "") -> dict:
    from mut.core import tree as tree_mod

    nested: dict = {}
    for path, content in files.items():
        parts = path.split("/")
        d = nested
        for p in parts[:-1]:
            d = d.setdefault(p, {})
        blob_hash = repo.store.put(content)
        d[parts[-1]] = ("B", blob_hash)

    def write_nested(node):
        entries = {}
        for name, val in sorted(node.items()):
            if isinstance(val, tuple):
                entries[name] = list(val)
            else:
                sub_hash = write_nested(val)
                entries[name] = ["T", sub_hash]
        return repo.store.put(json.dumps(entries, sort_keys=True).encode())

    root_hash = write_nested(nested)
    reachable = tree_mod.collect_reachable_hashes(repo.store, root_hash)
    objects_b64 = {h: base64.b64encode(repo.store.get(h)).decode()
                   for h in reachable}
    return {
        "base_commit_id": base_commit_id,
        "snapshots": [{"id": 1, "root": root_hash, "message": "test",
                       "who": "test", "time": ""}],
        "objects": objects_b64,
    }


def _auth_context(api_auth, key):
    return _run(api_auth.authenticate({"authorization": f"Bearer {key}"}, {}))


# ── Cross-Scope Auth Rejection ─────────────────────────────────

class TestCrossScopeRejection:
    def test_docs_key_cannot_push_to_excluded_path(self, server_repo, api_auth):
        """A client with docs scope + exclude cannot push into excluded subdir."""
        key_docs = api_auth.issue("alice", "scope-docs", "rw")
        _auth_context(api_auth, key_docs)

        server_repo.scopes.delete("scope-docs")
        server_repo.add_scope("scope-docs", "/docs/",
                              exclude=["/docs/secret/"])
        scope = server_repo.scopes.get_by_id("scope-docs")
        scope["mode"] = "rw"
        auth_with_exclude = {"agent": "alice", "_scope": scope}

        body = _make_push_body(server_repo,
                               {"secret/classified.txt": b"top secret"})
        with pytest.raises(PermissionDenied, match="paths outside scope"):
            _run(_handle_push(server_repo, auth_with_exclude, body))

    def test_docs_key_can_push_to_docs(self, server_repo, api_auth):
        key_docs = api_auth.issue("alice", "scope-docs", "rw")
        auth = _auth_context(api_auth, key_docs)

        body = _make_push_body(server_repo, {"readme.md": b"# Docs"})
        result = _run(_handle_push(server_repo, auth, body))
        assert result["status"] == "ok"

    def test_readonly_key_cannot_push(self, server_repo, api_auth):
        key_ro = api_auth.issue("reader", "scope-docs", "r")
        auth = _auth_context(api_auth, key_ro)

        body = _make_push_body(server_repo, {"readme.md": b"# Hello"})
        with pytest.raises(PermissionDenied, match="read-only"):
            _run(_handle_push(server_repo, auth, body))

    def test_readonly_key_can_clone(self, server_repo, api_auth):
        key_ro = api_auth.issue("reader", "scope-docs", "r")
        auth = _auth_context(api_auth, key_ro)

        result = _run(_handle_clone(server_repo, auth, {}))
        assert result["project"] == "integration-test"

    def test_root_scope_can_access_everything(self, server_repo, api_auth):
        key_root = api_auth.issue("admin", "scope-all", "rw")
        auth = _auth_context(api_auth, key_root)

        body = _make_push_body(server_repo, {"anything.txt": b"data"})
        result = _run(_handle_push(server_repo, auth, body))
        assert result["status"] == "ok"

    def test_revoked_key_rejected_completely(self, server_repo, api_auth):
        key = api_auth.issue("alice", "scope-docs", "rw")
        api_auth.revoke(key)

        with pytest.raises(AuthenticationError, match="revoked"):
            _auth_context(api_auth, key)


# ── Concurrent Push Serialization ──────────────────────────────

class TestConcurrentPush:
    def test_same_scope_serial(self, server_repo, api_auth):
        """Two pushes to the same scope are serialized — no data loss."""
        key = api_auth.issue("alice", "scope-docs", "rw")
        auth = _auth_context(api_auth, key)

        async def push_file(name, content, base):
            body = _make_push_body(server_repo, {name: content}, base)
            return await _handle_push(server_repo, auth, body)

        # Both pushes share the same base commit (_SEED). After r1 commits,
        # r2's base diverges from head → server triggers three-way merge
        # so neither file is lost.
        async def go():
            r1, r2 = await asyncio.gather(
                push_file("a.md", b"file-a", _SEED),
                push_file("b.md", b"file-b", _SEED),
            )
            return r1, r2

        r1, r2 = _run(go())
        assert r1["status"] == "ok"
        assert r2["status"] == "ok"

        assert r1["commit_id"] != r2["commit_id"]

        scope = auth["_scope"]
        files = server_repo.list_scope_files(scope)
        assert "a.md" in files
        assert "b.md" in files

    def test_different_scope_parallel(self, server_repo, api_auth):
        key_docs = api_auth.issue("alice", "scope-docs", "rw")
        key_src = api_auth.issue("bob", "scope-src", "rw")
        auth_docs = _auth_context(api_auth, key_docs)
        auth_src = _auth_context(api_auth, key_src)

        async def push_docs():
            body = _make_push_body(server_repo, {"readme.md": b"# Docs"})
            return await _handle_push(server_repo, auth_docs, body)

        async def push_src():
            body = _make_push_body(server_repo, {"main.py": b"print(1)"})
            return await _handle_push(server_repo, auth_src, body)

        async def go():
            return await asyncio.gather(push_docs(), push_src())

        r_docs, r_src = _run(go())
        assert r_docs["status"] == "ok"
        assert r_src["status"] == "ok"
        assert r_docs["commit_id"] != r_src["commit_id"]

    def test_three_way_merge_on_concurrent_same_scope(self, server_repo,
                                                       api_auth):
        """Concurrent pushes trigger server-side three-way merge."""
        key = api_auth.issue("alice", "scope-docs", "rw")
        auth = _auth_context(api_auth, key)

        # Both pushes share the same base commit (_SEED). After r1 commits,
        # r2's base diverges from head → three-way merge kicks in.
        body_a = _make_push_body(server_repo, {"a.txt": b"aaa"}, _SEED)
        body_b = _make_push_body(server_repo, {"b.txt": b"bbb"}, _SEED)

        async def go():
            r1 = await _handle_push(server_repo, auth, body_a)
            r2 = await _handle_push(server_repo, auth, body_b)
            return r1, r2

        r1, r2 = _run(go())

        assert r1["commit_id"] != r2["commit_id"]

        scope = auth["_scope"]
        files = server_repo.list_scope_files(scope)
        assert "a.txt" in files
        assert "b.txt" in files


# ── Full Workflow: Push → Rollback → Verify ────────────────────

class TestFullWorkflow:
    def test_push_rollback_verify(self, server_repo, api_auth):
        """Full cycle: push v1, push v2, rollback to v1, verify content."""
        key = api_auth.issue("alice", "scope-docs", "rw")
        auth = _auth_context(api_auth, key)

        body1 = _make_push_body(server_repo, {"doc.md": b"# Version 1"})
        r1 = _run(_handle_push(server_repo, auth, body1))
        cid1 = r1["commit_id"]

        body2 = _make_push_body(server_repo, {"doc.md": b"# Version 2"},
                                base_commit_id=cid1)
        r2 = _run(_handle_push(server_repo, auth, body2))
        cid2 = r2["commit_id"]
        assert cid1 != cid2

        rb = _run(_handle_rollback(server_repo, auth,
                                   {"target_commit_id": cid1}))
        assert rb["status"] == "rolled-back"
        assert rb["new_commit_id"] not in (cid1, cid2)

        clone = _run(_handle_clone(server_repo, auth, {}))
        content = base64.b64decode(clone["files"]["doc.md"])
        assert content == b"# Version 1"
        assert clone["head_commit_id"] == rb["new_commit_id"]

    def test_push_after_rollback(self, server_repo, api_auth):
        """Can push normally after a rollback."""
        key = api_auth.issue("alice", "scope-docs", "rw")
        auth = _auth_context(api_auth, key)

        body1 = _make_push_body(server_repo, {"a.txt": b"v1"})
        r1 = _run(_handle_push(server_repo, auth, body1))

        body2 = _make_push_body(server_repo, {"a.txt": b"v2"},
                                base_commit_id=r1["commit_id"])
        r2 = _run(_handle_push(server_repo, auth, body2))

        rb = _run(_handle_rollback(server_repo, auth,
                                   {"target_commit_id": r1["commit_id"]}))

        body4 = _make_push_body(server_repo, {"a.txt": b"v4-new"},
                                base_commit_id=rb["new_commit_id"])
        r4 = _run(_handle_push(server_repo, auth, body4))
        assert r4["commit_id"] not in (
            r1["commit_id"], r2["commit_id"], rb["new_commit_id"],
        )

        scope = auth["_scope"]
        files = server_repo.list_scope_files(scope)
        assert files["a.txt"] == b"v4-new"


# ── Pull with scope isolation ──────────────────────────────────

class TestPullScopeIsolation:
    def test_pull_only_sees_own_scope(self, server_repo, api_auth):
        """Pull returns only files in the authenticated scope."""
        key_docs = api_auth.issue("alice", "scope-docs", "rw")
        key_src = api_auth.issue("bob", "scope-src", "rw")
        auth_docs = _auth_context(api_auth, key_docs)
        auth_src = _auth_context(api_auth, key_src)

        body_docs = _make_push_body(server_repo, {"readme.md": b"# Docs"})
        _run(_handle_push(server_repo, auth_docs, body_docs))

        body_src = _make_push_body(server_repo, {"main.py": b"print(1)"})
        _run(_handle_push(server_repo, auth_src, body_src))

        pull_result = _run(_handle_pull(server_repo, auth_docs,
                                        {"since_commit_id": ""}))
        assert "readme.md" in pull_result["files"]
        assert "main.py" not in pull_result["files"]

        pull_result2 = _run(_handle_pull(server_repo, auth_src,
                                         {"since_commit_id": ""}))
        assert "main.py" in pull_result2["files"]
        assert "readme.md" not in pull_result2["files"]
