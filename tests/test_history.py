"""Tests for server/history.py — HistoryManager."""

import pytest

from mut.server.history import HistoryManager, _scopes_overlap, _redact_for_scope
from mut.foundation.fs import write_text


@pytest.fixture
def history(tmp_path):
    d = tmp_path / "history"
    d.mkdir()
    write_text(d / "latest", "0")
    write_text(d / "root", "")
    return HistoryManager(d)


class TestHistoryManager:
    def test_initial_state(self, history):
        assert history.get_latest_version() == 0
        assert history.get_root_hash() == ""

    def test_set_version(self, history):
        history.set_latest_version(5)
        assert history.get_latest_version() == 5

    def test_set_root_hash(self, history):
        history.set_root_hash("abc123")
        assert history.get_root_hash() == "abc123"

    def test_record_and_get(self, history):
        history.record(1, "agent-A", "first push", "/src/",
                       [{"path": "src/main.py", "action": "add"}])
        entry = history.get_entry(1)
        assert entry is not None
        assert entry["who"] == "agent-A"
        assert entry["message"] == "first push"
        assert entry["scope"] == "/src/"
        assert len(entry["changes"]) == 1

    def test_record_with_conflicts(self, history):
        from mut.core.merge import ConflictRecord
        conflicts = [ConflictRecord(path="f.txt", strategy="lww", detail="both changed", kept="theirs")]
        history.record(1, "agent-A", "push", "/src/", [], conflicts=conflicts)
        entry = history.get_entry(1)
        assert "conflicts" in entry
        assert entry["conflicts"][0]["strategy"] == "lww"

    def test_get_nonexistent(self, history):
        assert history.get_entry(999) is None

    def test_get_since(self, history):
        history.record(1, "a", "v1", "/src/", [])
        history.record(2, "b", "v2", "/docs/", [])
        history.record(3, "c", "v3", "/src/", [])
        history.set_latest_version(3)

        all_entries = history.get_since(0)
        assert len(all_entries) == 3

        since_1 = history.get_since(1)
        assert len(since_1) == 2

    def test_get_since_scope_filter(self, history):
        history.record(1, "a", "v1", "/src/", [{"path": "src/main.py", "action": "add"}])
        history.record(2, "b", "v2", "/docs/", [{"path": "docs/readme.md", "action": "add"}])
        history.set_latest_version(2)

        src_entries = history.get_since(0, scope_path="/src/")
        assert len(src_entries) == 1
        assert src_entries[0]["who"] == "a"

    def test_redaction(self, history):
        history.record(1, "a", "push", "/",
                       [{"path": "src/main.py", "action": "add"},
                        {"path": "docs/readme.md", "action": "add"}])
        history.set_latest_version(1)

        entries = history.get_since(0, scope_path="/src/")
        assert len(entries) == 1
        changes = entries[0]["changes"]
        paths = [c["path"] for c in changes]
        assert "src/main.py" in paths
        assert "docs/readme.md" not in paths


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
            "id": 1,
            "changes": [
                {"path": "src/main.py", "action": "add"},
                {"path": "docs/readme.md", "action": "add"},
            ],
        }
        redacted = _redact_for_scope(entry, "src")
        assert len(redacted["changes"]) == 1
        assert redacted["changes"][0]["path"] == "src/main.py"

    def test_no_changes_key(self):
        entry = {"id": 1}
        assert _redact_for_scope(entry, "src") is entry  # same object
