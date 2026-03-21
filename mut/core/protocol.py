"""Typed protocol models for Mut client-server communication.

All request/response payloads are defined here as dataclasses with
full type annotations. This is the single source of truth for the
wire format — both client (transport.py) and server (handlers/) use
these models.

Protocol version is embedded in every request so that server and
client can negotiate compatibility.
"""

from __future__ import annotations

from dataclasses import dataclass, field


# ── Protocol version ───────────────────────────
PROTOCOL_VERSION = 1


# ── Helpers ────────────────────────────────────

# Re-export from canonical location for backwards compatibility
from mut.foundation.config import normalize_path  # noqa: F401


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
    version: int
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
            "version": self.version,
            "scope": self.scope.to_dict(),
        }
        return d


# ── Push ───────────────────────────────────────

@dataclass
class PushRequest:
    base_version: int = 0
    snapshots: list[dict] = field(default_factory=list)
    objects: dict[str, str] = field(default_factory=dict)  # {hash: base64}
    protocol_version: int = PROTOCOL_VERSION

    @classmethod
    def from_dict(cls, d: dict) -> PushRequest:
        return cls(
            base_version=d.get("base_version", 0),
            snapshots=d.get("snapshots", []),
            objects=d.get("objects", {}),
            protocol_version=d.get("protocol_version", 1),
        )

    def to_dict(self) -> dict:
        return {
            "protocol_version": self.protocol_version,
            "base_version": self.base_version,
            "snapshots": self.snapshots,
            "objects": self.objects,
        }


@dataclass
class PushResponse:
    status: str
    version: int
    pushed: int = 0
    root: str = ""
    merged: bool = False
    conflicts: int = 0
    protocol_version: int = PROTOCOL_VERSION

    def to_dict(self) -> dict:
        d: dict = {
            "protocol_version": self.protocol_version,
            "status": self.status,
            "version": self.version,
            "pushed": self.pushed,
            "root": self.root,
        }
        if self.merged:
            d["merged"] = True
            d["conflicts"] = self.conflicts
        return d


# ── Pull ───────────────────────────────────────

@dataclass
class PullRequest:
    since_version: int = 0
    have_hashes: list[str] = field(default_factory=list)
    protocol_version: int = PROTOCOL_VERSION

    @classmethod
    def from_dict(cls, d: dict) -> PullRequest:
        return cls(
            since_version=d.get("since_version", 0),
            have_hashes=d.get("have_hashes", []),
            protocol_version=d.get("protocol_version", 1),
        )

    def to_dict(self) -> dict:
        d: dict = {
            "protocol_version": self.protocol_version,
            "since_version": self.since_version,
        }
        if self.have_hashes:
            d["have_hashes"] = self.have_hashes
        return d


@dataclass
class PullResponse:
    status: str
    version: int
    files: dict[str, str] = field(default_factory=dict)
    objects: dict[str, str] = field(default_factory=dict)
    history: list[dict] = field(default_factory=list)
    protocol_version: int = PROTOCOL_VERSION

    def to_dict(self) -> dict:
        return {
            "protocol_version": self.protocol_version,
            "status": self.status,
            "version": self.version,
            "files": self.files,
            "objects": self.objects,
            "history": self.history,
        }


# ── Negotiate ──────────────────────────────────

@dataclass
class NegotiateRequest:
    hashes: list[str] = field(default_factory=list)
    protocol_version: int = PROTOCOL_VERSION

    @classmethod
    def from_dict(cls, d: dict) -> NegotiateRequest:
        return cls(
            hashes=d.get("hashes", []),
            protocol_version=d.get("protocol_version", 1),
        )

    def to_dict(self) -> dict:
        return {
            "protocol_version": self.protocol_version,
            "hashes": self.hashes,
        }


@dataclass
class NegotiateResponse:
    missing: list[str] = field(default_factory=list)
    protocol_version: int = PROTOCOL_VERSION

    def to_dict(self) -> dict:
        return {
            "protocol_version": self.protocol_version,
            "missing": self.missing,
        }


# ── Pull Version ──────────────────────────────

@dataclass
class PullVersionRequest:
    version: int = 0
    protocol_version: int = PROTOCOL_VERSION

    @classmethod
    def from_dict(cls, d: dict) -> PullVersionRequest:
        return cls(
            version=d.get("version", 0),
            protocol_version=d.get("protocol_version", 1),
        )

    def to_dict(self) -> dict:
        return {
            "protocol_version": self.protocol_version,
            "version": self.version,
        }


# ── Rollback ──────────────────────────────────

@dataclass
class RollbackRequest:
    target_version: int = 0
    protocol_version: int = PROTOCOL_VERSION

    @classmethod
    def from_dict(cls, d: dict) -> RollbackRequest:
        return cls(
            target_version=d.get("target_version", 0),
            protocol_version=d.get("protocol_version", 1),
        )

    def to_dict(self) -> dict:
        return {
            "protocol_version": self.protocol_version,
            "target_version": self.target_version,
        }


@dataclass
class RollbackResponse:
    status: str
    new_version: int = 0
    target_version: int = 0
    changes: list[dict] = field(default_factory=list)
    protocol_version: int = PROTOCOL_VERSION

    def to_dict(self) -> dict:
        return {
            "protocol_version": self.protocol_version,
            "status": self.status,
            "new_version": self.new_version,
            "target_version": self.target_version,
            "changes": self.changes,
        }


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
