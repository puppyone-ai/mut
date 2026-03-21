"""Tests for rollback mechanism — revert commit via handlers."""

import asyncio
import base64
import json
import pytest

from mut.server.repo import ServerRepo
from mut.server.server import _handle_push, _handle_rollback, _handle_clone
from mut.server.handlers import handle_rollback


def _run(coro):
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


@pytest.fixture
def server_repo(tmp_path):
    repo = ServerRepo.init(str(tmp_path / "server"), project_name="test-proj")
    root = repo.build_full_tree()
    repo.set_root_hash(root)
    repo.record_history(0, "server", "initial state", "/", [], root_hash=root)
    return repo


@pytest.fixture
def rw_scope(server_repo):
    server_repo.add_scope("scope-all", "/")
    scope = server_repo.scopes.get_by_id("scope-all")
    scope["mode"] = "rw"
    return scope


@pytest.fixture
def auth(rw_scope):
    return {"agent": "agent-A", "_scope": rw_scope}


def _make_push_body(repo, files: dict, base_version: int = 0) -> dict:
    from mut.core import tree as tree_mod

    nested = {}
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
        "base_version": base_version,
        "snapshots": [{"id": 1, "root": root_hash, "message": "push",
                       "who": "agent-A", "time": ""}],
        "objects": objects_b64,
    }


class TestRollbackSync:
    """Test sync rollback handler."""

    def test_rollback_to_v1(self, server_repo, auth):
        # Push v1: file-a.txt
        body1 = _make_push_body(server_repo, {"file-a.txt": b"version1"})
        result1 = _run(_handle_push(server_repo, auth, body1))
        assert result1["version"] == 1

        # Push v2: file-a.txt updated
        body2 = _make_push_body(server_repo, {"file-a.txt": b"version2"}, base_version=1)
        result2 = _run(_handle_push(server_repo, auth, body2))
        assert result2["version"] == 2

        # Rollback to v1
        rb_result = handle_rollback(server_repo, auth, {"target_version": 1})
        assert rb_result["status"] == "rolled-back"
        assert rb_result["new_version"] == 3
        assert rb_result["target_version"] == 1

        # Verify the file content is back to v1
        scope = auth["_scope"]
        files = server_repo.list_scope_files(scope)
        assert files.get("file-a.txt") == b"version1"

        # Version chain is v0→v1→v2→v3(rollback)
        assert server_repo.get_latest_version() == 3

    def test_rollback_already_at_version(self, server_repo, auth):
        body = _make_push_body(server_repo, {"x.txt": b"data"})
        _run(_handle_push(server_repo, auth, body))

        result = handle_rollback(server_repo, auth, {"target_version": 1})
        assert result["status"] == "already-at-version"

    def test_rollback_invalid_version_zero(self, server_repo, auth):
        body = _make_push_body(server_repo, {"x.txt": b"data"})
        _run(_handle_push(server_repo, auth, body))

        with pytest.raises(ValueError, match="invalid target version"):
            handle_rollback(server_repo, auth, {"target_version": 0})

    def test_rollback_future_version(self, server_repo, auth):
        body = _make_push_body(server_repo, {"x.txt": b"data"})
        _run(_handle_push(server_repo, auth, body))

        with pytest.raises(ValueError, match="invalid target version"):
            handle_rollback(server_repo, auth, {"target_version": 999})

    def test_rollback_readonly_rejected(self, server_repo):
        server_repo.add_scope("scope-ro", "/")
        scope = server_repo.scopes.get_by_id("scope-ro")
        scope["mode"] = "r"
        auth = {"agent": "reader", "_scope": scope}

        from mut.foundation.error import PermissionDenied
        with pytest.raises(PermissionDenied):
            handle_rollback(server_repo, auth, {"target_version": 1})

    def test_rollback_preserves_history(self, server_repo, auth):
        """All versions remain in history after rollback."""
        body1 = _make_push_body(server_repo, {"a.txt": b"v1"})
        _run(_handle_push(server_repo, auth, body1))
        body2 = _make_push_body(server_repo, {"a.txt": b"v2"}, base_version=1)
        _run(_handle_push(server_repo, auth, body2))

        handle_rollback(server_repo, auth, {"target_version": 1})

        # All 4 entries exist: v0 (init), v1, v2, v3 (rollback)
        for v in range(4):
            entry = server_repo.get_history_entry(v)
            assert entry is not None
        assert "rollback" in server_repo.get_history_entry(3)["message"]


class TestRollbackAsync:
    """Test async rollback handler via server route."""

    def test_async_rollback(self, server_repo, auth):
        body = _make_push_body(server_repo, {"doc.md": b"# Hello"})
        _run(_handle_push(server_repo, auth, body))

        body2 = _make_push_body(server_repo, {"doc.md": b"# Updated"}, base_version=1)
        _run(_handle_push(server_repo, auth, body2))

        result = _run(_handle_rollback(server_repo, auth, {"target_version": 1}))
        assert result["status"] == "rolled-back"
        assert result["new_version"] == 3

        # Verify via clone
        clone_result = _run(_handle_clone(server_repo, auth, {}))
        content = base64.b64decode(clone_result["files"]["doc.md"])
        assert content == b"# Hello"
