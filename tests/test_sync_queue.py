"""Tests for server/sync_queue.py — queue-based scope serialization."""

import asyncio
import pytest

from mut.server.sync_queue import ScopeQueue, _paths_overlap


# ── Path overlap logic ───────────────────────────────────────────

class TestPathsOverlap:
    def test_root_overlaps_everything(self):
        assert _paths_overlap("", "docs") is True
        assert _paths_overlap("src", "") is True
        assert _paths_overlap("", "") is True

    def test_parent_child(self):
        assert _paths_overlap("docs", "docs/internal") is True
        assert _paths_overlap("docs/internal", "docs") is True

    def test_siblings_no_overlap(self):
        assert _paths_overlap("docs", "src") is False
        assert _paths_overlap("src/frontend", "src/backend") is False

    def test_same_path(self):
        assert _paths_overlap("docs", "docs") is True

    def test_prefix_but_not_parent(self):
        # "doc" is NOT a parent of "docs" (no "/" separator)
        assert _paths_overlap("doc", "docs") is False


# ── Queue serialization ──────────────────────────────────────────

def _run(coro):
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


class TestScopeQueue:
    def test_acquire_release(self):
        q = ScopeQueue()

        async def go():
            await q.acquire("docs")
            q.release("docs")

        _run(go())

    def test_siblings_parallel(self):
        """Sibling scopes should NOT block each other."""
        q = ScopeQueue()
        order = []

        async def worker(scope, label):
            await q.acquire(scope)
            order.append(f"{label}_start")
            await asyncio.sleep(0.01)
            order.append(f"{label}_end")
            q.release(scope)

        async def go():
            await asyncio.gather(
                worker("docs", "A"),
                worker("src", "B"),
            )

        _run(go())
        # Both should start before either ends (parallel)
        assert order.index("A_start") < order.index("A_end")
        assert order.index("B_start") < order.index("B_end")
        # At least one B_start should happen before A_end (true parallelism)
        assert "B_start" in order[:3]  # B starts early

    def test_parent_child_serial(self):
        """Parent-child scopes MUST serialize."""
        q = ScopeQueue()
        order = []

        async def worker(scope, label):
            await q.acquire(scope)
            order.append(f"{label}_start")
            await asyncio.sleep(0.02)
            order.append(f"{label}_end")
            q.release(scope)

        async def go():
            # Start parent first, then child should wait
            t1 = asyncio.create_task(worker("docs", "parent"))
            await asyncio.sleep(0.005)
            t2 = asyncio.create_task(worker("docs/internal", "child"))
            await t1
            await t2

        _run(go())
        # Parent must finish before child starts
        assert order.index("parent_end") < order.index("child_start")

    def test_root_scope_serializes_everything(self):
        """Root scope ("") overlaps with all scopes."""
        q = ScopeQueue()
        order = []

        async def worker(scope, label):
            await q.acquire(scope)
            order.append(f"{label}_start")
            await asyncio.sleep(0.02)
            order.append(f"{label}_end")
            q.release(scope)

        async def go():
            t1 = asyncio.create_task(worker("", "root"))
            await asyncio.sleep(0.005)
            t2 = asyncio.create_task(worker("docs", "docs"))
            await t1
            await t2

        _run(go())
        assert order.index("root_end") < order.index("docs_start")

    def test_multiple_siblings_concurrent(self):
        """Multiple unrelated scopes run concurrently."""
        q = ScopeQueue()
        started = []

        async def worker(scope):
            await q.acquire(scope)
            started.append(scope)
            await asyncio.sleep(0.01)
            q.release(scope)

        async def go():
            await asyncio.gather(
                worker("docs"),
                worker("src"),
                worker("config"),
            )

        _run(go())
        assert len(started) == 3
