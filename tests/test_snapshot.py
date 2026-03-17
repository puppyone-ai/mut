"""Tests for core/snapshot.py — SnapshotChain."""

import pytest

from mut.core.snapshot import SnapshotChain


@pytest.fixture
def chain(tmp_path):
    return SnapshotChain(tmp_path / "snapshots.json")


class TestSnapshotChain:
    def test_empty_chain(self, chain):
        assert chain.load_all() == []
        assert chain.latest() is None
        assert chain.count() == 0

    def test_create_first(self, chain):
        snap = chain.create("roothash1", "agent-A", "initial commit")
        assert snap is not None
        assert snap["id"] == 1
        assert snap["root"] == "roothash1"
        assert snap["who"] == "agent-A"
        assert snap["message"] == "initial commit"
        assert snap["parent"] is None
        assert snap["pushed"] is False

    def test_create_second(self, chain):
        chain.create("root1", "a", "first")
        snap = chain.create("root2", "b", "second")
        assert snap["id"] == 2
        assert snap["parent"] == 1

    def test_create_returns_none_if_unchanged(self, chain):
        chain.create("roothash1", "a", "first")
        result = chain.create("roothash1", "a", "same root")
        assert result is None

    def test_get_by_id(self, chain):
        chain.create("r1", "a", "first")
        chain.create("r2", "b", "second")
        snap = chain.get(1)
        assert snap["root"] == "r1"
        snap2 = chain.get(2)
        assert snap2["root"] == "r2"

    def test_get_invalid_id(self, chain):
        chain.create("r1", "a", "first")
        assert chain.get(0) is None
        assert chain.get(99) is None

    def test_latest(self, chain):
        chain.create("r1", "a", "first")
        chain.create("r2", "b", "second")
        latest = chain.latest()
        assert latest["id"] == 2
        assert latest["root"] == "r2"

    def test_count(self, chain):
        assert chain.count() == 0
        chain.create("r1", "a", "first")
        assert chain.count() == 1
        chain.create("r2", "b", "second")
        assert chain.count() == 2

    def test_get_unpushed(self, chain):
        chain.create("r1", "a", "local1")
        chain.create("r2", "b", "local2", pushed=True)
        chain.create("r3", "c", "local3")
        unpushed = chain.get_unpushed()
        ids = [s["id"] for s in unpushed]
        assert 1 in ids
        assert 3 in ids
        assert 2 not in ids

    def test_mark_pushed(self, chain):
        chain.create("r1", "a", "first")
        chain.create("r2", "b", "second")
        chain.mark_pushed(1)
        unpushed = chain.get_unpushed()
        ids = [s["id"] for s in unpushed]
        assert 1 not in ids
        assert 2 in ids

    def test_mark_pushed_all(self, chain):
        chain.create("r1", "a", "first")
        chain.create("r2", "b", "second")
        chain.mark_pushed(2)
        assert chain.get_unpushed() == []

    def test_create_with_pushed_true(self, chain):
        snap = chain.create("r1", "pull", "pulled", pushed=True)
        assert snap["pushed"] is True
        assert chain.get_unpushed() == []

    def test_time_is_set(self, chain):
        snap = chain.create("r1", "a", "first")
        assert snap["time"]  # non-empty string
