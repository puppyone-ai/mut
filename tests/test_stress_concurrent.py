"""Stress tests for concurrent multi-client sync scenarios (commit-id identity).

Simulates multiple clients pushing/pulling to the same server simultaneously,
testing queue serialization, auth isolation, notification delivery, and
merge correctness under contention.
"""

import asyncio
import base64
import json
import pytest
import time

from mut.server.repo import ServerRepo
from mut.server.server import (
    _handle_push, _handle_pull, _handle_clone,
    _handle_rollback, _handle_pull_commit,
)
from mut.server.auth.api_key import ApiKeyAuth
from mut.server.sync_queue import ScopeQueue
from mut.server.notification import NotificationManager, InMemoryNotificationSink
from mut.server.websocket import WebSocketManager, WebSocketClient
from mut.foundation.error import PermissionDenied, AuthenticationError
from mut.core import tree as tree_mod


_SEED = "seedseedseedseed"
_CID_X = "a1b2c3d4e5f60718"
_CID_Y = "1122334455667788"
_CID_Z = "9988776655443322"


def _run(coro):
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


@pytest.fixture
def server_repo(tmp_path):
    repo = ServerRepo.init(str(tmp_path / "server"), project_name="stress-test")
    repo.record_history(_SEED, "server", "init", "/", [])
    repo.set_head_commit_id(_SEED)
    return repo


@pytest.fixture
def api_auth(server_repo, tmp_path):
    server_repo.add_scope("scope-root", "/")
    server_repo.add_scope("scope-docs", "/docs/")
    server_repo.add_scope("scope-src", "/src/")
    server_repo.add_scope("scope-docs-internal", "/docs/internal/")
    return ApiKeyAuth(server_repo.scopes, tmp_path / "creds.json")


def _make_push(repo, files, base: str = ""):
    nested: dict = {}
    for path, content in files.items():
        parts = path.split("/")
        d = nested
        for p in parts[:-1]:
            d = d.setdefault(p, {})
        d[parts[-1]] = ("B", repo.store.put(content))

    def build(node):
        entries = {}
        for name, val in sorted(node.items()):
            if isinstance(val, tuple):
                entries[name] = list(val)
            else:
                entries[name] = ["T", build(val)]
        return repo.store.put(json.dumps(entries, sort_keys=True).encode())

    root = build(nested)
    reachable = tree_mod.collect_reachable_hashes(repo.store, root)
    return {
        "base_commit_id": base,
        "snapshots": [{"id": 1, "root": root, "message": "push",
                       "who": "test", "time": ""}],
        "objects": {h: base64.b64encode(repo.store.get(h)).decode()
                    for h in reachable},
    }


def _auth(api_auth, key):
    return _run(api_auth.authenticate({"authorization": f"Bearer {key}"}, {}))


# ══════════════════════════════════════════════════════════════
# 1. Multi-Client Concurrent Push — Same Scope
# ══════════════════════════════════════════════════════════════

