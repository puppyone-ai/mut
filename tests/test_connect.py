"""Tests for ``mut connect`` — the one-step "attach existing folder" command.

Network is stubbed via monkeypatched :class:`MutClient` so these tests run
without a server. The four orchestration steps inside :func:`connect_op.connect`
(``init`` → ``link_access`` → ``commit`` → ``push``) are exercised end-to-end
using the real on-disk state machinery.
"""

from __future__ import annotations

import json

import pytest

from mut.foundation.error import MutError
from mut.ops import connect_op, init_op, commit_op, link_access_op, push_op
from mut.ops.repo import MutRepo


# ── Fake transport ────────────────────────────────────────────────────────

class _FakeMutClient:
    """In-memory stand-in for :class:`mut.foundation.transport.MutClient`.

    Records every call so tests can assert which steps actually fired.
    """

    server_state: dict[str, object] = {
        "head_commit_id": "srv-head-0",
        "next_commit_id": "srv-head-1",
    }

    def __init__(self, url: str, credential: str, **_: object):
        self.url = url
        self.credential = credential
        type(self).last_instance = self

    def clone(self) -> dict:
        return {
            "head_commit_id": self.server_state["head_commit_id"],
            "files": {},
            "objects": {},
            "project": "test-project",
        }

    def push(self, base_commit_id: str, snapshots: list, objects: dict) -> dict:
        type(self).last_push = {
            "base_commit_id": base_commit_id,
            "snapshots": snapshots,
            "objects": list(objects.keys()),
        }
        return {
            "commit_id": self.server_state["next_commit_id"],
            "merged": False,
            "conflicts": 0,
        }

    def negotiate(self, hashes=None, remote_head: str = "") -> dict:
        return {
            "missing": [],
            "server_head_commit_id": self.server_state["head_commit_id"],
            "remote_head_recognized": True,
        }

    def pull(self, *_, **__) -> dict:
        return {
            "files": {},
            "objects": {},
            "head_commit_id": self.server_state["head_commit_id"],
        }


@pytest.fixture
def fake_client(monkeypatch):
    """Patch every ``MutClient`` lookup site to return :class:`_FakeMutClient`.

    Each op imports ``MutClient`` at module top, so we have to patch on every
    module that ``connect_op`` chains through (``link_access_op``, ``push_op``)
    and reset the recorded state between tests.
    """
    _FakeMutClient.last_instance = None
    _FakeMutClient.last_push = None
    monkeypatch.setattr(link_access_op, "MutClient", _FakeMutClient)
    monkeypatch.setattr(push_op, "MutClient", _FakeMutClient)
    return _FakeMutClient


@pytest.fixture
def workdir(tmp_path):
    d = tmp_path / "existing-folder"
    d.mkdir()
    return d


URL = "https://api.example.com/api/v1/mut/ap/test-key-abc"


# ── Happy-path orchestration ─────────────────────────────────────────────

class TestConnectOrchestration:
    def test_creates_mut_dir_when_absent(self, workdir, fake_client):
        result = connect_op.connect(URL, workdir=str(workdir))

        assert (workdir / ".mut").is_dir()
        assert result["status"] == "connected"
        assert result["initialized"] is True

    def test_preserves_existing_mut_dir(self, workdir, fake_client):
        init_op.init(str(workdir))
        (workdir / "preexisting.txt").write_text("old content")
        commit_op.commit(MutRepo(str(workdir)), "pre", who="test")

        result = connect_op.connect(URL, workdir=str(workdir))

        assert result["initialized"] is False, (
            "must not report 'initialized' when .mut/ already existed"
        )
        assert result["status"] == "connected"

    def test_writes_server_url_to_config(self, workdir, fake_client):
        connect_op.connect(URL, workdir=str(workdir))

        config = json.loads((workdir / ".mut" / "config.json").read_text())
        assert config["server"] == URL
        assert config["credential"] == "test-key-abc"

    def test_imports_existing_files_when_present(self, workdir, fake_client):
        (workdir / "src").mkdir()
        (workdir / "src" / "main.py").write_text("print('hi')")
        (workdir / "README.md").write_text("# project")

        result = connect_op.connect(URL, workdir=str(workdir))

        assert result["imported"] is True
        assert result["snapshot_id"] == 1
        assert result["pushed"] == 1
        assert fake_client.last_push is not None
        assert "snapshots" in fake_client.last_push
        # the push must carry exactly the snapshot we just created
        assert len(fake_client.last_push["snapshots"]) == 1

    def test_no_op_commit_when_workdir_empty(self, workdir, fake_client):
        result = connect_op.connect(URL, workdir=str(workdir))

        assert result["imported"] is False
        assert result["snapshot_id"] is None
        assert result["pushed"] == 0


# ── Credential plumbing ──────────────────────────────────────────────────

class TestCredentialHandling:
    def test_extracts_credential_from_url_when_omitted(self, workdir, fake_client):
        connect_op.connect(URL, workdir=str(workdir))

        assert fake_client.last_instance.credential == "test-key-abc"

    def test_explicit_credential_overrides_url(self, workdir, fake_client):
        connect_op.connect(
            URL, credential="explicit-override", workdir=str(workdir),
        )

        assert fake_client.last_instance.credential == "explicit-override"


# ── Error propagation ────────────────────────────────────────────────────

class TestErrorPropagation:
    def test_raises_when_server_unreachable(self, workdir, monkeypatch):
        class _BrokenClient:
            def __init__(self, *_, **__):
                pass

            def clone(self):
                raise ConnectionError("server down")

        monkeypatch.setattr(link_access_op, "MutClient", _BrokenClient)

        with pytest.raises(MutError, match="Cannot connect to server"):
            connect_op.connect(URL, workdir=str(workdir))

    def test_raises_when_push_fails(self, workdir, monkeypatch):
        # link_access succeeds, but push blows up
        monkeypatch.setattr(link_access_op, "MutClient", _FakeMutClient)

        class _PushFailClient(_FakeMutClient):
            def negotiate(self, *_, **__):
                return {
                    "missing": [],
                    "server_head_commit_id": "srv-head-0",
                    "remote_head_recognized": True,
                }

            def push(self, *_, **__):
                raise RuntimeError("server rejected push")

        monkeypatch.setattr(push_op, "MutClient", _PushFailClient)

        (workdir / "data.txt").write_text("payload")

        with pytest.raises(MutError, match="connect: push failed"):
            connect_op.connect(URL, workdir=str(workdir))

    def test_does_not_silently_overwrite_existing_repo(self, workdir, fake_client):
        """Smoke-test: even if connect runs against a folder that already had
        a different remote configured, we end up with the new server URL —
        no half-state where the local config still points to the old place."""
        init_op.init(str(workdir))
        old_config = workdir / ".mut" / "config.json"
        old_config.write_text(json.dumps({
            "version": 1, "server": "https://stale.example/old",
            "credential": "stale-key",
        }))

        connect_op.connect(URL, workdir=str(workdir))

        cfg = json.loads(old_config.read_text())
        assert cfg["server"] == URL
        assert cfg["credential"] == "test-key-abc"
