"""Tests for the ConflictResolver / MergeStrategy abstraction in core/merge.py."""

import json
import pytest

from mut.core.merge import (
    ConflictResolver,
    MergeStrategy, MergeResult, ConflictRecord,
    IdenticalStrategy, OneSideOnlyStrategy,
    LineMergeStrategy, JsonMergeStrategy, LWWStrategy,
    DEFAULT_STRATEGIES,
    three_way_merge,
)


# ── Individual strategies ──────────────────────

class TestIdenticalStrategy:
    def test_match(self):
        s = IdenticalStrategy()
        r = s.try_merge(b"base", b"same", b"same", "f.txt")
        assert r is not None
        assert r.strategy == "identical"
        assert r.content == b"same"

    def test_no_match(self):
        s = IdenticalStrategy()
        r = s.try_merge(b"base", b"ours", b"theirs", "f.txt")
        assert r is None


class TestOneSideOnlyStrategy:
    def test_theirs_only(self):
        s = OneSideOnlyStrategy()
        r = s.try_merge(b"base", b"base", b"changed", "f.txt")
        assert r is not None
        assert r.strategy == "theirs_only"
        assert r.content == b"changed"

    def test_ours_only(self):
        s = OneSideOnlyStrategy()
        r = s.try_merge(b"base", b"changed", b"base", "f.txt")
        assert r is not None
        assert r.strategy == "ours_only"
        assert r.content == b"changed"

    def test_both_changed(self):
        s = OneSideOnlyStrategy()
        r = s.try_merge(b"base", b"A", b"B", "f.txt")
        assert r is None


class TestLineMergeStrategy:
    def test_non_overlapping(self):
        s = LineMergeStrategy()
        base = b"line1\nline2\nline3\n"
        ours = b"LINE1\nline2\nline3\n"
        theirs = b"line1\nline2\nLINE3\n"
        r = s.try_merge(base, ours, theirs, "f.txt")
        assert r is not None
        assert r.strategy == "line_merge"
        assert b"LINE1" in r.content
        assert b"LINE3" in r.content

    def test_overlapping_returns_none(self):
        s = LineMergeStrategy()
        base = b"line1\nline2\n"
        ours = b"line1\nOURS\n"
        theirs = b"line1\nTHEIRS\n"
        r = s.try_merge(base, ours, theirs, "f.txt")
        assert r is None

    def test_binary_returns_none(self):
        s = LineMergeStrategy()
        base = b"\x80\x81"
        ours = b"\x80\x82"
        theirs = b"\x80\x83"
        r = s.try_merge(base, ours, theirs, "f.txt")
        assert r is None


class TestJsonMergeStrategy:
    def test_only_for_json_files(self):
        s = JsonMergeStrategy()
        r = s.try_merge(b'{"a":1}', b'{"a":2}', b'{"a":3}', "readme.txt")
        assert r is None

    def test_different_keys(self):
        s = JsonMergeStrategy()
        base = json.dumps({"a": 1, "b": 2}).encode()
        ours = json.dumps({"a": 10, "b": 2}).encode()
        theirs = json.dumps({"a": 1, "b": 20}).encode()
        r = s.try_merge(base, ours, theirs, "config.json")
        assert r is not None
        merged = json.loads(r.content)
        assert merged["a"] == 10
        assert merged["b"] == 20

    def test_non_dict_json_returns_none(self):
        s = JsonMergeStrategy()
        base = json.dumps([1, 2, 3]).encode()
        ours = json.dumps([1, 2, 4]).encode()
        theirs = json.dumps([1, 2, 5]).encode()
        r = s.try_merge(base, ours, theirs, "list.json")
        assert r is None

    def test_invalid_json_returns_none(self):
        s = JsonMergeStrategy()
        r = s.try_merge(b"not json", b"still not", b"nope", "bad.json")
        assert r is None


class TestLWWStrategy:
    def test_always_succeeds(self):
        s = LWWStrategy()
        r = s.try_merge(b"base", b"ours", b"theirs", "f.txt")
        assert r is not None
        assert r.strategy == "lww"
        assert r.content == b"theirs"
        assert len(r.conflicts) == 1
        assert r.conflicts[0].kept == "theirs"

    def test_lost_content_recorded(self):
        s = LWWStrategy()
        r = s.try_merge(b"base", b"important data", b"theirs", "f.txt")
        assert r.conflicts[0].lost_content == "important data"


# ── ConflictResolver ───────────────────────────

