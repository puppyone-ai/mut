"""Tests for server/handlers.py — request handler functions.

These tests create a real ServerRepo on disk and call handler functions
directly (bypassing HTTP), simulating a full push/pull/clone cycle.
"""

import json
import base64
import pytest

from mut.server.repo import ServerRepo
from tests._handlers_shim import (
    handle_clone, handle_push, handle_pull,
    handle_negotiate,
)
from mut.server.history import HistoryManager
from mut.foundation.error import PermissionDenied


_CID_SEED = "a1b2c3d4e5f60718"


@pytest.fixture
def server_repo(tmp_path):
    repo = ServerRepo.init(str(tmp_path / "server"), project_name="test-proj")
    # Seed: build initial tree and one "initial" commit so pulls have a baseline.
    root = repo.build_full_tree()
    repo.set_root_hash(root)
    repo.record_history(
        _CID_SEED, "server", "initial state", "/", [], root_hash=root,
    )
    repo.set_head_commit_id(_CID_SEED)
    return repo


@pytest.fixture
def rw_scope(server_repo):
    """Create an rw scope at /src/ for agent-A."""
    server_repo.add_scope("scope-1", "/src/")
    scope = server_repo.scopes.get_by_id("scope-1")
    scope["mode"] = "rw"  # mode comes from auth layer
    return scope


@pytest.fixture
def auth(rw_scope):
    return {"agent": "agent-A", "_scope": rw_scope}


class TestHandleClone:
    def test_clone_empty_scope(self, server_repo, auth):
        result = handle_clone(server_repo, auth, {})
        assert result["project"] == "test-proj"
        assert isinstance(result["files"], dict)
        assert isinstance(result["objects"], dict)
        assert "head_commit_id" in result
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

    def test_clone_returns_scope_head_not_global(self, server_repo):
        """Regression for Bug #2: clone must return the per-scope head,
        otherwise the client's REMOTE_HEAD ends up as a commit from a
        different scope and /pull + /push misbehave.
        """
        server_repo.add_scope("scope-src", "/src/")
        server_repo.add_scope("scope-docs", "/docs/")
        src_scope = server_repo.scopes.get_by_id("scope-src")
        docs_scope = server_repo.scopes.get_by_id("scope-docs")
        src_scope["mode"] = "rw"
        docs_scope["mode"] = "rw"

        docs_cid = "d" * 16
        server_repo.record_history(
            docs_cid, "agent-D", "push to docs", "/docs/", [],
        )
        server_repo.history.set_scope_head_commit_id("/docs/", docs_cid)
        server_repo.set_head_commit_id(docs_cid)

        src_result = handle_clone(
            server_repo, {"agent": "agent-A", "_scope": src_scope}, {},
        )
        assert src_result["head_commit_id"] != docs_cid
        assert src_result["head_commit_id"] == \
            server_repo.history.get_scope_head_commit_id("/src/")


