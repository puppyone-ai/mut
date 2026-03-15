"""Tests for ops/ — init, commit, status, checkout, log, diff, show, tree, stats.

All tests use local-only mode (no server) to test the operations independently.
"""

import pytest

from mut.ops.repo import MutRepo
from mut.ops import (
    init_op, commit_op, status_op, log_op,
    diff_op, checkout_op, show_op, tree_op, stats_op,
)
from mut.foundation.error import NotARepoError, SnapshotNotFoundError, MutError


@pytest.fixture
def workdir(tmp_path):
    d = tmp_path / "project"
    d.mkdir()
    return d


@pytest.fixture
def repo(workdir):
    return init_op.init(str(workdir))


# ── init ───────────────────────────────────────

class TestInit:
    def test_creates_mut_dir(self, workdir):
        repo = init_op.init(str(workdir))
        assert (workdir / ".mut").is_dir()
        assert (workdir / ".mut" / "objects").is_dir()

    def test_double_init_fails(self, workdir):
        init_op.init(str(workdir))
        with pytest.raises(FileExistsError):
            init_op.init(str(workdir))

    def test_returns_repo(self, workdir):
        repo = init_op.init(str(workdir))
        assert isinstance(repo, MutRepo)


# ── commit ─────────────────────────────────────

class TestCommit:
    def test_commit_empty_dir(self, repo, workdir):
        snap = commit_op.commit(repo, "empty commit")
        assert snap is not None
        assert snap["id"] == 1
        assert snap["message"] == "empty commit"

    def test_commit_with_files(self, repo, workdir):
        (workdir / "hello.txt").write_text("hello world")
        snap = commit_op.commit(repo, "add hello")
        assert snap is not None
        assert snap["root"]

    def test_commit_no_change_returns_none(self, repo, workdir):
        (workdir / "hello.txt").write_text("hello")
        commit_op.commit(repo, "first")
        result = commit_op.commit(repo, "same state")
        assert result is None

    def test_commit_detects_modification(self, repo, workdir):
        (workdir / "f.txt").write_text("v1")
        commit_op.commit(repo, "v1")
        (workdir / "f.txt").write_text("v2")
        snap = commit_op.commit(repo, "v2")
        assert snap is not None
        assert snap["id"] == 2

    def test_commit_custom_who(self, repo, workdir):
        (workdir / "f.txt").write_text("x")
        snap = commit_op.commit(repo, "msg", who="agent-B")
        assert snap["who"] == "agent-B"


# ── status ─────────────────────────────────────

class TestStatus:
    def test_clean_after_commit(self, repo, workdir):
        (workdir / "f.txt").write_text("x")
        commit_op.commit(repo, "init")
        result = status_op.status(repo)
        assert result["changes"] == []

    def test_detects_new_file(self, repo, workdir):
        commit_op.commit(repo, "empty")
        (workdir / "new.txt").write_text("new")
        result = status_op.status(repo)
        ops = {c["path"]: c["op"] for c in result["changes"]}
        assert "new.txt" in ops
        assert ops["new.txt"] == "added"

    def test_detects_modification(self, repo, workdir):
        (workdir / "f.txt").write_text("v1")
        commit_op.commit(repo, "v1")
        (workdir / "f.txt").write_text("v2")
        result = status_op.status(repo)
        ops = {c["path"]: c["op"] for c in result["changes"]}
        assert ops["f.txt"] == "modified"

    def test_detects_deletion(self, repo, workdir):
        (workdir / "f.txt").write_text("x")
        commit_op.commit(repo, "add")
        (workdir / "f.txt").unlink()
        result = status_op.status(repo)
        ops = {c["path"]: c["op"] for c in result["changes"]}
        assert ops["f.txt"] == "deleted"

    def test_unpushed_count(self, repo, workdir):
        (workdir / "f.txt").write_text("x")
        commit_op.commit(repo, "one")
        result = status_op.status(repo)
        assert result["unpushed"] == 1

    def test_new_repo_no_snapshots(self, repo, workdir):
        result = status_op.status(repo)
        assert len(result["changes"]) > 0  # "new repo" message


# ── log ────────────────────────────────────────

class TestLog:
    def test_empty_log(self, repo):
        entries = log_op.log(repo)
        assert entries == []

    def test_log_ordering(self, repo, workdir):
        (workdir / "a.txt").write_text("a")
        commit_op.commit(repo, "first")
        (workdir / "b.txt").write_text("b")
        commit_op.commit(repo, "second")
        entries = log_op.log(repo)
        assert len(entries) == 2
        assert entries[0]["id"] == 2  # newest first
        assert entries[1]["id"] == 1


# ── diff ───────────────────────────────────────

