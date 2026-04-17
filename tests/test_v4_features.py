"""Tests for v4 remaining features: .env credentials, X-Mut-User header,
pull-commit endpoint, remote command, and client notification listener."""

import asyncio
import base64
import json
import pytest

from mut.server.repo import ServerRepo
from tests._handlers_shim import (
    _handle_push, _handle_pull_commit, _handle_clone,
)
from mut.foundation.config import load_env, get_client_credential
from mut.foundation.transport import MutClient


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
        "snapshots": [{"id": 1, "root": root_hash, "message": "test",
                       "who": "agent-A", "time": ""}],
        "objects": objects_b64,
    }


# ── .env credential loading ───────────────────────────────────

class TestLoadEnv:
    def test_load_env_basic(self, tmp_path):
        env_file = tmp_path / ".env"
        env_file.write_text('MUT_KEY=mut_abc123\nMUT_USER=alice@co.com\n')
        result = load_env(tmp_path)
        assert result["MUT_KEY"] == "mut_abc123"
        assert result["MUT_USER"] == "alice@co.com"

    def test_load_env_with_quotes(self, tmp_path):
        env_file = tmp_path / ".env"
        env_file.write_text("MUT_KEY='mut_quoted'\nMUT_USER=\"bob@co.com\"\n")
        result = load_env(tmp_path)
        assert result["MUT_KEY"] == "mut_quoted"
        assert result["MUT_USER"] == "bob@co.com"

    def test_load_env_skips_comments(self, tmp_path):
        env_file = tmp_path / ".env"
        env_file.write_text("# comment\nMUT_KEY=abc\n\n# another\n")
        result = load_env(tmp_path)
        assert result == {"MUT_KEY": "abc"}

    def test_load_env_missing_file(self, tmp_path):
        result = load_env(tmp_path)
        assert result == {}


class TestGetClientCredential:
    def test_from_env(self, tmp_path):
        # Setup .mut/ directory
        mut_root = tmp_path / ".mut"
        mut_root.mkdir()
        (mut_root / "config.json").write_text('{}')

        # Setup .env
        (tmp_path / ".env").write_text("MUT_KEY=env_key\nMUT_USER=env_user\n")

        cred, user = get_client_credential(mut_root, tmp_path)
        assert cred == "env_key"
        assert user == "env_user"

    def test_fallback_to_credential_file(self, tmp_path):
        mut_root = tmp_path / ".mut"
        mut_root.mkdir()
        (mut_root / "credential").write_text("file_key")
        (mut_root / "config.json").write_text('{"user_identity": "file_user"}')

        cred, user = get_client_credential(mut_root, tmp_path)
        assert cred == "file_key"
        assert user == "file_user"

    def test_env_takes_precedence(self, tmp_path):
        mut_root = tmp_path / ".mut"
        mut_root.mkdir()
        (mut_root / "credential").write_text("old_key")
        (mut_root / "config.json").write_text('{"user_identity": "old_user"}')
        (tmp_path / ".env").write_text("MUT_KEY=new_key\nMUT_USER=new_user\n")

        cred, user = get_client_credential(mut_root, tmp_path)
        assert cred == "new_key"
        assert user == "new_user"


# ── MutClient user_identity header ────────────────────────────

class TestMutClientUserIdentity:
    def test_client_stores_user_identity(self):
        c = MutClient("http://localhost:9742", "key", user_identity="alice@co.com")
        assert c.user_identity == "alice@co.com"

    def test_client_default_no_identity(self):
        c = MutClient("http://localhost:9742", "key")
        assert c.user_identity == ""

    def test_client_has_post_method(self):
        c = MutClient("http://localhost:9742", "key")
        assert hasattr(c, "post")

    def test_client_has_pull_commit_method(self):
        c = MutClient("http://localhost:9742", "key")
        assert hasattr(c, "pull_commit")

    def test_client_has_rollback_method(self):
        c = MutClient("http://localhost:9742", "key")
        assert hasattr(c, "rollback")


# ── Pull Commit endpoint ──────────────────────────────────────

