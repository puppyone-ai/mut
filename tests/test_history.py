"""Tests for server/history.py — HistoryManager (commit-id identity model)."""

import pytest

from mut.server.history import (
    HistoryManager, FileSystemHistoryBackend,
    _scopes_overlap, _redact_for_scope,
)
from mut.foundation.fs import write_text


_CID_1 = "a1b2c3d4e5f60718"
_CID_2 = "1122334455667788"
_CID_3 = "9988776655443322"


@pytest.fixture
def history(tmp_path):
    d = tmp_path / "history"
    d.mkdir()
    write_text(d / "latest", "")
    write_text(d / "root", "")
    return HistoryManager(FileSystemHistoryBackend(d))


class TestHistoryManager:
    def test_initial_state(self, history):
        assert history.get_head_commit_id() == ""
        assert history.get_root_hash() == ""

    def test_set_head_commit_id(self, history):
        history.set_head_commit_id(_CID_1)
        assert history.get_head_commit_id() == _CID_1

    def test_set_root_hash(self, history):
        history.set_root_hash("abc123")
        assert history.get_root_hash() == "abc123"

    def test_record_and_get(self, history):
        history.record(_CID_1, "agent-A", "first push", "/src/",
                       [{"path": "src/main.py", "action": "add"}])
        entry = history.get_entry(_CID_1)
        assert entry is not None
        assert entry["who"] == "agent-A"
        assert entry["message"] == "first push"
        assert entry["scope"] == "/src/"
        assert entry["commit_id"] == _CID_1
        assert len(entry["changes"]) == 1

    def test_record_with_conflicts(self, history):
        from mut.core.merge import ConflictRecord
        conflicts = [ConflictRecord(path="f.txt", strategy="lww",
                                    detail="both changed", kept="theirs")]
        history.record(_CID_1, "agent-A", "push", "/src/", [],
                       conflicts=conflicts)
        entry = history.get_entry(_CID_1)
        assert "conflicts" in entry
        assert entry["conflicts"][0]["strategy"] == "lww"

    def test_get_nonexistent(self, history):
        assert history.get_entry("deadbeefdeadbeef") is None

    def test_get_since(self, history):
        # Explicit timestamps guarantee a deterministic linear order
        # that does NOT depend on commit-id lexicographic order.
        history.record(_CID_1, "a", "v1", "/src/", [],
                       created_at_iso="2026-04-16T12:00:01+00:00")
        history.record(_CID_2, "b", "v2", "/docs/", [],
                       created_at_iso="2026-04-16T12:00:02+00:00")
        history.record(_CID_3, "c", "v3", "/src/", [],
                       created_at_iso="2026-04-16T12:00:03+00:00")
        history.set_head_commit_id(_CID_3)

        all_entries = history.get_since("")
        assert len(all_entries) == 3

        since_first = history.get_since(_CID_1)
        assert len(since_first) == 2

    def test_get_since_unknown_commit_id_fails_open(self, history):
        """Regression: unknown since_commit_id must NOT silently return []
        (previously caused clients to miss history after cross-scope or
        pruned commits).
        """
        history.record(_CID_1, "a", "v1", "/src/", [],
                       created_at_iso="2026-04-16T12:00:01+00:00")
        history.record(_CID_2, "b", "v2", "/docs/", [],
                       created_at_iso="2026-04-16T12:00:02+00:00")

        unknown = "deadbeefdeadbeef"
        entries = history.get_since(unknown)
        assert len(entries) == 2, (
            "unknown since_commit_id should fall back to returning every "
            "known entry, not an empty list"
        )

    def test_get_since_scope_filter(self, history):
        history.record(_CID_1, "a", "v1", "/src/",
                       [{"path": "src/main.py", "action": "add"}],
                       created_at_iso="2026-04-16T12:00:01+00:00")
        history.record(_CID_2, "b", "v2", "/docs/",
                       [{"path": "docs/readme.md", "action": "add"}],
                       created_at_iso="2026-04-16T12:00:02+00:00")
        history.set_head_commit_id(_CID_2)

        src_entries = history.get_since("", scope_path="/src/")
        assert len(src_entries) == 1
        assert src_entries[0]["who"] == "a"

    def test_redaction(self, history):
        history.record(_CID_1, "a", "push", "/",
                       [{"path": "src/main.py", "action": "add"},
                        {"path": "docs/readme.md", "action": "add"}],
                       created_at_iso="2026-04-16T12:00:01+00:00")
        history.set_head_commit_id(_CID_1)

        entries = history.get_since("", scope_path="/src/")
        assert len(entries) == 1
        changes = entries[0]["changes"]
        paths = [c["path"] for c in changes]
        assert "src/main.py" in paths
        assert "docs/readme.md" not in paths


class TestComputeCommitId:
    def test_deterministic(self):
        args = dict(scope_path="/docs/", scope_hash="aabbccddeeff0011",
                    created_at_iso="2026-04-16T12:00:00+00:00", who="alice")
        cid1 = HistoryManager.compute_commit_id(**args)
        cid2 = HistoryManager.compute_commit_id(**args)
        assert cid1 == cid2

    def test_length_is_16_hex(self):
        cid = HistoryManager.compute_commit_id(
            scope_path="/", scope_hash="0" * 16,
            created_at_iso="2026-04-16T12:00:00+00:00", who="x",
        )
        assert len(cid) == 16
        assert all(c in "0123456789abcdef" for c in cid)

    def test_changing_who_changes_id(self):
        base = dict(scope_path="/", scope_hash="0" * 16,
                    created_at_iso="2026-04-16T12:00:00+00:00")
        a = HistoryManager.compute_commit_id(who="alice", **base)
        b = HistoryManager.compute_commit_id(who="bob", **base)
        assert a != b

    def test_changing_scope_hash_changes_id(self):
        base = dict(scope_path="/", who="alice",
                    created_at_iso="2026-04-16T12:00:00+00:00")
        a = HistoryManager.compute_commit_id(scope_hash="a" * 16, **base)
        b = HistoryManager.compute_commit_id(scope_hash="b" * 16, **base)
        assert a != b


class TestScopesOverlap:
    def test_same_scope(self):
        assert _scopes_overlap("/src/", "src")

    def test_child_scope(self):
        assert _scopes_overlap("/src/", "src/components")

    def test_parent_scope(self):
        assert _scopes_overlap("/src/components/", "src")

    def test_disjoint_scopes(self):
        assert not _scopes_overlap("/src/", "docs")

    def test_empty_entry_scope(self):
        assert _scopes_overlap("/", "src")

    def test_empty_requesting_scope(self):
        assert _scopes_overlap("/src/", "")


class TestRedactForScope:
    def test_filters_changes(self):
        entry = {
            "commit_id": _CID_1,
            "changes": [
                {"path": "src/main.py", "action": "add"},
                {"path": "docs/readme.md", "action": "add"},
            ],
        }
        redacted = _redact_for_scope(entry, "src")
        assert len(redacted["changes"]) == 1
        assert redacted["changes"][0]["path"] == "src/main.py"

    def test_no_changes_key(self):
        entry = {"commit_id": _CID_1}
        assert _redact_for_scope(entry, "src") == entry