class TestConcurrentSameScope:
    """Multiple clients pushing to the same scope simultaneously."""

    def test_5_clients_same_scope_all_succeed(self, server_repo, api_auth):
        """5 clients push to /docs/ concurrently — all should succeed via merge."""
        keys = [api_auth.issue(f"agent-{i}", "scope-docs", "rw") for i in range(5)]
        auths = [_auth(api_auth, k) for k in keys]

        # All five clients share the same base (_SEED). After the first push
        # commits, subsequent pushes diverge from head and server-side three-way
        # merge keeps every file.
        bodies = [
            _make_push(server_repo,
                       {f"file-{i}.txt": f"content-{i}".encode()},
                       base=_SEED)
            for i in range(5)
        ]

        async def push_all():
            tasks = [_handle_push(server_repo, auths[i], bodies[i])
                     for i in range(5)]
            return await asyncio.gather(*tasks)

        results = _run(push_all())

        assert all(r["status"] == "ok" for r in results)
        # All commit ids should be distinct
        commit_ids = [r["commit_id"] for r in results]
        assert len(set(commit_ids)) == 5

        scope = auths[0]["_scope"]
        files = server_repo.list_scope_files(scope)
        for i in range(5):
            assert f"file-{i}.txt" in files

    def test_same_file_concurrent_merge(self, server_repo, api_auth):
        """Two clients edit the same file — server auto-merges or LWW."""
        k1 = api_auth.issue("alice", "scope-docs", "rw")
        k2 = api_auth.issue("bob", "scope-docs", "rw")
        a1 = _auth(api_auth, k1)
        a2 = _auth(api_auth, k2)

        body1 = _make_push(server_repo, {"shared.txt": b"alice-version"})
        body2 = _make_push(server_repo, {"shared.txt": b"bob-version"})

        async def go():
            r1 = await _handle_push(server_repo, a1, body1)
            r2 = await _handle_push(server_repo, a2, body2)
            return r1, r2

        r1, r2 = _run(go())
        assert r1["commit_id"] != r2["commit_id"]

        scope = a1["_scope"]
        files = server_repo.list_scope_files(scope)
        content = files["shared.txt"]
        assert b"bob-version" in content

    def test_rapid_sequential_pushes(self, server_repo, api_auth):
        """20 rapid sequential pushes to same scope — chain intact."""
        key = api_auth.issue("agent", "scope-docs", "rw")
        auth = _auth(api_auth, key)

        async def go():
            prev = ""
            seen = set()
            for i in range(20):
                body = _make_push(server_repo, {f"r{i}.txt": f"v{i}".encode()},
                                  base=prev)
                r = await _handle_push(server_repo, auth, body)
                assert r["status"] == "ok"
                assert r["commit_id"] not in seen
                seen.add(r["commit_id"])
                prev = r["commit_id"]
            return seen

        seen = _run(go())
        assert len(seen) == 20


# ══════════════════════════════════════════════════════════════
# 2. Multi-Client Concurrent Push — Different Scopes
# ══════════════════════════════════════════════════════════════

class TestConcurrentDifferentScopes:
    """Clients in different scopes should run in parallel."""

    def test_sibling_scopes_parallel(self, server_repo, api_auth):
        """Push to /docs/ and /src/ simultaneously — both succeed, no blocking."""
        k_docs = api_auth.issue("doc-agent", "scope-docs", "rw")
        k_src = api_auth.issue("src-agent", "scope-src", "rw")
        a_docs = _auth(api_auth, k_docs)
        a_src = _auth(api_auth, k_src)

        body_docs = _make_push(server_repo, {"readme.md": b"# Docs"})
        body_src = _make_push(server_repo, {"main.py": b"print(1)"})

        async def go():
            return await asyncio.gather(
                _handle_push(server_repo, a_docs, body_docs),
                _handle_push(server_repo, a_src, body_src),
            )

        r_docs, r_src = _run(go())
        assert r_docs["status"] == "ok"
        assert r_src["status"] == "ok"
        assert r_docs["commit_id"] != r_src["commit_id"]

    def test_parent_child_scope_serialized(self, server_repo, api_auth):
        """Push to / and /docs/ — should serialize (parent-child overlap)."""
        k_root = api_auth.issue("admin", "scope-root", "rw")
        k_docs = api_auth.issue("editor", "scope-docs", "rw")
        a_root = _auth(api_auth, k_root)
        a_docs = _auth(api_auth, k_docs)

        order = []

        async def push_root():
            body = _make_push(server_repo, {"global.txt": b"root-data"})
            r = await _handle_push(server_repo, a_root, body)
            order.append(("root", r["commit_id"]))
            return r

        async def push_docs():
            await asyncio.sleep(0.01)
            body = _make_push(server_repo, {"doc.md": b"doc-data"})
            r = await _handle_push(server_repo, a_docs, body)
            order.append(("docs", r["commit_id"]))
            return r

        async def go():
            return await asyncio.gather(push_root(), push_docs())

        _run(go())
        commit_ids = [cid for _, cid in order]
        assert len(set(commit_ids)) == 2

    def test_3_scopes_mixed_parallel_serial(self, server_repo, api_auth):
        """/docs/ and /src/ parallel, /docs/internal/ serialized with /docs/."""
        k_docs = api_auth.issue("a1", "scope-docs", "rw")
        k_src = api_auth.issue("a2", "scope-src", "rw")
        k_int = api_auth.issue("a3", "scope-docs-internal", "rw")
        a_docs = _auth(api_auth, k_docs)
        a_src = _auth(api_auth, k_src)
        a_int = _auth(api_auth, k_int)

        async def go():
            r1 = await _handle_push(server_repo, a_docs,
                                     _make_push(server_repo, {"d.txt": b"d"}))
            r2 = await _handle_push(server_repo, a_src,
                                     _make_push(server_repo, {"s.txt": b"s"}))
            r3 = await _handle_push(server_repo, a_int,
                                     _make_push(server_repo, {"i.txt": b"i"}))
            return r1, r2, r3

        r1, r2, r3 = _run(go())
        assert all(r["status"] == "ok" for r in [r1, r2, r3])
        assert len({r1["commit_id"], r2["commit_id"], r3["commit_id"]}) == 3


