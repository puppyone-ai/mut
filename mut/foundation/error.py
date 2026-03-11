"""Mut error types."""


class MutError(Exception):
    """Base error for all Mut operations."""


class NotARepoError(MutError):
    """Raised when operating outside a .mut/ repository."""


class SnapshotNotFoundError(MutError):
    """Raised when a requested snapshot ID does not exist."""


class ObjectNotFoundError(MutError):
    """Raised when a hash-addressed object is missing from the store."""


class PermissionDenied(MutError):
    """Raised when an agent tries to access outside its scope."""


class ConflictError(MutError):
    """Raised when a merge conflict cannot be auto-resolved."""


class DirtyWorkdirError(MutError):
    """Raised when pull would overwrite uncommitted local changes."""


class NetworkError(MutError):
    """Raised on server communication failures."""
