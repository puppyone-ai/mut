"""Mut error types.

Each server-facing error carries an HTTP status code so that the
server handler can return the correct response without a big
if/elif chain.
"""


class MutError(Exception):
    """Base error for all Mut operations."""
    http_status: int = 500


class NotARepoError(MutError):
    """Raised when operating outside a .mut/ repository."""
    http_status = 400


class SnapshotNotFoundError(MutError):
    """Raised when a requested snapshot ID does not exist."""
    http_status = 404


class ObjectNotFoundError(MutError):
    """Raised when a hash-addressed object is missing from the store."""
    http_status = 404


class AuthenticationError(MutError):
    """Raised on invalid / expired / missing auth tokens."""
    http_status = 401


class PermissionDenied(MutError):
    """Raised when an agent tries to access outside its scope."""
    http_status = 403


class LockError(MutError):
    """Raised when a scope lock cannot be acquired."""
    http_status = 409


class ConflictError(MutError):
    """Raised when a merge conflict cannot be auto-resolved."""
    http_status = 409


class DirtyWorkdirError(MutError):
    """Raised when pull would overwrite uncommitted local changes."""
    http_status = 400


class NetworkError(MutError):
    """Raised on server communication failures."""
    http_status = 502


class StorageWriteError(MutError):
    """Raised when writing an object to the storage backend fails."""
    http_status = 502


class PayloadTooLargeError(MutError):
    """Raised when request body exceeds the size limit."""
    http_status = 413


class ValidationError(MutError):
    """Raised when request data fails schema/semantic validation."""
    http_status = 422


class ClientTooOldError(MutError):
    """Raised when a client speaks an outdated wire protocol version.

    Per the design in ``docs/design/mut-git-alignment.md`` and mirroring
    Git's ``Git-Protocol`` header negotiation, the server rejects any
    request whose ``protocol_version`` falls below
    :data:`mut.core.protocol.MIN_SUPPORTED_PROTOCOL_VERSION` instead of
    silently defaulting missing fields — the latter let old clients
    push trees without their real ``base_commit_id`` being honored and
    silently bypass three-way merge. Returns HTTP 426 so clients can
    recognize "upgrade required" without confusing it with 400/422.
    """
    http_status = 426
