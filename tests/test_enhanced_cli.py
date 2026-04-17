"""Tests for enhanced mut status and log output."""

import pytest
from pathlib import Path

from mut.ops.init_op import init
from mut.ops.repo import MutRepo
from mut.ops import commit_op
from mut.foundation.config import load_config, save_config, REMOTE_HEAD_FILE
from mut.foundation.fs import write_text


@pytest.fixture
def workspace(tmp_path):
    """Initialize a mut workspace with some files."""
    wd = tmp_path / "project"
    wd.mkdir()
    init(str(wd))
    (wd / "readme.md").write_text("# Hello")
    (wd / "data.json").write_text('{"key": "value"}')
    return wd


class TestMutStatus:
    def test_status_clean(self, workspace):
        repo = MutRepo(str(workspace))
        commit_op.commit(repo, "initial", "alice")

        from mut.ops import status_op
        result = status_op.status(repo)
        assert result["changes"] == []
        assert result["unpushed"] >= 0

    def test_status_with_changes(self, workspace):
        repo = MutRepo(str(workspace))
        commit_op.commit(repo, "initial", "alice")
        (workspace / "new.txt").write_text("new file")

        from mut.ops import status_op
        result = status_op.status(repo)
        assert len(result["changes"]) > 0
        paths = [c["path"] for c in result["changes"]]
        assert "new.txt" in paths

    def test_status_shows_server_url(self, workspace, capsys, monkeypatch):
        repo = MutRepo(str(workspace))
        commit_op.commit(repo, "initial", "alice")

        # Set server URL
        config = load_config(repo.mut_root)
        config["server"] = "https://api.puppyone.com/mut/ap_test123"
        save_config(repo.mut_root, config)

        # Write REMOTE_HEAD (now a hash-based commit id, not an int)
        write_text(repo.mut_root / REMOTE_HEAD_FILE, "a1b2c3d4e5f60718")

        # Run cmd_status in the workspace directory
        monkeypatch.chdir(workspace)

        from mut.cli import cmd_status
        import argparse
        cmd_status(argparse.Namespace())

        captured = capsys.readouterr()
        assert "api.puppyone.com" in captured.out
        # cli displays the full 16-hex commit id (no Git-style truncation —
        # 16 hex is short enough already)
        assert "remote commit: a1b2c3d4e5f60718" in captured.out

    def test_status_shows_unpushed_count(self, workspace):
        repo = MutRepo(str(workspace))
        commit_op.commit(repo, "first", "alice")
        (workspace / "x.txt").write_text("x")
        commit_op.commit(repo, "second", "alice")

        from mut.ops import status_op
        result = status_op.status(repo)
        assert result["unpushed"] >= 1


class TestMutLog:
    def test_log_shows_entries(self, workspace):
        repo = MutRepo(str(workspace))
        commit_op.commit(repo, "initial", "alice")
        (workspace / "new.txt").write_text("data")
        commit_op.commit(repo, "add new file", "bob")

        from mut.ops import log_op
        entries = log_op.log(repo)
        assert len(entries) == 2
        # log returns oldest first
        whos = {e["who"] for e in entries}
        assert "alice" in whos
        assert "bob" in whos

    def test_log_format(self, workspace, capsys, monkeypatch):
        repo = MutRepo(str(workspace))
        commit_op.commit(repo, "first commit", "test-user")

        monkeypatch.chdir(workspace)
        from mut.cli import cmd_log
        import argparse
        cmd_log(argparse.Namespace())

        captured = capsys.readouterr()
        assert "#1" in captured.out
        assert "first commit" in captured.out
        assert "test-user" in captured.out
