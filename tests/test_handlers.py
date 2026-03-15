"""Tests for server/handlers.py — request handler functions.

These tests create a real ServerRepo on disk and call handler functions
directly (bypassing HTTP), simulating a full push/pull/clone cycle.
"""

import json
import base64
import pytest

from mut.server.repo import ServerRepo
from mut.server.handlers import (
    handle_clone, handle_push, handle_pull,
    handle_negotiate, handle_register,
)
from mut.core.auth import sign_token
from mut.foundation.error import PermissionDenied, LockError


@pytest.fixture
def server_repo(tmp_path):
    repo = ServerRepo.init(str(tmp_path / "server"), project_name="test-proj")
    # Build initial tree + version 0
    root = repo.build_full_tree()
    repo.set_root_hash(root)
    repo.record_history(0, "server", "initial state", "/", [], root_hash=root)
    return repo


@pytest.fixture
def rw_scope(server_repo):
    """Create an rw scope at /src/ for agent-A."""
    server_repo.add_scope("scope-1", "/src/", ["agent-A"], "rw")
    return server_repo.get_scope_for_agent("agent-A")


@pytest.fixture
def auth(rw_scope):
    return {"agent": "agent-A", "_scope": rw_scope}


def _make_push_body(files: dict[str, bytes], base_version: int = 0) -> dict:
    """Helper: build push body with a tree from file content dict."""
    # We'll construct the tree manually via object_store
    return {
        "base_version": base_version,
        "snapshots": [],
        "objects": {},
    }


class TestHandleClone:
    def test_clone_empty_scope(self, server_repo, auth):
        result = handle_clone(server_repo, auth, {})
        assert result["project"] == "test-proj"
        assert isinstance(result["files"], dict)
        assert isinstance(result["objects"], dict)
        assert isinstance(result["version"], int)
        assert result["scope"]["path"] == "/src/"

    def test_clone_with_files(self, server_repo, auth):
        scope = auth["_scope"]
        server_repo.write_scope_files(scope, {"main.py": b"print('hello')"})
        result = handle_clone(server_repo, auth, {})
        assert "main.py" in result["files"]
        content = base64.b64decode(result["files"]["main.py"])
        assert content == b"print('hello')"

    def test_clone_protocol_version(self, server_repo, auth):
        result = handle_clone(server_repo, auth, {})
        assert "protocol_version" in result


class TestHandlePush:
    def _push_with_files(self, server_repo, auth, files: dict[str, bytes],
                         base_version: int = 0):
        """Helper: build a valid push body with tree + objects."""
        scope = auth["_scope"]
        # Build tree objects the same way ServerRepo does
        from mut.core import tree as tree_mod

        for path, content in files.items():
            server_repo.store.put(content)

        # Build a tree from files
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

        # Collect all objects
        reachable = tree_mod.collect_reachable_hashes(server_repo.store, root_hash)
        objects_b64 = {}
        for h in reachable:
            objects_b64[h] = base64.b64encode(server_repo.store.get(h)).decode()

        body = {
            "base_version": base_version,
            "snapshots": [{"id": 1, "root": root_hash, "message": "test push",
                           "who": "agent-A", "time": ""}],
            "objects": objects_b64,
        }
        return handle_push(server_repo, auth, body)

    def test_push_empty_snapshots(self, server_repo, auth):
        result = handle_push(server_repo, auth, {
            "base_version": 0, "snapshots": [], "objects": {},
        })
        assert result["status"] == "ok"

    def test_push_creates_files(self, server_repo, auth):
        result = self._push_with_files(server_repo, auth, {"main.py": b"hello"})
        assert result["status"] == "ok"
        assert result["version"] == 1
        assert result["pushed"] == 1

        # Verify files were written
        scope = auth["_scope"]
        files = server_repo.list_scope_files(scope)
        assert "main.py" in files

    def test_push_readonly_scope(self, server_repo):
        server_repo.add_scope("ro-scope", "/docs/", ["agent-R"], "r")
        scope = server_repo.get_scope_for_agent("agent-R")
        ro_auth = {"agent": "agent-R", "_scope": scope}
        with pytest.raises(PermissionDenied, match="read-only"):
            handle_push(server_repo, ro_auth, {})

    def test_push_lock_conflict(self, server_repo, auth):
        scope = auth["_scope"]
        server_repo.acquire_lock(scope["id"])
        try:
            with pytest.raises(LockError, match="locked"):
                handle_push(server_repo, auth, {
                    "base_version": 0, "snapshots": [{"id": 1, "root": "x"}],
                    "objects": {},
                })
        finally:
            server_repo.release_lock(scope["id"])

    def test_push_increments_version(self, server_repo, auth):
        result1 = self._push_with_files(server_repo, auth, {"a.py": b"v1"})
        assert result1["version"] == 1

        result2 = self._push_with_files(server_repo, auth, {"a.py": b"v2"},
                                         base_version=1)
        assert result2["version"] == 2


class TestHandlePull:
    def test_pull_up_to_date(self, server_repo, auth):
        result = handle_pull(server_repo, auth, {"since_version": 0})
        assert result["status"] == "up-to-date"

    def test_pull_after_push(self, server_repo, auth):
        scope = auth["_scope"]
        server_repo.write_scope_files(scope, {"f.txt": b"content"})
        root = server_repo.build_full_tree()
        server_repo.set_root_hash(root)
        server_repo.set_latest_version(1)
        server_repo.record_history(1, "agent-A", "push", "/src/",
                                   [{"path": "src/f.txt", "action": "add"}])

        result = handle_pull(server_repo, auth, {"since_version": 0})
        assert result["status"] == "updated"
        assert result["version"] == 1
        assert "f.txt" in result["files"]

    def test_pull_skips_known_objects(self, server_repo, auth):
        scope = auth["_scope"]
        server_repo.write_scope_files(scope, {"f.txt": b"content"})
        root = server_repo.build_full_tree()
        server_repo.set_root_hash(root)
        server_repo.set_latest_version(1)
        server_repo.record_history(1, "a", "push", "/src/", [])

        # Get all hashes the server has for this scope
        from mut.core import tree as tree_mod
        scope_tree = server_repo.build_scope_tree(scope)
        all_hashes = list(tree_mod.collect_reachable_hashes(server_repo.store, scope_tree))

        result = handle_pull(server_repo, auth, {
            "since_version": 0,
            "have_hashes": all_hashes,
        })
        assert result["objects"] == {}  # client already has everything


class TestHandleNegotiate:
    def test_negotiate_all_missing(self, server_repo, auth):
        result = handle_negotiate(server_repo, auth, {"hashes": ["aaa", "bbb"]})
        assert set(result["missing"]) == {"aaa", "bbb"}

    def test_negotiate_some_present(self, server_repo, auth):
        h = server_repo.store.put(b"existing object")
        result = handle_negotiate(server_repo, auth, {"hashes": [h, "missing"]})
        assert "missing" in result["missing"]
        assert h not in result["missing"]

    def test_negotiate_empty(self, server_repo, auth):
        result = handle_negotiate(server_repo, auth, {"hashes": []})
        assert result["missing"] == []


class TestHandleRegister:
    def test_register_via_invite(self, server_repo):
        invite = server_repo.create_invite("/src/", "rw")
        result = handle_register(server_repo, invite["id"])
        assert "agent_id" in result
        assert "token" in result
        assert result["project"] == "test-proj"
        assert result["scope"]["path"] == "/src/"

    def test_register_invalid_invite(self, server_repo):
        with pytest.raises(ValueError, match="invalid"):
            handle_register(server_repo, "nonexistent")