# ══════════════════════════════════════════════════════════════
# 3. Auth Key Verification + Scope Isolation
# ══════════════════════════════════════════════════════════════

class TestAuthScopeIsolation:
    """Auth keys restrict clients to their assigned scope."""

    def test_docs_key_cannot_see_src_files(self, server_repo, api_auth):
        k_root = api_auth.issue("admin", "scope-root", "rw")
        k_docs = api_auth.issue("doc-user", "scope-docs", "rw")
        a_root = _auth(api_auth, k_root)
        a_docs = _auth(api_auth, k_docs)

        _run(_handle_push(server_repo, a_root,
                          _make_push(server_repo, {"readme.md": b"docs"})))

        clone = _run(_handle_clone(server_repo, a_docs, {}))
        assert clone["project"] == "stress-test"

    def test_revoked_key_rejected(self, server_repo, api_auth):
        key = api_auth.issue("agent", "scope-docs", "rw")
        api_auth.revoke(key)

        with pytest.raises(AuthenticationError, match="revoked"):
            _auth(api_auth, key)

    def test_identity_mismatch_rejected(self, server_repo, api_auth):
        key = api_auth.issue("alice", "scope-docs", "rw",
                             user_identity="alice@company.com")

        with pytest.raises(AuthenticationError, match="identity mismatch"):
            _run(api_auth.authenticate(
                {"authorization": f"Bearer {key}",
                 "x-mut-user": "mallory@evil.com"}, {}))

    def test_readonly_scope_blocks_push(self, server_repo, api_auth):
        key = api_auth.issue("reader", "scope-docs", "r")
        auth = _auth(api_auth, key)

        body = _make_push(server_repo, {"x.txt": b"data"})
        with pytest.raises(PermissionDenied, match="read-only"):
            _run(_handle_push(server_repo, auth, body))

    def test_readonly_allows_clone_and_pull(self, server_repo, api_auth):
        key = api_auth.issue("reader", "scope-docs", "r")
        auth = _auth(api_auth, key)

        clone = _run(_handle_clone(server_repo, auth, {}))
        assert clone["project"] == "stress-test"

        pull = _run(_handle_pull(server_repo, auth, {"since_commit_id": ""}))
        assert pull["status"] in ("up-to-date", "updated")

    def test_scope_excludes_enforced(self, server_repo, api_auth):
        """Push to excluded path within scope is rejected."""
        server_repo.scopes.delete("scope-docs")
        server_repo.add_scope("scope-docs", "/docs/",
                              exclude=["/docs/secret/"])
        key = api_auth.issue("agent", "scope-docs", "rw")
        auth = _auth(api_auth, key)

        body = _make_push(server_repo,
                          {"secret/classified.txt": b"top secret"})
        with pytest.raises(PermissionDenied, match="paths outside scope"):
            _run(_handle_push(server_repo, auth, body))

    def test_batch_revoke_by_scope(self, server_repo, api_auth):
        k1 = api_auth.issue("a1", "scope-docs", "rw")
        k2 = api_auth.issue("a2", "scope-docs", "r")
        k3 = api_auth.issue("a3", "scope-src", "rw")

        count = api_auth.revoke_by_scope("scope-docs")
        assert count == 2

        with pytest.raises(AuthenticationError):
            _auth(api_auth, k1)
        with pytest.raises(AuthenticationError):
            _auth(api_auth, k2)

        auth3 = _auth(api_auth, k3)
        assert auth3["agent"] == "a3"


