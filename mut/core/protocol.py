"""Typed protocol models for Mut client-server communication.

All request/response payloads are defined here as dataclasses with
full type annotations. This is the single source of truth for the
wire format — both client (transport.py) and server (handlers/) use
these models.

Protocol version is embedded in every request so that server and
client can negotiate compatibility.

Commit identity model (since v1 + hash-id migration):
- Commits are identified by a 16-hex-char commit_id (SHA256 truncated).
- Hash payload: scope_path | scope_hash | created_at_iso | who.
- Linear history only; no parent_commit_id on the wire yet.
"""

from __future__ import annotations

from dataclasses import dataclass, field


PROTOCOL_VERSION = 1


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
