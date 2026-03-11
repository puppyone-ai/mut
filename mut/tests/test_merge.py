"""Unit tests for core/merge.py — three-way merge engine."""

import json
import pytest

from mut.core.merge import three_way_merge, merge_file_sets


def test_identical():
    r = three_way_merge(b"base", b"same", b"same")
    assert r.content == b"same"
    assert r.strategy == "identical"
    assert not r.conflicts


def test_ours_only():
    r = three_way_merge(b"base", b"base", b"changed")
    assert r.content == b"changed"
    assert r.strategy == "theirs_only"


def test_theirs_only():
    r = three_way_merge(b"base", b"changed", b"base")
    assert r.content == b"changed"
    assert r.strategy == "ours_only"


def test_line_merge_non_overlapping():
    base = b"line1\nline2\nline3\nline4\nline5\n"
    ours = b"LINE1\nline2\nline3\nline4\nline5\n"
    theirs = b"line1\nline2\nline3\nline4\nLINE5\n"
    r = three_way_merge(base, ours, theirs)
    assert r.strategy == "line_merge"
    assert b"LINE1" in r.content
    assert b"LINE5" in r.content


def test_line_merge_with_insertion():
    """Insertion in one side, modification in another — should auto-merge."""
    base = b"aaa\nccc\n"
    ours = b"aaa\nbbb\nccc\n"      # inserted bbb after aaa
    theirs = b"aaa\nCCC\n"          # modified ccc → CCC
    r = three_way_merge(base, ours, theirs)
    assert r.strategy == "line_merge"
    assert b"bbb" in r.content
    assert b"CCC" in r.content


def test_same_line_conflict_falls_to_lww():
    base = b"line1\nline2\n"
    ours = b"line1\nOURS\n"
    theirs = b"line1\nTHEIRS\n"
    r = three_way_merge(base, ours, theirs)
    assert r.strategy == "lww"
    assert r.content == theirs
    assert len(r.conflicts) == 1


def test_json_merge_different_keys():
    base = json.dumps({"a": 1, "b": 2}).encode()
    ours = json.dumps({"a": 10, "b": 2}).encode()
    theirs = json.dumps({"a": 1, "b": 20}).encode()
    r = three_way_merge(base, ours, theirs, path="config.json")
    assert r.strategy == "json_merge"
    merged = json.loads(r.content)
    assert merged["a"] == 10
    assert merged["b"] == 20


def test_json_merge_same_key_conflict():
    base = json.dumps({"x": 1}).encode()
    ours = json.dumps({"x": 2}).encode()
    theirs = json.dumps({"x": 3}).encode()
    r = three_way_merge(base, ours, theirs, path="config.json")
    merged = json.loads(r.content)
    assert merged["x"] == 3  # LWW: theirs wins
    assert len(r.conflicts) == 1


def test_binary_falls_to_lww():
    base = b"\x00\x01\x02"
    ours = b"\x00\x01\x03"
    theirs = b"\x00\x01\x04"
    r = three_way_merge(base, ours, theirs)
    assert r.strategy == "lww"


def test_merge_file_sets_add_delete():
    base = {"a.txt": b"a", "b.txt": b"b"}
    ours = {"a.txt": b"a"}             # deleted b.txt
    theirs = {"a.txt": b"a", "c.txt": b"c"}  # kept a, deleted b, added c
    merged, conflicts = merge_file_sets(base, ours, theirs)
    assert "c.txt" in merged
    assert "b.txt" not in merged
    assert "a.txt" in merged
