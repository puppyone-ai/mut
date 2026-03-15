"""Tests for core/ignore.py — IgnoreRules."""

import pytest

from mut.core.ignore import IgnoreRules
from mut.foundation.config import BUILTIN_IGNORE


class TestIgnoreRules:
    def test_builtin_ignores(self, tmp_path):
        rules = IgnoreRules(tmp_path)
        for name in [".mut", ".git", "__pycache__", "node_modules", ".DS_Store"]:
            assert rules.should_ignore(name), f"{name} should be ignored"

    def test_regular_files_not_ignored(self, tmp_path):
        rules = IgnoreRules(tmp_path)
        for name in ["main.py", "README.md", "src", "config.json"]:
            assert not rules.should_ignore(name), f"{name} should not be ignored"

    def test_custom_mutignore(self, tmp_path):
        (tmp_path / ".mutignore").write_text("build\n*.pyc\n# comment\n\n")
        rules = IgnoreRules(tmp_path)
        assert rules.should_ignore("build")
        assert rules.should_ignore("*.pyc")
        assert not rules.should_ignore("# comment")  # comments are not patterns

    def test_mutignore_file_missing(self, tmp_path):
        rules = IgnoreRules(tmp_path)
        # Should still work with builtins only
        assert rules.should_ignore(".git")
        assert not rules.should_ignore("hello.txt")

    def test_patterns_cached(self, tmp_path):
        rules = IgnoreRules(tmp_path)
        rules.should_ignore("test")  # triggers _load
        assert rules._patterns is not None
        patterns = rules._patterns
        rules.should_ignore("test2")  # should reuse cached
        assert rules._patterns is patterns
