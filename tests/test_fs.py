"""Tests for foundation/fs.py — atomic writes, locks, path safety."""

import os
import pytest

from mut.foundation.fs import (
    read_json, write_json, read_text, write_text,
    atomic_write, mkdir_p, rmtree,
    lock_acquire, lock_release,
    is_safe_path,
)


class TestAtomicWrite:
    def test_basic_write(self, tmp_path):
        p = tmp_path / "test.txt"
        atomic_write(p, b"hello")
        assert p.read_bytes() == b"hello"

    def test_creates_parent_dirs(self, tmp_path):
        p = tmp_path / "deep" / "nested" / "file.txt"
        atomic_write(p, b"data")
        assert p.read_bytes() == b"data"

    def test_overwrite(self, tmp_path):
        p = tmp_path / "test.txt"
        atomic_write(p, b"v1")
        atomic_write(p, b"v2")
        assert p.read_bytes() == b"v2"


class TestJsonIO:
    def test_write_and_read(self, tmp_path):
        p = tmp_path / "data.json"
        write_json(p, {"key": "value", "num": 42})
        data = read_json(p)
        assert data == {"key": "value", "num": 42}

    def test_unicode(self, tmp_path):
        p = tmp_path / "unicode.json"
        write_json(p, {"emoji": "中文测试"})
        data = read_json(p)
        assert data["emoji"] == "中文测试"


class TestTextIO:
    def test_write_and_read(self, tmp_path):
        p = tmp_path / "text.txt"
        write_text(p, "hello world")
        assert read_text(p) == "hello world"

    def test_strips_whitespace(self, tmp_path):
        p = tmp_path / "text.txt"
        p.write_text("  hello  \n", encoding="utf-8")
        assert read_text(p) == "hello"


class TestMkdirP:
    def test_creates_nested(self, tmp_path):
        p = tmp_path / "a" / "b" / "c"
        mkdir_p(p)
        assert p.is_dir()

    def test_idempotent(self, tmp_path):
        p = tmp_path / "already"
        p.mkdir()
        mkdir_p(p)  # should not raise
        assert p.is_dir()


class TestRmtree:
    def test_remove_dir(self, tmp_path):
        d = tmp_path / "dir"
        d.mkdir()
        (d / "file.txt").write_text("x")
        rmtree(d)
        assert not d.exists()

    def test_remove_file(self, tmp_path):
        f = tmp_path / "file.txt"
        f.write_text("x")
        rmtree(f)
        assert not f.exists()

    def test_nonexistent_ok(self, tmp_path):
        rmtree(tmp_path / "nonexistent")  # should not raise


class TestFileLock:
    def test_acquire_and_release(self, tmp_path):
        lock = tmp_path / "test.lock"
        assert lock_acquire(lock)
        assert lock.exists()
        lock_release(lock)
        assert not lock.exists()

    def test_double_acquire_fails(self, tmp_path):
        lock = tmp_path / "test.lock"
        assert lock_acquire(lock)
        # Second acquire should fail (same process, lock has our PID)
        # Since our PID is alive, it should return False
        assert not lock_acquire(lock)
        lock_release(lock)

    def test_release_nonexistent(self, tmp_path):
        lock = tmp_path / "nonexistent.lock"
        lock_release(lock)  # should not raise


class TestIsSafePath:
    def test_safe_path(self, tmp_path):
        assert is_safe_path(tmp_path, tmp_path / "sub" / "file.txt")

    def test_unsafe_traversal(self, tmp_path):
        assert not is_safe_path(tmp_path, tmp_path / ".." / "escaped.txt")

    def test_exact_base(self, tmp_path):
        assert is_safe_path(tmp_path, tmp_path)
