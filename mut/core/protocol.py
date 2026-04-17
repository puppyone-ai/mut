"""Typed protocol models for Mut client-server communication.

All request/response payloads are defined here as dataclasses with
full type annotations. This is the single source of truth for the
wire format — both client (transport.py) and server (handlers/) use
these models.

Versioning
──────────
``PROTOCOL_VERSION`` is bumped whenever the wire shape changes in
a non-additive way. Servers **must** reject any request whose
``protocol_version`` is below ``MIN_SUPPORTED_PROTOCOL_VERSION`` with
:class:`mut.foundation.error.ClientTooOldError` (HTTP 426) so old
clients surface a clear upgrade error rather than silently losing
data — mirroring Git's explicit ``Git-Protocol`` capability
negotiation (see ``docs/design/mut-git-alignment.md``).

Version history
* ``v1`` — initial integer-version wire format.
  (``base_version``, ``since_version``, ``version`` response fields).
* ``v2`` — commit_id migration. Replaces the integer identifiers
  with 16-hex ``*_commit_id`` strings. NOT a superset of v1: a v1
  client sending ``base_version=3`` against a v2 server would see
  ``base_commit_id`` silently default to ``""`` and skip three-way
  merge. That is the exact failure mode v2 exists to surface.

Commit identity model (v2)
──────────────────────────
- Commits are identified by a 16-hex-char commit_id (SHA256 truncated).
- Hash payload: ``scope_path | scope_hash | created_at_iso | who``.
- Linear history only; no ``parent_commit_id`` on the wire yet.
"""

from __future__ import annotations

from dataclasses import dataclass, field


PROTOCOL_VERSION = 2
MIN_SUPPORTED_PROTOCOL_VERSION = 2


from mut.foundation.config import normalize_path  # noqa: F401
from mut.foundation.error import ClientTooOldError


def require_supported_protocol(body: dict) -> int:
    """Reject requests speaking a wire version older than this server
    supports.

    Returns the validated protocol version on success. Raises
    :class:`ClientTooOldError` (HTTP 426) when the client's declared
    version is below :data:`MIN_SUPPORTED_PROTOCOL_VERSION` *or*
    absent (old clients always sent ``protocol_version=1``; a missing
    field in 2026+ almost certainly means a forged or severely
    outdated request).

    Centralizing the check here — rather than duplicating it in every
    ``from_dict`` — keeps handler code single-purpose and ensures
    every wire entry point enforces the same contract.
    """
    declared = body.get("protocol_version")
    if not isinstance(declared, int) or declared < MIN_SUPPORTED_PROTOCOL_VERSION:
        raise ClientTooOldError(
            f"client speaks protocol v{declared or 'unknown'} but this "
            f"server requires v{MIN_SUPPORTED_PROTOCOL_VERSION}+. "
            "Upgrade the client: `pip install -U mutai`."
        )
    return declared


# ── Clone ──────────────────────────────────────

@dataclass
class CloneRequest:
    protocol_version: int = PROTOCOL_VERSION

    @classmethod
    def from_dict(cls, d: dict) -> CloneRequest:
        return cls(protocol_version=d.get("protocol_version", 1))

    def to_dict(self) -> dict:
        return {"protocol_version": self.protocol_version}


@dataclass
class ScopeInfo:
    path: str = "/"
    exclude: list[str] = field(default_factory=list)
    mode: str = "rw"

    @classmethod
    def from_dict(cls, d: dict) -> ScopeInfo:
        return cls(
            path=d.get("path", "/"),
            exclude=d.get("exclude", []),
            mode=d.get("mode", "rw"),
        )

    def to_dict(self) -> dict:
        return {"path": self.path, "exclude": self.exclude, "mode": self.mode}


@dataclass
class CloneResponse:
    project: str
    files: dict[str, str]       # {rel_path: base64_content}
    objects: dict[str, str]     # {hash: base64_content}
    history: list[dict]
    head_commit_id: str
    scope: ScopeInfo
    agent_id: str = ""
    protocol_version: int = PROTOCOL_VERSION

    def to_dict(self) -> dict:
        d = {
            "protocol_version": self.protocol_version,
            "agent_id": self.agent_id,
            "project": self.project,
            "files": self.files,
            "objects": self.objects,
            "history": self.history,
            "head_commit_id": self.head_commit_id,
            "scope": self.scope.to_dict(),
        }
        return d


# ── Push ───────────────────────────────────────

@dataclass
class PushRequest:
    base_commit_id: str = ""
    snapshots: list[dict] = field(default_factory=list)
    objects: dict[str, str] = field(default_factory=dict)  # {hash: base64}
    protocol_version: int = PROTOCOL_VERSION

    @classmethod
    def from_dict(cls, d: dict) -> PushRequest:
        return cls(
            base_commit_id=d.get("base_commit_id", ""),
            snapshots=d.get("snapshots", []),
            objects=d.get("objects", {}),
            protocol_version=d.get("protocol_version", 1),
        )

    def to_dict(self) -> dict:
        return {
            "protocol_version": self.protocol_version,
            "base_commit_id": self.base_commit_id,
            "snapshots": self.snapshots,
            "objects": self.objects,
        }


@dataclass
class PushResponse:
    status: str
    commit_id: str = ""
    pushed: int = 0
    root: str = ""
    merged: bool = False
    conflicts: int = 0
    merged_changes: list[dict] = field(default_factory=list)
    protocol_version: int = PROTOCOL_VERSION

    def to_dict(self) -> dict:
        d: dict = {
            "protocol_version": self.protocol_version,
            "status": self.status,
            "commit_id": self.commit_id,
            "pushed": self.pushed,
            "root": self.root,
        }
        if self.merged:
            d["merged"] = True
            d["conflicts"] = self.conflicts
        if self.merged_changes:
            d["merged_changes"] = self.merged_changes
        return d


