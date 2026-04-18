"""Mut directory and configuration constants + unified config I/O."""

import json
import os
import sys
import warnings
from pathlib import Path

# ── Agent-side (.mut/) ───────────────────────
MUT_DIR = ".mut"
OBJECTS_DIR = "objects"
SNAPSHOTS_FILE = "snapshots.json"
MANIFEST_FILE = "manifest.json"
HEAD_FILE = "HEAD"
REMOTE_HEAD_FILE = "REMOTE_HEAD"
CONFIG_FILE = "config.json"
CREDENTIAL_FILE = "credential"
IGNORE_FILE = ".mutignore"

# ── Server-side (.mut-server/) ───────────────
MUT_SERVER_DIR = ".mut-server"
SERVER_OBJECTS_DIR = "objects"
SERVER_CURRENT_DIR = "current"
SERVER_SCOPES_DIR = "scopes"
SERVER_HISTORY_DIR = "history"
SERVER_LOCKS_DIR = "locks"
SERVER_LATEST_FILE = "latest"
SERVER_ROOT_FILE = "root"
SERVER_CONFIG_FILE = "config.json"
SERVER_AUDIT_DIR = "audit"

BUILTIN_IGNORE = {
    ".mut", ".mut-server", ".git", ".DS_Store",
    "__pycache__", ".env", "node_modules", ".venv",
}

# SHA-256 truncated hex length: 16 hex chars = 64 bits.
# TODO: HASH_LEN = 32 (128 bits) would be safer against collisions, but
# changing it would break compatibility with PuppyOne server which also uses 16.
HASH_LEN = 16


def _secure_file(path: Path):
    """Set file permissions to 0o600 (user-only read/write) on POSIX systems."""
    if sys.platform != "win32":
        try:
            os.chmod(str(path), 0o600)
        except OSError:
            pass  # best-effort; may fail on some filesystems


def normalize_path(path: str) -> str:
    """Strip leading/trailing slashes for consistent path comparison.

    Rejects paths containing '..' segments to prevent path traversal attacks.
    """
    clean = path.strip("/")
    if clean and ".." in clean.split("/"):
        raise ValueError(f"path traversal not allowed: {path}")
    return clean


# ── Unified config I/O ───────────────────────

def load_config(mut_root: Path) -> dict:
    cfg_path = mut_root / CONFIG_FILE
    if not cfg_path.exists():
        return {}
    return json.loads(cfg_path.read_text(encoding="utf-8"))


def save_config(mut_root: Path, cfg: dict):
    from mut.foundation.fs import write_json
    cfg_path = mut_root / CONFIG_FILE
    write_json(cfg_path, cfg)
    _secure_file(cfg_path)


def load_env(workdir: Path) -> dict:
    """Read key=value pairs from .env file in workdir. Returns dict."""
    env_path = workdir / ".env"
    if not env_path.exists():
        return {}
    result = {}
    for line in env_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        result[key.strip()] = value.strip().strip("'\"")
    return result


def get_client_credential(mut_root: Path, workdir: Path) -> tuple[str, str]:
    """Get (credential, user_identity) for client operations.

    Reads from .env first (MUT_KEY, MUT_USER), falls back to
    .mut/credential and config.json for backwards compatibility.
    """
    from mut.foundation.fs import read_text

    env = load_env(workdir)
    credential = env.get("MUT_KEY", "")
    user_identity = env.get("MUT_USER", "")

    if not credential:
        cred_path = mut_root / CREDENTIAL_FILE
        if cred_path.exists():
            credential = read_text(cred_path).strip()
            # Warn if credential file has overly permissive permissions
            if sys.platform != "win32":
                try:
                    mode = os.stat(str(cred_path)).st_mode & 0o777
                    if mode & 0o077:
                        warnings.warn(
                            f"credential file {cred_path} has permissions "
                            f"{oct(mode)} — should be 0o600. "
                            f"Run: chmod 600 {cred_path}",
                            stacklevel=2,
                        )
                except OSError:
                    pass
            _secure_file(cred_path)

    # Fallback: read credential from config.json
    if not credential:
        cfg = load_config(mut_root)
        credential = cfg.get("credential", "")
        if not user_identity:
            user_identity = cfg.get("user_identity", "")
    else:
        if not user_identity:
            cfg = load_config(mut_root)
            user_identity = cfg.get("user_identity", "")

    return credential, user_identity
