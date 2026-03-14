"""Mut error types with HTTP status code mapping."""


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
    """Raised when token verification fails (bad signature, expired, malformed)."""
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


class ValidationError(MutError):
    """Raised for invalid request payloads."""
    http_status = 422


class PayloadTooLargeError(MutError):
    """Raised when request body exceeds size limit."""
    http_status = 413
