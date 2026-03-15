"""Tests for core/manifest.py — flat path-hash map generation and I/O."""

import pytest

from mut.core.manifest import generate, load, save
from mut.core.ignore import IgnoreRules
from mut.foundation.hash import hash_bytes


class TestManifestGenerate:
    def test_flat_directory(self, tmp_path):
        (tmp_path / "a.txt").write_bytes(b"aaa")
        (tmp_path / "b.txt").write_bytes(b"bbb")
        ignore = IgnoreRules(tmp_path)
        m = generate(tmp_path, ignore)
        assert len(m) == 2
        assert m["a.txt"] == hash_bytes(b"aaa")
        assert m["b.txt"] == hash_bytes(b"bbb")

    def test_nested_directory(self, tmp_path):
        sub = tmp_path / "sub"
        sub.mkdir()
        (sub / "c.txt").write_bytes(b"ccc")
        ignore = IgnoreRules(tmp_path)
        m = generate(tmp_path, ignore)
        assert "sub/c.txt" in m

    def test_ignores_builtins(self, tmp_path):
        (tmp_path / "good.txt").write_bytes(b"ok")
        pycache = tmp_path / "__pycache__"
        pycache.mkdir()
        (pycache / "cache.pyc").write_bytes(b"cached")
        ignore = IgnoreRules(tmp_path)
        m = generate(tmp_path, ignore)
        assert "good.txt" in m
        assert "__pycache__/cache.pyc" not in m

    def test_empty_directory(self, tmp_path):
        ignore = IgnoreRules(tmp_path)
        m = generate(tmp_path, ignore)
        assert m == {}


class TestManifestIO:
    def test_save_and_load(self, tmp_path):
        manifest = {"file.txt": "abcdef1234567890"}
        save(tmp_path, manifest)
        loaded = load(tmp_path)
        assert loaded == manifest

    def test_load_missing(self, tmp_path):
        assert load(tmp_path) == {}
