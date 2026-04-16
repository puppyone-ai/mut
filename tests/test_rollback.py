"""Tests for rollback mechanism — revert via commit_id."""

import asyncio
import base64
import json
import pytest

from mut.server.repo import ServerRepo
from mut.server.server import _handle_push, _handle_rollback, _handle_clone
from mut.server.handlers import handle_rollback


_SEED = "seedseedseedseed"


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
    repo.record_history(_SEED, "server", "initial state", "/", [],
                        root_hash=root)
    repo.set_head_commit_id(_SEED)
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
        "snapshots": [{"id": 1, "root": root_hash, "message": "push",
                       "who": "agent-A", "time": ""}],
        "objects": objects_b64,
    }


class TestRollbackSync:
    """Test sync rollback handler."""

    def test_rollback_to_earlier_commit(self, server_repo, auth):
        body1 = _make_push_body(server_repo, {"file-a.txt": b"version1"})
        r1 = _run(_handle_push(server_repo, auth, body1))
        cid1 = r1["commit_id"]

        body2 = _make_push_body(server_repo, {"file-a.txt": b"version2"},
                                base_commit_id=cid1)
        r2 = _run(_handle_push(server_repo, auth, body2))
        cid2 = r2["commit_id"]
        assert cid1 != cid2

        rb = handle_rollback(server_repo, auth,
                             {"target_commit_id": cid1})
        assert rb["status"] == "rolled-back"
        assert rb["target_commit_id"] == cid1
        assert rb["new_commit_id"]  # rollback creates a forward commit
        assert rb["new_commit_id"] != cid1
        assert rb["new_commit_id"] != cid2

        scope = auth["_scope"]
        files = server_repo.list_scope_files(scope)
        assert files.get("file-a.txt") == b"version1"

    def test_rollback_already_at_commit(self, server_repo, auth):
        body = _make_push_body(server_repo, {"x.txt": b"data"})
        r = _run(_handle_push(server_repo, auth, body))

        result = handle_rollback(server_repo, auth,
                                 {"target_commit_id": r["commit_id"]})
        assert result["status"] == "already-at-commit"

    def test_rollback_missing_target_raises(self, server_repo, auth):
        body = _make_push_body(server_repo, {"x.txt": b"data"})
        _run(_handle_push(server_repo, auth, body))

        with pytest.raises(ValueError):
            handle_rollback(server_repo, auth, {"target_commit_id": ""})

    def test_rollback_unknown_commit(self, server_repo, auth):
        body = _make_push_body(server_repo, {"x.txt": b"data"})
        _run(_handle_push(server_repo, auth, body))

        with pytest.raises(ValueError):
            handle_rollback(server_repo, auth,
                            {"target_commit_id": "deadbeefdeadbeef"})

    def test_rollback_readonly_rejected(self, server_repo):
        server_repo.add_scope("scope-ro", "/")
        scope = server_repo.scopes.get_by_id("scope-ro")
        scope["mode"] = "r"
        auth = {"agent": "reader", "_scope": scope}

        from mut.foundation.error import PermissionDenied
        with pytest.raises(PermissionDenied):
            handle_rollback(server_repo, auth,
                            {"target_commit_id": "abcdef1234567890"})

    def test_rollback_preserves_history(self, server_repo, auth):
        """All prior commits remain in history after rollback."""
        body1 = _make_push_body(server_repo, {"a.txt": b"v1"})
        r1 = _run(_handle_push(server_repo, auth, body1))
        body2 = _make_push_body(server_repo, {"a.txt": b"v2"},
                                base_commit_id=r1["commit_id"])
        r2 = _run(_handle_push(server_repo, auth, body2))

        rb = handle_rollback(server_repo, auth,
                             {"target_commit_id": r1["commit_id"]})

        for cid in (_SEED, r1["commit_id"], r2["commit_id"], rb["new_commit_id"]):
            entry = server_repo.get_history_entry(cid)
            assert entry is not None, f"missing commit {cid}"

        assert "rollback" in server_repo.get_history_entry(
            rb["new_commit_id"]
        )["message"]


class TestRollbackAsync:
    """Test async rollback handler via server route."""

    def test_async_rollback(self, server_repo, auth):
        body = _make_push_body(server_repo, {"doc.md": b"# Hello"})
        r1 = _run(_handle_push(server_repo, auth, body))

        body2 = _make_push_body(server_repo, {"doc.md": b"# Updated"},
                                base_commit_id=r1["commit_id"])
        _run(_handle_push(server_repo, auth, body2))

        result = _run(_handle_rollback(server_repo, auth,
                                       {"target_commit_id": r1["commit_id"]}))
        assert result["status"] == "rolled-back"
        assert result["target_commit_id"] == r1["commit_id"]
        assert result["new_commit_id"]  # rollback creates a forward commit

        clone_result = _run(_handle_clone(server_repo, auth, {}))
        content = base64.b64decode(clone_result["files"]["doc.md"])
        assert content == b"# Hello"
