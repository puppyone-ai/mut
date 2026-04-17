"""Tests for protocol-version enforcement (Bug #8) and the
negotiate-driven REMOTE_HEAD self-heal (Bug #6).

These tests directly exercise the helpers added in ``mut/core/protocol.py``,
``mut/server/handlers.py``, and ``mut/core/snapshot.py`` so the safety net
catches accidental regressions of the cross-version compatibility contract.
"""

from __future__ import annotations

import pytest

from mut.core.protocol import (
    PROTOCOL_VERSION,
    MIN_SUPPORTED_PROTOCOL_VERSION,
    NegotiateRequest,
    NegotiateResponse,
    require_supported_protocol,
)
from mut.core.snapshot import SnapshotChain
from mut.foundation.error import ClientTooOldError
from mut.server.repo import ServerRepo
from tests._handlers_shim import handle_clone, handle_negotiate


_CID_SEED = "deadbeefcafef00d"


# ---------------------------------------------------------------------------
# Bug #8 — protocol version enforcement
# ---------------------------------------------------------------------------


class TestRequireSupportedProtocol:
    def test_accepts_current_protocol(self):
        assert require_supported_protocol(
            {"protocol_version": PROTOCOL_VERSION}
        ) == PROTOCOL_VERSION

    def test_accepts_minimum_supported(self):
        assert require_supported_protocol(
            {"protocol_version": MIN_SUPPORTED_PROTOCOL_VERSION}
        ) == MIN_SUPPORTED_PROTOCOL_VERSION

    def test_accepts_future_protocol(self):
        # Forward-compat: a newer client can talk to an older server as long
        # as it speaks at least our minimum.
        future = MIN_SUPPORTED_PROTOCOL_VERSION + 5
        assert require_supported_protocol({"protocol_version": future}) == future

    def test_rejects_missing_field(self):
        with pytest.raises(ClientTooOldError):
            require_supported_protocol({})

    def test_rejects_non_int(self):
        with pytest.raises(ClientTooOldError):
            require_supported_protocol({"protocol_version": "1"})

    def test_rejects_below_minimum(self):
        below = MIN_SUPPORTED_PROTOCOL_VERSION - 1
        with pytest.raises(ClientTooOldError) as exc:
            require_supported_protocol({"protocol_version": below})
        # Message must surface the upgrade hint so users know what to do.
        assert "pip install" in str(exc.value).lower()

    def test_error_uses_upgrade_required_status(self):
        with pytest.raises(ClientTooOldError) as exc:
            require_supported_protocol({"protocol_version": 0})
        assert exc.value.http_status == 426


# ---------------------------------------------------------------------------
# Bug #8 — handler entrypoints reject old clients before doing any work
# ---------------------------------------------------------------------------


@pytest.fixture
def server_repo(tmp_path):
    repo = ServerRepo.init(str(tmp_path / "server"), project_name="test-proto")
    root = repo.build_full_tree()
    repo.set_root_hash(root)
    repo.record_history(
        _CID_SEED, "server", "initial state", "/", [], root_hash=root,
    )
    repo.set_head_commit_id(_CID_SEED)
    return repo


@pytest.fixture
def rw_scope(server_repo):
    server_repo.add_scope("scope-1", "/")
    scope = server_repo.scopes.get_by_id("scope-1")
    scope["mode"] = "rw"
    return scope


@pytest.fixture
def auth(rw_scope):
    return {"agent": "agent-A", "_scope": rw_scope}


class TestHandlerProtocolEnforcement:
    """Calling handlers WITHOUT the shim must reject old wire versions.

    The shim auto-injects ``protocol_version``; here we deliberately bypass
    it to confirm the enforcement is wired at the very top of each handler.
    """

    def test_clone_rejects_missing_version(self, server_repo, auth):
        from mut.server.handlers import handle_clone as raw_clone
        with pytest.raises(ClientTooOldError):
            raw_clone(server_repo, auth, {})

    def test_negotiate_rejects_old_version(self, server_repo, auth):
        from mut.server.handlers import handle_negotiate as raw_negotiate
        with pytest.raises(ClientTooOldError):
            raw_negotiate(
                server_repo, auth,
                {"protocol_version": 1, "hashes": []},
            )

    def test_push_rejects_old_version(self, server_repo, auth):
        from mut.server.handlers import handle_push as raw_push
        with pytest.raises(ClientTooOldError):
            raw_push(
                server_repo, auth,
                {"protocol_version": 1, "snapshots": [], "objects": {}},
            )

    def test_pull_rejects_old_version(self, server_repo, auth):
        from mut.server.handlers import handle_pull as raw_pull
        with pytest.raises(ClientTooOldError):
            raw_pull(
                server_repo, auth,
                {"protocol_version": 1, "since_commit_id": "", "have_hashes": []},
            )


