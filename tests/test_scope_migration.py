"""Tests for scope split/merge and history migration."""

import pytest

from mut.server.scope_manager import ScopeManager, FileSystemScopeBackend
from mut.server.history import HistoryManager, FileSystemHistoryBackend
from mut.foundation.fs import write_text


@pytest.fixture
def scopes(tmp_path):
    d = tmp_path / "scopes"
    d.mkdir()
    sm = ScopeManager(FileSystemScopeBackend(d))
    sm.add("scope-docs", "/docs/")
    sm.add("scope-src", "/src/")
    return sm


@pytest.fixture
def history(tmp_path):
    d = tmp_path / "history"
    d.mkdir()
    write_text(d / "latest", "0")
    write_text(d / "root", "")
    return HistoryManager(FileSystemHistoryBackend(d))


# ── ScopeManager split/merge ──────────────────────────────────

class TestScopeSplit:
    def test_split_scope(self, scopes):
        new_scopes = scopes.split_scope("scope-docs", [
            {"id": "scope-docs-internal", "path": "/docs/internal/"},
            {"id": "scope-docs-public", "path": "/docs/public/"},
        ])
        assert len(new_scopes) == 2
        assert scopes.get_by_id("scope-docs") is None  # old scope deleted
        assert scopes.get_by_id("scope-docs-internal")["path"] == "/docs/internal/"
        assert scopes.get_by_id("scope-docs-public")["path"] == "/docs/public/"

    def test_split_nonexistent_scope_raises(self, scopes):
        with pytest.raises(ValueError, match="not found"):
            scopes.split_scope("scope-nope", [
                {"id": "x", "path": "/x/"},
            ])

    def test_merge_scopes(self, scopes):
        scopes.add("scope-fe", "/src/frontend/")
        scopes.add("scope-be", "/src/backend/")

        merged = scopes.merge_scopes(
            ["scope-fe", "scope-be"],
            "scope-all-src", "/src/",
        )
        assert merged["path"] == "/src/"
        assert scopes.get_by_id("scope-fe") is None
        assert scopes.get_by_id("scope-be") is None
        assert scopes.get_by_id("scope-all-src")["path"] == "/src/"

    def test_merge_nonexistent_raises(self, scopes):
        with pytest.raises(ValueError, match="not found"):
            scopes.merge_scopes(["scope-nope"], "new-id", "/new/")


class TestFindByPathPrefix:
    def test_find_by_prefix(self, scopes):
        scopes.add("scope-docs-internal", "/docs/internal/")
        results = scopes.find_by_path_prefix("/docs/")
        paths = [s["path"] for s in results]
        assert "/docs/" in paths
        assert "/docs/internal/" in paths
        assert "/src/" not in paths

    def test_find_all_with_empty_prefix(self, scopes):
        results = scopes.find_by_path_prefix("")
        assert len(results) >= 2  # docs + src


# ── History migration ──────────────────────────────────────────

class TestHistoryMigration:
    def _seed_history(self, history):
        """Create history entries under /docs/ scope."""
        history.set_latest_version(0)
        for v in range(1, 4):
            changes = [
                {"path": f"docs/internal/secret-{v}.md", "action": "add"},
                {"path": f"docs/public/readme-{v}.md", "action": "add"},
            ]
            history.record(v, f"agent-{v}", f"commit {v}", "docs", changes,
                           root_hash=f"hash-{v}")
            history.set_latest_version(v)

    def test_migrate_splits_history(self, history):
        self._seed_history(history)

        count = history.migrate_scope(
            "docs",
            {
                "docs/internal": "docs/internal",
                "docs/public": "docs/public",
            },
            fallback_scope="/",
        )
        assert count == 3  # all 3 entries migrated

        # Each entry should now be attributed to the sub-scope with more matches
        # Since each entry has one internal + one public change,
        # the first matching prefix wins
        for v in range(1, 4):
            entry = history.get_entry(v)
            assert entry["scope"] in ("docs/internal", "docs/public")

    def test_migrate_no_match_goes_to_fallback(self, history):
        history.set_latest_version(0)
        history.record(1, "a", "msg", "docs",
                       [{"path": "docs/other.txt", "action": "add"}],
                       root_hash="h1")
        history.set_latest_version(1)

        count = history.migrate_scope(
            "docs",
            {"docs/internal": "docs/internal"},
            fallback_scope="root",
        )
        assert count == 1
        entry = history.get_entry(1)
        assert entry["scope"] == "root"

    def test_migrate_ignores_other_scopes(self, history):
        self._seed_history(history)
        # Add an entry under a different scope
        history.record(4, "x", "other scope", "src",
                       [{"path": "src/main.py", "action": "add"}],
                       root_hash="h4")
        history.set_latest_version(4)

        count = history.migrate_scope(
            "docs",
            {"docs/internal": "docs/internal"},
            fallback_scope="/",
        )
        # Only docs entries migrated, not src
        assert count == 3
        assert history.get_entry(4)["scope"] == "src"  # unchanged

    def test_migrate_empty_changes_uses_fallback(self, history):
        history.set_latest_version(0)
        history.record(1, "a", "empty", "docs", [], root_hash="h")
        history.set_latest_version(1)

        history.migrate_scope("docs", {"docs/x": "docs/x"}, fallback_scope="/")
        assert history.get_entry(1)["scope"] == "/"
