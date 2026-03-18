"""Tests for server/repo.py — ServerRepo initialization and file operations."""

import json
import pytest

from mut.server.repo import ServerRepo


@pytest.fixture
def server_repo(tmp_path):
    return ServerRepo.init(str(tmp_path / "repo"), project_name="test-project")


class TestServerRepoInit:
    def test_init_creates_structure(self, tmp_path):
        repo = ServerRepo.init(str(tmp_path / "repo"))
        root = tmp_path / "repo"
        assert (root / "current").is_dir()
        assert (root / ".mut-server" / "objects").is_dir()
        assert (root / ".mut-server" / "scopes").is_dir()
        assert (root / ".mut-server" / "history").is_dir()
        assert (root / ".mut-server" / "locks").is_dir()
        assert (root / ".mut-server" / "audit").is_dir()

    def test_double_init_fails(self, tmp_path):
        ServerRepo.init(str(tmp_path / "repo"))
        with pytest.raises(FileExistsError):
            ServerRepo.init(str(tmp_path / "repo"))

    def test_project_name(self, server_repo):
        assert server_repo.get_project_name() == "test-project"

    def test_initial_version_zero(self, server_repo):
        assert server_repo.get_latest_version() == 0


class TestServerRepoScopes:
    def test_add_and_get_scope(self, server_repo):
        server_repo.add_scope("scope-1", "/src/")
        scope = server_repo.scopes.get_by_id("scope-1")
        assert scope is not None
        assert scope["id"] == "scope-1"
        assert scope["path"] == "/src/"
        assert scope["exclude"] == []

    def test_add_scope_with_exclude(self, server_repo):
        server_repo.add_scope("scope-2", "/docs/", exclude=["/docs/private/"])
        scope = server_repo.scopes.get_by_id("scope-2")
        assert scope["exclude"] == ["/docs/private/"]


class TestServerRepoFiles:
    def test_write_and_list_files(self, server_repo):
        scope = {"id": "s1", "path": "/", "exclude": [], "mode": "rw"}
        files = {"hello.txt": b"hello", "sub/deep.txt": b"deep"}
        server_repo.write_scope_files(scope, files)
        listed = server_repo.list_scope_files(scope)
        assert listed["hello.txt"] == b"hello"
        assert listed["sub/deep.txt"] == b"deep"

    def test_delete_scope_file(self, server_repo):
        scope = {"id": "s1", "path": "/", "exclude": [], "mode": "rw"}
        server_repo.write_scope_files(scope, {"a.txt": b"a", "b.txt": b"b"})
        server_repo.delete_scope_file(scope, "a.txt")
        listed = server_repo.list_scope_files(scope)
        assert "a.txt" not in listed
        assert "b.txt" in listed

    def test_list_with_excludes(self, server_repo):
        scope = {"id": "s1", "path": "/src/", "exclude": ["/src/vendor/"], "mode": "rw"}
        base = server_repo.current / "src"
        base.mkdir(parents=True, exist_ok=True)
        (base / "main.py").write_bytes(b"main")
        vendor = base / "vendor"
        vendor.mkdir()
        (vendor / "lib.py").write_bytes(b"lib")
        listed = server_repo.list_scope_files(scope)
        assert "main.py" in listed
        assert "vendor/lib.py" not in listed

    def test_list_empty_scope(self, server_repo):
        scope = {"id": "s1", "path": "/nonexistent/", "exclude": [], "mode": "rw"}
        assert server_repo.list_scope_files(scope) == {}

    def test_path_traversal_blocked(self, server_repo):
        scope = {"id": "s1", "path": "/", "exclude": [], "mode": "rw"}
        with pytest.raises(ValueError, match="path traversal"):
            server_repo.write_scope_files(scope, {"../../escape.txt": b"bad"})


class TestServerRepoTree:
    def test_build_full_tree_empty(self, server_repo):
        root = server_repo.build_full_tree()
        assert isinstance(root, str)
        assert len(root) > 0

    def test_build_full_tree_with_files(self, server_repo):
        (server_repo.current / "a.txt").write_bytes(b"aaa")
        root = server_repo.build_full_tree()
        assert root

    def test_build_scope_tree(self, server_repo):
        scope = {"id": "s1", "path": "/", "exclude": [], "mode": "rw"}
        server_repo.write_scope_files(scope, {"x.txt": b"xxx"})
        tree_hash = server_repo.build_scope_tree(scope)
        assert tree_hash


class TestServerRepoLock:
    def test_acquire_and_release(self, server_repo):
        assert server_repo.acquire_lock("scope-1")
        server_repo.release_lock("scope-1")

    def test_double_acquire_fails(self, server_repo):
        assert server_repo.acquire_lock("scope-1")
        assert not server_repo.acquire_lock("scope-1")
        server_repo.release_lock("scope-1")


class TestServerRepoHistory:
    def test_version_management(self, server_repo):
        assert server_repo.get_latest_version() == 0
        server_repo.set_latest_version(5)
        assert server_repo.get_latest_version() == 5

    def test_root_hash(self, server_repo):
        assert server_repo.get_root_hash() == ""
        server_repo.set_root_hash("abc123")
        assert server_repo.get_root_hash() == "abc123"

    def test_record_and_get_history(self, server_repo):
        server_repo.record_history(1, "agent-A", "push 1", "/src/",
                                   [{"path": "src/main.py", "action": "add"}])
        entry = server_repo.get_history_entry(1)
        assert entry is not None
        assert entry["who"] == "agent-A"

    def test_history_since(self, server_repo):
        server_repo.record_history(1, "a", "v1", "/src/", [])
        server_repo.record_history(2, "b", "v2", "/src/", [])
        server_repo.set_latest_version(2)
        entries = server_repo.get_history_since(0, scope_path="/src/")
        assert len(entries) == 2


class TestServerRepoAudit:
    def test_record_audit(self, server_repo):
        server_repo.record_audit("test_event", "agent-A", {"key": "value"})
        # Audit is append-only files — just check no exception
        audit_files = list(server_repo.audit.dir.iterdir())
        assert len(audit_files) == 1