# ---------------------------------------------------------------------------
# Bug #6 — negotiate self-heal
# ---------------------------------------------------------------------------


class TestNegotiateRemoteHeadProbe:
    def test_response_advertises_server_head(self, server_repo, auth):
        result = handle_negotiate(server_repo, auth, {"hashes": []})
        # Server head is set in the fixture, must be echoed back.
        assert result["server_head_commit_id"] == _CID_SEED
        # Without sending a remote_head the field is treated as recognised.
        assert result["remote_head_recognized"] is True

    def test_recognises_known_remote_head(self, server_repo, auth):
        result = handle_negotiate(server_repo, auth, {
            "hashes": [],
            "remote_head": _CID_SEED,
        })
        assert result["remote_head_recognized"] is True
        assert result["server_head_commit_id"] == _CID_SEED

    def test_flags_unknown_remote_head(self, server_repo, auth):
        # Simulates: server history was truncated (or restored from a
        # backup), and the client's REMOTE_HEAD points at a commit the
        # server no longer knows about.
        result = handle_negotiate(server_repo, auth, {
            "hashes": [],
            "remote_head": "0000000000000000",
        })
        assert result["remote_head_recognized"] is False
        # Server head must still be returned so the client can re-anchor.
        assert result["server_head_commit_id"] == _CID_SEED

    def test_round_trip_preserves_new_fields(self):
        req = NegotiateRequest(hashes=["aaa"], remote_head="cafebabe12345678")
        encoded = req.to_dict()
        decoded = NegotiateRequest.from_dict(encoded)
        assert decoded.hashes == ["aaa"]
        assert decoded.remote_head == "cafebabe12345678"

        resp = NegotiateResponse(
            missing=["x"],
            server_head_commit_id="abcdef0123456789",
            remote_head_recognized=False,
        )
        encoded_resp = resp.to_dict()
        decoded_resp = NegotiateResponse.from_dict(encoded_resp)
        assert decoded_resp.missing == ["x"]
        assert decoded_resp.server_head_commit_id == "abcdef0123456789"
        assert decoded_resp.remote_head_recognized is False

    def test_negotiate_response_defaults_back_compat(self):
        # Older payloads without the new fields must still decode.
        decoded = NegotiateResponse.from_dict({"missing": ["a"]})
        assert decoded.missing == ["a"]
        assert decoded.server_head_commit_id == ""
        assert decoded.remote_head_recognized is True


# ---------------------------------------------------------------------------
# Bug #6 — local snapshot watermark reset
# ---------------------------------------------------------------------------


class TestResetPushedWatermark:
    def test_clears_pushed_flag_for_local_commits(self, tmp_path):
        chain = SnapshotChain(tmp_path / "snapshots")
        chain.create("r1", "agent-A", "first", pushed=True)
        chain.create("r2", "agent-A", "second", pushed=True)

        cleared = chain.reset_pushed_watermark()

        assert cleared == 2
        unpushed = chain.get_unpushed()
        assert {s["id"] for s in unpushed} == {1, 2}

    def test_preserves_pull_originated_snapshots(self, tmp_path):
        chain = SnapshotChain(tmp_path / "snapshots")
        # Local commit pushed up to the (now-truncated) server.
        chain.create("r1", "agent-A", "local change", pushed=True)
        # Snapshot that came from a pull — its data lives on the server,
        # we should never re-push it as a fresh commit.
        snap2 = chain.create("r2", "pull", "pulled", pushed=True)
        snap2["server_commit_id"] = "abcdef0123456789"
        # Persist via the chain's writer.
        chain._write_snap(snap2)

        cleared = chain.reset_pushed_watermark()

        assert cleared == 1  # only the local commit
        unpushed_ids = {s["id"] for s in chain.get_unpushed()}
        assert unpushed_ids == {1}
        # The pulled snapshot remains pushed=True so we don't re-upload it.
        assert chain.get(2)["pushed"] is True

    def test_resets_watermark_file(self, tmp_path):
        chain = SnapshotChain(tmp_path / "snapshots")
        chain.create("r1", "agent-A", "first", pushed=True)
        chain.create("r2", "agent-A", "second", pushed=True)

        chain.reset_pushed_watermark()
        # After reset, get_unpushed() must walk from id 1 again.
        unpushed = chain.get_unpushed()
        assert [s["id"] for s in unpushed] == [1, 2]

    def test_noop_when_nothing_pushed(self, tmp_path):
        chain = SnapshotChain(tmp_path / "snapshots")
        chain.create("r1", "agent-A", "first")  # pushed=False

        cleared = chain.reset_pushed_watermark()
        assert cleared == 0
        # still unpushed
        assert [s["id"] for s in chain.get_unpushed()] == [1]