class TestDiff:
    def test_diff_same_snapshot(self, repo, workdir):
        (workdir / "f.txt").write_text("x")
        commit_op.commit(repo, "one")
        changes = diff_op.diff(repo, 1, 1)
        assert changes == []

    def test_diff_detects_changes(self, repo, workdir):
        (workdir / "f.txt").write_text("v1")
        commit_op.commit(repo, "v1")
        (workdir / "f.txt").write_text("v2")
        commit_op.commit(repo, "v2")
        changes = diff_op.diff(repo, 1, 2)
        assert len(changes) == 1
        assert changes[0]["op"] == "modified"

    def test_diff_nonexistent_snapshot(self, repo, workdir):
        (workdir / "f.txt").write_text("x")
        commit_op.commit(repo, "one")
        with pytest.raises(SnapshotNotFoundError):
            diff_op.diff(repo, 1, 99)


# ── checkout ───────────────────────────────────

class TestCheckout:
    def test_restore_to_earlier_state(self, repo, workdir):
        (workdir / "f.txt").write_text("v1")
        commit_op.commit(repo, "v1")
        (workdir / "f.txt").write_text("v2")
        commit_op.commit(repo, "v2")

        checkout_op.checkout(repo, 1)
        assert (workdir / "f.txt").read_text() == "v1"

    def test_checkout_removes_new_files(self, repo, workdir):
        commit_op.commit(repo, "empty")
        (workdir / "extra.txt").write_text("extra")
        commit_op.commit(repo, "added")
        checkout_op.checkout(repo, 1)
        assert not (workdir / "extra.txt").exists()

    def test_checkout_nonexistent(self, repo, workdir):
        with pytest.raises(SnapshotNotFoundError):
            checkout_op.checkout(repo, 99)


# ── show ───────────────────────────────────────

class TestShow:
    def test_show_file(self, repo, workdir):
        (workdir / "hello.py").write_text("print('hi')")
        commit_op.commit(repo, "add file")
        content = show_op.show(repo, 1, "hello.py")
        assert content == "print('hi')"

    def test_show_nested_file(self, repo, workdir):
        sub = workdir / "src"
        sub.mkdir()
        (sub / "main.py").write_text("main()")
        commit_op.commit(repo, "add nested")
        content = show_op.show(repo, 1, "src/main.py")
        assert content == "main()"

    def test_show_directory(self, repo, workdir):
        sub = workdir / "sub"
        sub.mkdir()
        (sub / "a.txt").write_bytes(b"aaa")
        commit_op.commit(repo, "add dir")
        result = show_op.show(repo, 1, "sub")
        assert "a.txt" in result  # JSON representation of tree

    def test_show_nonexistent_path(self, repo, workdir):
        (workdir / "f.txt").write_text("x")
        commit_op.commit(repo, "add")
        with pytest.raises(MutError, match="not found"):
            show_op.show(repo, 1, "nonexistent.txt")

    def test_show_nonexistent_snapshot(self, repo, workdir):
        with pytest.raises(SnapshotNotFoundError):
            show_op.show(repo, 99, "f.txt")


# ── tree ───────────────────────────────────────

class TestTree:
    def test_tree_output(self, repo, workdir):
        (workdir / "a.txt").write_bytes(b"aaa")
        sub = workdir / "sub"
        sub.mkdir()
        (sub / "b.txt").write_bytes(b"bbb")
        commit_op.commit(repo, "add tree")
        output = tree_op.tree(repo, 1)
        assert "a.txt" in output
        assert "sub" in output
        assert "b.txt" in output

    def test_tree_nonexistent(self, repo, workdir):
        with pytest.raises(SnapshotNotFoundError):
            tree_op.tree(repo, 99)


# ── stats ──────────────────────────────────────

class TestStats:
    def test_empty_repo_stats(self, repo):
        s = stats_op.stats(repo)
        assert s["objects"] == 0
        assert s["bytes"] == 0
        assert s["snapshots"] == 0

    def test_stats_after_commits(self, repo, workdir):
        (workdir / "f.txt").write_bytes(b"hello")
        commit_op.commit(repo, "first")
        s = stats_op.stats(repo)
        assert s["objects"] > 0
        assert s["bytes"] > 0
        assert s["snapshots"] == 1

    def test_stats_increments(self, repo, workdir):
        (workdir / "a.txt").write_bytes(b"aaa")
        commit_op.commit(repo, "one")
        s1 = stats_op.stats(repo)

        (workdir / "b.txt").write_bytes(b"bbb")
        commit_op.commit(repo, "two")
        s2 = stats_op.stats(repo)

        assert s2["objects"] > s1["objects"]
        assert s2["snapshots"] == s1["snapshots"] + 1


# ── MutRepo ────────────────────────────────────

class TestMutRepo:
    def test_check_init_fails_outside_repo(self, tmp_path):
        repo = MutRepo(str(tmp_path))
        with pytest.raises(NotARepoError):
            repo.check_init()
