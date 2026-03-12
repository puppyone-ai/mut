"""Mut directory and configuration constants + unified config I/O."""

import json
from pathlib import Path

# ── Agent-side (.mut/) ───────────────────────
MUT_DIR = ".mut"
OBJECTS_DIR = "objects"
SNAPSHOTS_FILE = "snapshots.json"
MANIFEST_FILE = "manifest.json"
HEAD_FILE = "HEAD"
REMOTE_HEAD_FILE = "REMOTE_HEAD"
CONFIG_FILE = "config.json"
TOKEN_FILE = "token"
IGNORE_FILE = ".mutignore"

# ── Server-side (.mut-server/) ───────────────
MUT_SERVER_DIR = ".mut-server"
SERVER_OBJECTS_DIR = "objects"
SERVER_CURRENT_DIR = "current"
SERVER_SCOPES_DIR = "scopes"
SERVER_HISTORY_DIR = "history"
SERVER_KEYS_DIR = "keys"
SERVER_LOCKS_DIR = "locks"
SERVER_LATEST_FILE = "latest"
SERVER_ROOT_FILE = "root"          # global root tree hash
SERVER_CONFIG_FILE = "config.json"
SECRET_KEY_FILE = "secret.key"
SERVER_AUDIT_DIR = "audit"
SERVER_INVITES_DIR = "invites"

BUILTIN_IGNORE = {".mut", ".mut-server", ".git", ".DS_Store", "__pycache__", ".env", "node_modules", ".venv"}

# SHA-256 truncated hex length: 16 hex chars = 64 bits.
# Collision probability is ~1 in 2^32 at 2^16 objects (birthday bound).
# Sufficient for typical agent project sizes. Increase if needed.
HASH_LEN = 16


# ── Unified config I/O ───────────────────────

def load_config(mut_root: Path) -> dict:
    """Load agent-side config from .mut/config.json. Returns {} if missing."""
    cfg_path = mut_root / CONFIG_FILE
    if not cfg_path.exists():
        return {}
    return json.loads(cfg_path.read_text(encoding="utf-8"))


def save_config(mut_root: Path, cfg: dict):
    """Save agent-side config to .mut/config.json."""
    from mut.foundation.fs import write_json
    write_json(mut_root / CONFIG_FILE, cfg)
