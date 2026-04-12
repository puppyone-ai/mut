"""Content hashing utilities (SHA-256, truncated to HASH_LEN hex chars)."""

import hashlib
from pathlib import Path

from mut.foundation.config import HASH_LEN


def hash_bytes(data: bytes) -> str:
    """Return truncated SHA-256 hex digest of data."""
    return hashlib.sha256(data).hexdigest()[:HASH_LEN]


def hash_file(path: Path) -> str:
    """Return truncated SHA-256 hex digest of a file's contents (streamed)."""
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while True:
            chunk = f.read(8192)
            if not chunk:
                break
            h.update(chunk)
    return h.hexdigest()[:HASH_LEN]