# ── Pull ───────────────────────────────────────

@dataclass
class PullRequest:
    since_commit_id: str = ""
    have_hashes: list[str] = field(default_factory=list)
    protocol_version: int = PROTOCOL_VERSION

    @classmethod
    def from_dict(cls, d: dict) -> PullRequest:
        return cls(
            since_commit_id=d.get("since_commit_id", ""),
            have_hashes=d.get("have_hashes", []),
            protocol_version=d.get("protocol_version", 1),
        )

    def to_dict(self) -> dict:
        d: dict = {
            "protocol_version": self.protocol_version,
            "since_commit_id": self.since_commit_id,
        }
        if self.have_hashes:
            d["have_hashes"] = self.have_hashes
        return d


@dataclass
class PullResponse:
    status: str
    head_commit_id: str = ""
    files: dict[str, str] = field(default_factory=dict)
    objects: dict[str, str] = field(default_factory=dict)
    history: list[dict] = field(default_factory=list)
    protocol_version: int = PROTOCOL_VERSION

    def to_dict(self) -> dict:
        return {
            "protocol_version": self.protocol_version,
            "status": self.status,
            "head_commit_id": self.head_commit_id,
            "files": self.files,
            "objects": self.objects,
            "history": self.history,
        }


# ── Negotiate ──────────────────────────────────

@dataclass
class NegotiateRequest:
    """Combined object probe + ref discovery.

    ``hashes`` asks the server which of the listed object hashes it is
    missing (classic use — used before sending a pack to avoid
    redundant uploads).

    ``remote_head`` (v2) is the commit_id the *client* believes the
    server's head is at (i.e. the client's own REMOTE_HEAD). The
    server echoes back whether that commit still exists in its history
    so the client can detect a server-side history wipe and reset
    stale "already pushed" bookkeeping. This is the machinery used by
    ``mut push`` to kill phantom up-to-date states (see Bug #6).
    """
    hashes: list[str] = field(default_factory=list)
    remote_head: str = ""
    protocol_version: int = PROTOCOL_VERSION

    @classmethod
    def from_dict(cls, d: dict) -> NegotiateRequest:
        return cls(
            hashes=d.get("hashes", []),
            remote_head=d.get("remote_head", ""),
            protocol_version=d.get("protocol_version", 1),
        )

    def to_dict(self) -> dict:
        out: dict = {
            "protocol_version": self.protocol_version,
            "hashes": self.hashes,
        }
        if self.remote_head:
            out["remote_head"] = self.remote_head
        return out


@dataclass
class NegotiateResponse:
    """Pairs classic missing-objects info with ref-discovery fields.

    ``server_head_commit_id`` is the server's current head for the
    caller's scope (always populated so the client can re-anchor even
    when it did not send a ``remote_head``).

    ``remote_head_recognized`` is ``False`` iff the client asked about
    a specific ``remote_head`` and the server can no longer find it
    in history — the signal to reset local watermarks.
    """
    missing: list[str] = field(default_factory=list)
    server_head_commit_id: str = ""
    remote_head_recognized: bool = True
    protocol_version: int = PROTOCOL_VERSION

    @classmethod
    def from_dict(cls, d: dict) -> NegotiateResponse:
        return cls(
            missing=d.get("missing", []),
            server_head_commit_id=d.get("server_head_commit_id", ""),
            remote_head_recognized=d.get("remote_head_recognized", True),
            protocol_version=d.get("protocol_version", 1),
        )

    def to_dict(self) -> dict:
        return {
            "protocol_version": self.protocol_version,
            "missing": self.missing,
            "server_head_commit_id": self.server_head_commit_id,
            "remote_head_recognized": self.remote_head_recognized,
        }


# ── Pull Commit ───────────────────────────────

@dataclass
class PullCommitRequest:
    """Fetch files at a specific commit (not just HEAD)."""
    commit_id: str = ""
    protocol_version: int = PROTOCOL_VERSION

    @classmethod
    def from_dict(cls, d: dict) -> PullCommitRequest:
        return cls(
            commit_id=d.get("commit_id", ""),
            protocol_version=d.get("protocol_version", 1),
        )

    def to_dict(self) -> dict:
        return {
            "protocol_version": self.protocol_version,
            "commit_id": self.commit_id,
        }


# ── Rollback ──────────────────────────────────

@dataclass
class RollbackRequest:
    target_commit_id: str = ""
    protocol_version: int = PROTOCOL_VERSION

    @classmethod
    def from_dict(cls, d: dict) -> RollbackRequest:
        return cls(
            target_commit_id=d.get("target_commit_id", ""),
            protocol_version=d.get("protocol_version", 1),
        )

    def to_dict(self) -> dict:
        return {
            "protocol_version": self.protocol_version,
            "target_commit_id": self.target_commit_id,
        }


@dataclass
class RollbackResponse:
    status: str
    new_commit_id: str = ""
    target_commit_id: str = ""
    root: str = ""
    changes: list[dict] = field(default_factory=list)
    protocol_version: int = PROTOCOL_VERSION

    def to_dict(self) -> dict:
        d: dict = {
            "protocol_version": self.protocol_version,
            "status": self.status,
            "new_commit_id": self.new_commit_id,
            "target_commit_id": self.target_commit_id,
            "changes": self.changes,
        }
        if self.root:
            d["root"] = self.root
        return d


# ── Error ──────────────────────────────────────

@dataclass
class ErrorResponse:
    error: str
    code: int = 500
    protocol_version: int = PROTOCOL_VERSION

    def to_dict(self) -> dict:
        return {
            "protocol_version": self.protocol_version,
            "error": self.error,
        }