class TestConflictResolver:
    def test_default_resolver_uses_all_strategies(self):
        resolver = ConflictResolver()
        assert len(resolver.strategies) == len(DEFAULT_STRATEGIES)

    def test_identical_short_circuits(self):
        resolver = ConflictResolver()
        r = resolver.resolve(b"base", b"same", b"same", "f.txt")
        assert r.strategy == "identical"

    def test_falls_through_to_lww(self):
        resolver = ConflictResolver()
        r = resolver.resolve(b"\x00", b"\x01", b"\x02", "binary.bin")
        assert r.strategy == "lww"

    def test_custom_strategy_chain(self):
        """A chain with only LWW should always produce LWW result."""
        resolver = ConflictResolver(strategies=[LWWStrategy()])
        r = resolver.resolve(b"base", b"same", b"same", "f.txt")
        assert r.strategy == "lww"

    def test_custom_strategy_takes_precedence(self):
        """Custom strategy inserted first should override defaults."""

        class AlwaysOursStrategy(MergeStrategy):
            name = "always_ours"

            def try_merge(self, base, ours, theirs, path):
                return MergeResult(content=ours, strategy="always_ours")

        resolver = ConflictResolver(strategies=[AlwaysOursStrategy()])
        r = resolver.resolve(b"base", b"OURS", b"THEIRS", "f.txt")
        assert r.strategy == "always_ours"
        assert r.content == b"OURS"

    def test_empty_chain_falls_to_lww(self):
        """An empty chain should fallback to LWW safely."""
        resolver = ConflictResolver(strategies=[])
        r = resolver.resolve(b"base", b"ours", b"theirs", "f.txt")
        assert r.strategy == "lww"

    def test_resolver_passed_to_three_way_merge(self):
        """three_way_merge accepts a custom resolver."""
        resolver = ConflictResolver(strategies=[LWWStrategy()])
        r = three_way_merge(b"base", b"same", b"same", "f.txt", resolver=resolver)
        # LWW-only chain: even identical content goes through LWW
        assert r.strategy == "lww"


# ── JSON nested dict merge ─────────────────────

class TestJsonNestedMerge:
    def test_nested_dict_merge(self):
        base = json.dumps({"db": {"host": "old", "port": 5432}}).encode()
        ours = json.dumps({"db": {"host": "new-host", "port": 5432}}).encode()
        theirs = json.dumps({"db": {"host": "old", "port": 5433}}).encode()
        r = three_way_merge(base, ours, theirs, path="config.json")
        merged = json.loads(r.content)
        assert merged["db"]["host"] == "new-host"
        assert merged["db"]["port"] == 5433

    def test_json_key_deletion(self):
        base = json.dumps({"a": 1, "b": 2}).encode()
        ours = json.dumps({"a": 1}).encode()  # deleted "b"
        theirs = json.dumps({"a": 1, "b": 2}).encode()  # unchanged
        r = three_way_merge(base, ours, theirs, path="data.json")
        merged = json.loads(r.content)
        assert "b" not in merged

    def test_json_key_addition(self):
        base = json.dumps({"a": 1}).encode()
        ours = json.dumps({"a": 1, "b": 2}).encode()
        theirs = json.dumps({"a": 1, "c": 3}).encode()
        r = three_way_merge(base, ours, theirs, path="data.json")
        merged = json.loads(r.content)
        assert merged["b"] == 2
        assert merged["c"] == 3


# ── merge_file_sets edge cases ─────────────────

class TestMergeFileSets:
    def test_both_deleted(self):
        from mut.core.merge import merge_file_sets
        base = {"a.txt": b"content"}
        ours = {}
        theirs = {}
        merged, conflicts = merge_file_sets(base, ours, theirs)
        assert "a.txt" not in merged

    def test_delete_modify_conflict(self):
        from mut.core.merge import merge_file_sets
        base = {"a.txt": b"original"}
        ours = {}  # deleted
        theirs = {"a.txt": b"modified"}  # modified
        merged, conflicts = merge_file_sets(base, ours, theirs)
        assert merged["a.txt"] == b"modified"
        assert any(c.strategy == "delete_modify" for c in conflicts)

    def test_modify_delete_conflict(self):
        from mut.core.merge import merge_file_sets
        base = {"a.txt": b"original"}
        ours = {"a.txt": b"modified"}  # modified
        theirs = {}  # deleted
        merged, conflicts = merge_file_sets(base, ours, theirs)
        assert merged["a.txt"] == b"modified"
        assert any(c.strategy == "modify_delete" for c in conflicts)

    def test_new_file_from_theirs(self):
        from mut.core.merge import merge_file_sets
        merged, conflicts = merge_file_sets({}, {}, {"new.txt": b"hello"})
        assert merged["new.txt"] == b"hello"

    def test_new_file_from_ours(self):
        from mut.core.merge import merge_file_sets
        merged, conflicts = merge_file_sets({}, {"new.txt": b"hello"}, {})
        assert merged["new.txt"] == b"hello"

    def test_custom_resolver_in_file_sets(self):
        from mut.core.merge import merge_file_sets
        resolver = ConflictResolver(strategies=[LWWStrategy()])
        base = {"f.txt": b"base"}
        ours = {"f.txt": b"ours"}
        theirs = {"f.txt": b"theirs"}
        merged, conflicts = merge_file_sets(base, ours, theirs, resolver=resolver)
        assert merged["f.txt"] == b"theirs"
        assert any(c.strategy == "lww" for c in conflicts)
