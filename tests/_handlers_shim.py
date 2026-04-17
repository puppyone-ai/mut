"""Test-only wrappers that auto-inject the current ``PROTOCOL_VERSION``.

The real wire-protocol contract (see
:func:`mut.core.protocol.require_supported_protocol`) rejects any
request that does not carry ``protocol_version`` >= the supported
minimum. That is exactly the invariant ``tests/`` exercises
repeatedly, but the per-call dict literals already encode the
semantic payload being tested — adding ``{"protocol_version": N, ...}``
to every one obscures the real assertion.

These wrappers inject the version in one place so tests stay readable.
Tests that specifically want to assert the rejection behavior can
import from :mod:`mut.server.handlers` / :mod:`mut.server.server`
directly and call with a crafted body.
"""

from __future__ import annotations

import asyncio

from mut.core.protocol import PROTOCOL_VERSION
from mut.server import handlers as _sync
from mut.server import server as _async


def _v(body):
    out = dict(body or {})
    out.setdefault("protocol_version", PROTOCOL_VERSION)
    return out


# ── Sync handlers ────────────────────────────────

def handle_clone(repo, auth, body=None):
    return _sync.handle_clone(repo, auth, _v(body))


def handle_push(repo, auth, body=None):
    return _sync.handle_push(repo, auth, _v(body))


def handle_pull(repo, auth, body=None):
    return _sync.handle_pull(repo, auth, _v(body))


def handle_negotiate(repo, auth, body=None):
    return _sync.handle_negotiate(repo, auth, _v(body))


def handle_rollback(repo, auth, body=None):
    return _sync.handle_rollback(repo, auth, _v(body))


def handle_pull_commit(repo, auth, body=None):
    return _sync.handle_pull_commit(repo, auth, _v(body))


# ── Async handlers (mut.server.server._handle_*) ─

async def _handle_clone(repo, auth, body=None):
    return await _async._handle_clone(repo, auth, _v(body))


async def _handle_push(repo, auth, body=None):
    return await _async._handle_push(repo, auth, _v(body))


async def _handle_pull(repo, auth, body=None):
    return await _async._handle_pull(repo, auth, _v(body))


async def _handle_negotiate(repo, auth, body=None):
    return await _async._handle_negotiate(repo, auth, _v(body))


async def _handle_rollback(repo, auth, body=None):
    return await _async._handle_rollback(repo, auth, _v(body))


async def _handle_pull_commit(repo, auth, body=None):
    return await _async._handle_pull_commit(repo, auth, _v(body))