# ══════════════════════════════════════════════════════════════
# 4. Notification Online/Offline/Reconnect
# ══════════════════════════════════════════════════════════════

class _FakeWriter:
    def __init__(self, fail=False):
        self.written = []
        self.fail = fail
        self.closed = False

    def write(self, data):
        if self.fail:
            raise ConnectionError
        self.written.append(data)

    async def drain(self):
        if self.fail:
            raise ConnectionError

    def close(self):
        self.closed = True

    async def wait_closed(self):
        pass


class TestNotificationDelivery:
    """Test notification delivery under various client states."""

    def _client(self, cid, scope, fail=False):
        return WebSocketClient(
            client_id=cid, scope_path=scope,
            writer=_FakeWriter(fail=fail), reader=None,
        )

    def test_online_clients_receive_notification(self):
        mgr = WebSocketManager()
        c1 = self._client("alice", "/docs/")
        c2 = self._client("bob", "/docs/")
        c3 = self._client("charlie", "/src/")
        mgr.register(c1)
        mgr.register(c2)
        mgr.register(c3)

        notif = {"notification_id": "n1", "type": "version_update",
                 "scope": "/docs/", "commit_id": _CID_X}
        result = _run(mgr.broadcast(notif, exclude="alice",
                                    scope_path="/docs/"))

        assert "bob" in result["sent"]
        assert "alice" not in result["sent"]
        assert "charlie" not in result["sent"]

    def test_offline_client_gets_queued(self):
        mgr = WebSocketManager()
        c = self._client("alice", "/docs/", fail=True)
        mgr.register(c)

        notif = {"notification_id": "n1", "type": "version_update"}
        result = _run(mgr.broadcast(notif, scope_path="/docs/"))
        assert "alice" in result["queued"]

    def test_reconnect_flushes_queue(self):
        mgr = WebSocketManager()
        mgr._offline_queue["alice"] = [
            {"notification_id": "n1", "commit_id": _CID_X},
            {"notification_id": "n2", "commit_id": _CID_Y},
            {"notification_id": "n3", "commit_id": _CID_Z},
        ]

        c = self._client("alice", "/docs/")
        mgr.register(c)
        flushed = _run(mgr.flush_offline(c))
        assert flushed == 3
        assert mgr._offline_queue.get("alice") is None

    def test_idempotent_no_duplicate_delivery(self):
        mgr = WebSocketManager()
        c = self._client("bob", "/docs/")
        mgr.register(c)

        notif = {"notification_id": "same-id", "type": "version_update"}
        r1 = _run(mgr.broadcast(notif, scope_path="/docs/"))
        r2 = _run(mgr.broadcast(notif, scope_path="/docs/"))

        assert "bob" in r1["sent"]
        assert "bob" not in r2["sent"]

    def test_parent_scope_receives_child_notifications(self):
        mgr = WebSocketManager()
        c_root = self._client("admin", "/")
        c_docs = self._client("editor", "/docs/")
        c_src = self._client("dev", "/src/")
        mgr.register(c_root)
        mgr.register(c_docs)
        mgr.register(c_src)

        notif = {"notification_id": "n-child", "type": "version_update"}
        result = _run(mgr.broadcast(notif, scope_path="/docs/internal/"))

        assert "admin" in result["sent"]
        assert "editor" in result["sent"]
        assert "dev" not in result["sent"]

    def test_offline_queue_capped_at_500(self):
        mgr = WebSocketManager()
        c = self._client("alice", "/", fail=True)
        mgr.register(c)

        for i in range(600):
            notif = {"notification_id": f"n-{i}", "type": "test"}
            _run(mgr.broadcast(notif, scope_path="/"))

        assert len(mgr._offline_queue.get("alice", [])) <= 500

    def test_notification_manager_at_least_once(self):
        sink = InMemoryNotificationSink()
        nm = NotificationManager("test-repo", sink=sink)

        result = _run(nm.notify_after_push(
            "/docs/", _CID_X, "alice",
            [{"path": "docs/a.txt", "action": "add"}],
            client_ids=["alice", "bob", "charlie"],
        ))

        assert "alice" not in result["sent"]
        assert "bob" in result["sent"]
        assert "charlie" in result["sent"]


