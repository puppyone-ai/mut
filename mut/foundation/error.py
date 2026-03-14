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
    http_status: int = 400


class SnapshotNotFoundError(MutError):
    """Raised when a requested snapshot ID does not exist."""
    http_status: int = 404


class ObjectNotFoundError(MutError):
    """Raised when a hash-addressed object is missing from the store."""
    http_status: int = 404


class PermissionDenied(MutError):
    """Raised when an agent tries to access outside its scope."""
    http_status: int = 403


class AuthenticationError(MutError):
    """Raised on invalid / expired / missing auth tokens."""
    http_status: int = 401


class ConflictError(MutError):
    """Raised when a merge conflict cannot be auto-resolved."""
    http_status: int = 409


class LockError(MutError):
    """Raised when a scope lock cannot be acquired."""
    http_status: int = 409


class DirtyWorkdirError(MutError):
    """Raised when pull would overwrite uncommitted local changes."""
    http_status: int = 400


class NetworkError(MutError):
    """Raised on server communication failures."""
    http_status: int = 502


class PayloadTooLargeError(MutError):
    """Raised when request body exceeds the size limit."""
    http_status: int = 413


class ValidationError(MutError):
    """Raised when request data fails schema/semantic validation."""
    http_status: int = 422