class TestPullCommit:
    def test_pull_specific_commit(self, server_repo, auth):
        body1 = _make_push_body(server_repo, {"a.txt": b"version-1"})
        r1 = _run(_handle_push(server_repo, auth, body1))
        cid1 = r1["commit_id"]

        body2 = _make_push_body(server_repo, {"a.txt": b"version-2"},
                                base_commit_id=cid1)
        _run(_handle_push(server_repo, auth, body2))

        result = _run(_handle_pull_commit(server_repo, auth,
                                          {"commit_id": cid1}))
        assert result["status"] == "ok"
        assert result["commit_id"] == cid1
        content = base64.b64decode(result["files"]["a.txt"])
        assert content == b"version-1"

    def test_pull_latest_commit(self, server_repo, auth):
        body = _make_push_body(server_repo, {"a.txt": b"latest"})
        r = _run(_handle_push(server_repo, auth, body))

        result = _run(_handle_pull_commit(server_repo, auth,
                                          {"commit_id": r["commit_id"]}))
        assert result["commit_id"] == r["commit_id"]
        assert base64.b64decode(result["files"]["a.txt"]) == b"latest"

    def test_pull_commit_missing_id(self, server_repo, auth):
        body = _make_push_body(server_repo, {"a.txt": b"data"})
        _run(_handle_push(server_repo, auth, body))

        with pytest.raises(ValueError):
            _run(_handle_pull_commit(server_repo, auth, {"commit_id": ""}))

    def test_pull_unknown_commit(self, server_repo, auth):
        body = _make_push_body(server_repo, {"a.txt": b"data"})
        _run(_handle_push(server_repo, auth, body))

        with pytest.raises(ValueError):
            _run(_handle_pull_commit(server_repo, auth,
                                     {"commit_id": "deadbeefdeadbeef"}))

    def test_pull_commit_includes_objects(self, server_repo, auth):
        body = _make_push_body(server_repo, {"a.txt": b"data"})
        r = _run(_handle_push(server_repo, auth, body))

        result = _run(_handle_pull_commit(server_repo, auth,
                                          {"commit_id": r["commit_id"]}))
        assert len(result["objects"]) > 0

    def test_pull_commit_scoped(self, server_repo):
        """Pull-commit only returns files within the authenticated scope."""
        server_repo.add_scope("scope-docs", "/docs/")
        docs_scope = server_repo.scopes.get_by_id("scope-docs")
        docs_scope["mode"] = "rw"
        docs_auth = {"agent": "doc-agent", "_scope": docs_scope}

        server_repo.add_scope("scope-src", "/src/")
        src_scope = server_repo.scopes.get_by_id("scope-src")
        src_scope["mode"] = "rw"
        src_auth = {"agent": "src-agent", "_scope": src_scope}

        body_docs = _make_push_body(server_repo, {"readme.md": b"# Docs"})
        r_docs = _run(_handle_push(server_repo, docs_auth, body_docs))

        body_src = _make_push_body(server_repo, {"main.py": b"print(1)"})
        _run(_handle_push(server_repo, src_auth, body_src))

        result = _run(_handle_pull_commit(server_repo, docs_auth,
                                          {"commit_id": r_docs["commit_id"]}))
        assert "readme.md" in result["files"]
        assert "main.py" not in result["files"]


# ── Remote command ─────────────────────────────────────────────

class TestRemoteCommand:
    def test_remote_add_and_show(self, tmp_path):
        from mut.ops.init_op import init
        import os
        orig = os.getcwd()
        try:
            os.chdir(tmp_path)
            init(str(tmp_path))
            from mut.ops.repo import MutRepo
            from mut.foundation.config import load_config, save_config
            repo = MutRepo(str(tmp_path))
            config = load_config(repo.mut_root)
            config["server"] = "http://localhost:9742"
            save_config(repo.mut_root, config)

            loaded = load_config(repo.mut_root)
            assert loaded["server"] == "http://localhost:9742"
        finally:
            os.chdir(orig)

    def test_remote_remove(self, tmp_path):
        from mut.ops.init_op import init
        import os
        orig = os.getcwd()
        try:
            os.chdir(tmp_path)
            init(str(tmp_path))
            from mut.ops.repo import MutRepo
            from mut.foundation.config import load_config, save_config
            repo = MutRepo(str(tmp_path))
            config = load_config(repo.mut_root)
            config["server"] = "http://localhost:9742"
            save_config(repo.mut_root, config)

            # Remove
            config.pop("server", None)
            save_config(repo.mut_root, config)
            loaded = load_config(repo.mut_root)
            assert "server" not in loaded
        finally:
            os.chdir(orig)


# ── NotificationListener ──────────────────────────────────────

class TestNotificationListener:
    def test_init(self):
        from mut.ops.notify_op import NotificationListener
        listener = NotificationListener(
            "http://localhost:9742", "key", "alice@co.com",
        )
        assert listener.server_url == "http://localhost:9742"
        assert listener.credential == "key"
        assert listener.user_identity == "alice@co.com"
        assert listener._closed is False

    def test_close(self):
        from mut.ops.notify_op import NotificationListener
        listener = NotificationListener("http://localhost:9742", "key")
        listener.close()
        assert listener._closed is True

    def test_handle_message_callback(self):
        from mut.ops.notify_op import NotificationListener
        received = []
        listener = NotificationListener(
            "http://localhost:9742", "key",
            on_notification=lambda msg: received.append(msg),
        )
        cid = "a1b2c3d4e5f60718"
        listener._handle_message(json.dumps({
            "notification_id": "n1",
            "type": "version_update",
            "commit_id": cid,
        }).encode())
        assert len(received) == 1
        assert received[0]["commit_id"] == cid

    def test_handle_message_idempotent(self):
        from mut.ops.notify_op import NotificationListener
        received = []
        listener = NotificationListener(
            "http://localhost:9742", "key",
            on_notification=lambda msg: received.append(msg),
        )
        msg = json.dumps({"notification_id": "n1", "type": "test"}).encode()
        listener._handle_message(msg)
        listener._handle_message(msg)  # duplicate
        assert len(received) == 1  # only once

    def test_handle_message_invalid_json(self):
        from mut.ops.notify_op import NotificationListener
        received = []
        listener = NotificationListener(
            "http://localhost:9742", "key",
            on_notification=lambda msg: received.append(msg),
        )
        listener._handle_message(b"not json")
        assert len(received) == 0  # silently ignored