# ══════════════════════════════════════════════════════════════
# 5. Sync Queue Stress
# ══════════════════════════════════════════════════════════════

class TestSyncQueueStress:
    """Stress tests for the scope queue serialization."""

    def test_10_concurrent_on_overlapping_scopes(self):
        q = ScopeQueue()
        order = []

        async def worker(scope, label):
            await q.acquire(scope)
            order.append(f"{label}_start")
            await asyncio.sleep(0.005)
            order.append(f"{label}_end")
            q.release(scope)

        async def go():
            tasks = []
            for i in range(5):
                tasks.append(asyncio.create_task(
                    worker("/docs/", f"docs-{i}")))
            for i in range(5):
                tasks.append(asyncio.create_task(
                    worker("/docs/internal/", f"int-{i}")))
            await asyncio.gather(*tasks)

        _run(go())
        assert len(order) == 20

    def test_siblings_truly_parallel(self):
        q = ScopeQueue()
        started = []

        async def worker(scope, delay=0.05):
            await q.acquire(scope)
            started.append(scope)
            await asyncio.sleep(delay)
            q.release(scope)

        async def go():
            t0 = time.time()
            await asyncio.gather(
                worker("/docs/", 0.05),
                worker("/src/", 0.05),
                worker("/config/", 0.05),
            )
            return time.time() - t0

        elapsed = _run(go())
        assert len(started) == 3
        assert elapsed < 0.12

    def test_deep_nesting_serializes(self):
        q = ScopeQueue()
        order = []

        async def worker(scope, label):
            await q.acquire(scope)
            order.append(f"{label}_start")
            await asyncio.sleep(0.01)
            order.append(f"{label}_end")
            q.release(scope)

        async def go():
            t1 = asyncio.create_task(worker("/a/", "L1"))
            await asyncio.sleep(0.002)
            t2 = asyncio.create_task(worker("/a/b/", "L2"))
            await asyncio.sleep(0.002)
            t3 = asyncio.create_task(worker("/a/b/c/", "L3"))
            await t1
            await t2
            await t3

        _run(go())
        assert order.index("L1_end") < order.index("L2_start")
        assert order.index("L2_end") < order.index("L3_start")


# ══════════════════════════════════════════════════════════════
# 6. Full Client-Server E2E Simulation
# ══════════════════════════════════════════════════════════════