class TestHandlePush:
    def _push_with_files(self, server_repo, auth, files: dict[str, bytes],
                         base_commit_id: str = ""):
        """Helper: build a valid push body with tree + objects."""
        from mut.core import tree as tree_mod

        for content in files.values():
            server_repo.store.put(content)

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

        body = {
            "base_commit_id": base_commit_id,
            "snapshots": [{"id": 1, "root": root_hash, "message": "test push",
                           "who": "agent-A", "time": ""}],
            "objects": objects_b64,
        }
        return handle_push(server_repo, auth, body)

    def test_push_empty_snapshots(self, server_repo, auth):
        result = handle_push(server_repo, auth, {
            "base_commit_id": "", "snapshots": [], "objects": {},
        })
        assert result["status"] == "ok"

    def test_push_creates_files(self, server_repo, auth):
        result = self._push_with_files(
            server_repo, auth, {"main.py": b"hello"},
        )
        assert result["status"] == "ok"
        assert len(result["commit_id"]) == 16  # 16-hex SHA256 prefix
        assert result["pushed"] == 1

        scope = auth["_scope"]
        files = server_repo.list_scope_files(scope)
        assert "main.py" in files

    def test_push_readonly_scope(self, server_repo):
        server_repo.add_scope("ro-scope", "/docs/")
        scope = server_repo.scopes.get_by_id("ro-scope")
        scope["mode"] = "r"
        ro_auth = {"agent": "agent-R", "_scope": scope}
        with pytest.raises(PermissionDenied, match="read-only"):
            handle_push(server_repo, ro_auth, {})

    def test_push_cas_retry_on_concurrent_update(self, server_repo, auth):
        """Stale base_commit_id still succeeds via server-side merge AND
        MUST NOT silently delete files that were only on the server.
        Regression guard for the empty-base short-circuit bug in
        `_resolve_conflicts`.
        """
        r1 = self._push_with_files(server_repo, auth, {"a.py": b"first"})
        assert r1["status"] == "ok"

        r2 = self._push_with_files(
            server_repo, auth, {"b.py": b"second"},
            base_commit_id="",  # stale (empty) base
        )
        assert r2["status"] == "ok"

        scope = auth["_scope"]
        files = server_repo.list_scope_files(scope)
        assert "a.py" in files, (
            "regression: empty base_commit_id caused the server to drop "
            "a file from the previous push"
        )
        assert files["a.py"] == b"first"
        assert "b.py" in files
        assert files["b.py"] == b"second"

    def test_push_returns_new_commit_id(self, server_repo, auth):
        r1 = self._push_with_files(server_repo, auth, {"a.py": b"v1"})
        r2 = self._push_with_files(
            server_repo, auth, {"a.py": b"v2"},
            base_commit_id=r1["commit_id"],
        )
        assert r1["commit_id"] != r2["commit_id"]
        assert len(r1["commit_id"]) == 16
        assert len(r2["commit_id"]) == 16

    def test_push_commit_id_is_deterministic_from_payload(self, server_repo):
        """compute_commit_id is a pure function of (scope, hash, time, who)."""
        cid_a = HistoryManager.compute_commit_id(
            scope_path="/src/", scope_hash="a" * 16,
            created_at_iso="2026-01-01T00:00:00+00:00",
            who="alice",
        )
        cid_b = HistoryManager.compute_commit_id(
            scope_path="/src/", scope_hash="a" * 16,
            created_at_iso="2026-01-01T00:00:00+00:00",
            who="alice",
        )
        assert cid_a == cid_b


class TestHandlePull:
    def test_pull_up_to_date(self, server_repo, auth):
        """No commits for this scope yet → returns baseline head."""
        server_repo.history.set_scope_head_commit_id("/src/", _CID_SEED)
        result = handle_pull(server_repo, auth,
                             {"since_commit_id": _CID_SEED})
        assert result["status"] == "up-to-date"

    def test_pull_after_push(self, server_repo, auth):
        scope = auth["_scope"]
        server_repo.write_scope_files(scope, {"f.txt": b"content"})
        cid = "1111111111111111"
        server_repo.record_history(cid, "agent-A", "push", "/src/",
                                   [{"path": "src/f.txt", "action": "add"}])
        server_repo.history.set_scope_head_commit_id("/src/", cid)
        server_repo.set_head_commit_id(cid)

        result = handle_pull(server_repo, auth, {"since_commit_id": ""})
        assert result["status"] == "updated"
        assert result["head_commit_id"] == cid
        assert "f.txt" in result["files"]

    def test_pull_skips_known_objects(self, server_repo, auth):
        scope = auth["_scope"]
        server_repo.write_scope_files(scope, {"f.txt": b"content"})
        cid = "2222222222222222"
        server_repo.record_history(cid, "a", "push", "/src/", [])
        server_repo.history.set_scope_head_commit_id("/src/", cid)
        server_repo.set_head_commit_id(cid)

        from mut.core import tree as tree_mod
        scope_tree = server_repo.build_scope_tree(scope)
        all_hashes = list(
            tree_mod.collect_reachable_hashes(server_repo.store, scope_tree)
        )

        result = handle_pull(server_repo, auth, {
            "since_commit_id": "",
            "have_hashes": all_hashes,
        })
        assert result["objects"] == {}  # client already has everything


class TestHandleNegotiate:
    def test_negotiate_all_missing(self, server_repo, auth):
        result = handle_negotiate(server_repo, auth,
                                  {"hashes": ["aaa", "bbb"]})
        assert set(result["missing"]) == {"aaa", "bbb"}

    def test_negotiate_some_present(self, server_repo, auth):
        h = server_repo.store.put(b"existing object")
        result = handle_negotiate(server_repo, auth,
                                  {"hashes": [h, "missing"]})
        assert "missing" in result["missing"]
        assert h not in result["missing"]

    def test_negotiate_empty(self, server_repo, auth):
        result = handle_negotiate(server_repo, auth, {"hashes": []})
        assert result["missing"] == []
