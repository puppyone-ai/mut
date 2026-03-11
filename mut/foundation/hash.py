"""Content hashing utilities (SHA-256, truncated to HASH_LEN hex chars)."""

import hashlib
from pathlib import Path

from mut.foundation.config import HASH_LEN


def hash_bytes(data: bytes) -> str:
    """Return truncated SHA-256 hex digest of data."""
    return hashlib.sha256(data).hexdigest()[:HASH_LEN]


def hash_file(path: Path) -> str:
    """Return truncated SHA-256 hex digest of a file's contents."""
    return hash_bytes(path.read_bytes())