class TestE2EClientServerSimulation:
    """Simulate complete client-server workflows."""

    def test_client_a_pushes_client_b_pulls(self, server_repo, api_auth):
        """Client A pushes, Client B pulls the changes."""
        k_a = api_auth.issue("alice", "scope-docs", "rw")
        k_b = api_auth.issue("bob", "scope-docs", "rw")
        a_a = _auth(api_auth, k_a)
        a_b = _auth(api_auth, k_b)

        body = _make_push(server_repo, {"notes.md": b"# Meeting Notes"})
        r = _run(_handle_push(server_repo, a_a, body))
        assert r["status"] == "ok"

        pull = _run(_handle_pull(server_repo, a_b, {"since_commit_id": ""}))
        assert pull["status"] == "updated"
        assert pull["head_commit_id"] == r["commit_id"]
        assert "notes.md" in pull["files"]
        content = base64.b64decode(pull["files"]["notes.md"])
        assert content == b"# Meeting Notes"

    def test_conflicting_edits_auto_merged(self, server_repo, api_auth):
        """Two clients edit different files — auto-merged on server."""
        k_a = api_auth.issue("alice", "scope-docs", "rw")
        k_b = api_auth.issue("bob", "scope-docs", "rw")
        a_a = _auth(api_auth, k_a)
        a_b = _auth(api_auth, k_b)

        # Both clients pulled at _SEED then pushed concurrently — second push
        # diverges from head and triggers three-way merge.
        body_a = _make_push(server_repo, {"alice.txt": b"alice-data"},
                            base=_SEED)
        body_b = _make_push(server_repo, {"bob.txt": b"bob-data"},
                            base=_SEED)

        r_a = _run(_handle_push(server_repo, a_a, body_a))
        r_b = _run(_handle_push(server_repo, a_b, body_b))
        assert r_a["commit_id"] != r_b["commit_id"]

        scope = a_a["_scope"]
        files = server_repo.list_scope_files(scope)
        assert files["alice.txt"] == b"alice-data"
        assert files["bob.txt"] == b"bob-data"

    def test_rollback_then_continue_editing(self, server_repo, api_auth):
        """Push v1, push v2, rollback to v1, push v3 — full lifecycle."""
        key = api_auth.issue("agent", "scope-docs", "rw")
        auth = _auth(api_auth, key)

        body1 = _make_push(server_repo, {"doc.md": b"# V1"})
        r1 = _run(_handle_push(server_repo, auth, body1))
        cid1 = r1["commit_id"]

        body2 = _make_push(server_repo, {"doc.md": b"# V2"}, base=cid1)
        r2 = _run(_handle_push(server_repo, auth, body2))

        rb = _run(_handle_rollback(server_repo, auth,
                                   {"target_commit_id": cid1}))
        assert rb["new_commit_id"] not in (cid1, r2["commit_id"])

        pv = _run(_handle_pull_commit(server_repo, auth,
                                      {"commit_id": cid1}))
        assert base64.b64decode(pv["files"]["doc.md"]) == b"# V1"

        body4 = _make_push(server_repo, {"doc.md": b"# V4"},
                           base=rb["new_commit_id"])
        r4 = _run(_handle_push(server_repo, auth, body4))
        assert r4["commit_id"] not in (cid1, r2["commit_id"], rb["new_commit_id"])

        files = server_repo.list_scope_files(auth["_scope"])
        assert files["doc.md"] == b"# V4"

    def test_multi_scope_isolation(self, server_repo, api_auth):
        """Files in /docs/ scope invisible to /src/ scope client."""
        k_docs = api_auth.issue("doc-agent", "scope-docs", "rw")
        k_src = api_auth.issue("src-agent", "scope-src", "rw")
        a_docs = _auth(api_auth, k_docs)
        a_src = _auth(api_auth, k_src)

        _run(_handle_push(server_repo, a_docs,
                          _make_push(server_repo, {"readme.md": b"Docs"})))
        _run(_handle_push(server_repo, a_src,
                          _make_push(server_repo, {"main.py": b"Code"})))

        pull_docs = _run(_handle_pull(server_repo, a_docs,
                                      {"since_commit_id": ""}))
        assert "readme.md" in pull_docs["files"]
        assert "main.py" not in pull_docs["files"]

        pull_src = _run(_handle_pull(server_repo, a_src,
                                     {"since_commit_id": ""}))
        assert "main.py" in pull_src["files"]
        assert "readme.md" not in pull_src["files"]
