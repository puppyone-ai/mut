"""mut init — Initialize a new .mut/ repository.

Idempotent: if .mut/ already exists, silently keeps existing config.
Creates the directory structure needed for local operations (commit, status,
log, diff, checkout). Server connection is configured later via
``mut link access`` or ``mut clone``.
"""

from pathlib import Path

from mut.foundation.config import (
    MUT_DIR, OBJECTS_DIR, SNAPSHOTS_FILE, MANIFEST_FILE, HEAD_FILE, CONFIG_FILE,
)
from mut.foundation.fs import write_json, write_text
from mut.ops.repo import MutRepo

# Config schema version — bump when structure changes.
CONFIG_VERSION = 1


def init(workdir: str = ".") -> MutRepo:
    """Initialize a .mut/ repository in *workdir*.

    If ``.mut/`` already exists, reinitializes: upgrades config structure
    (adds missing fields, bumps version) but preserves content data
    (objects, snapshots, manifest) and user bindings (server, credential).
    Same behaviour as ``git init`` on an existing repo.

    Returns:
        (MutRepo, bool) — repo and whether this was a fresh init (True)
        or reinit (False). For API simplicity, returns just MutRepo.
    """
    root = Path(workdir).resolve()
    mut = root / MUT_DIR

    if mut.exists():
        # Reinit — upgrade config but keep content + bindings
        _reinit_config(mut)
        return MutRepo(workdir)

    # Fresh init
    (mut / OBJECTS_DIR).mkdir(parents=True)
    write_json(mut / SNAPSHOTS_FILE, [])
    write_json(mut / MANIFEST_FILE, {})
    write_json(mut / CONFIG_FILE, {
        "version": CONFIG_VERSION,
        "server": None,
    })
    write_text(mut / HEAD_FILE, "0")

    return MutRepo(workdir)


def _reinit_config(mut: Path) -> None:
    """Upgrade an existing .mut/config.json without losing user data."""
    import json

    cfg_path = mut / CONFIG_FILE
    if not cfg_path.exists():
        # Config missing entirely — recreate with defaults
        write_json(cfg_path, {"version": CONFIG_VERSION, "server": None})
        return

    cfg = json.loads(cfg_path.read_text(encoding="utf-8"))

    changed = False

    # Upgrade version
    if cfg.get("version", 0) < CONFIG_VERSION:
        cfg["version"] = CONFIG_VERSION
        changed = True

    # Ensure required fields exist
    if "server" not in cfg:
        cfg["server"] = None
        changed = True

    # Remove legacy fields (platform concepts MUT should not store)
    for legacy_key in ("scope", "project", "agent_id"):
        if legacy_key in cfg:
            del cfg[legacy_key]
            changed = True

    if changed:
        write_json(cfg_path, cfg)

    # Ensure directory structure is intact
    (mut / OBJECTS_DIR).mkdir(parents=True, exist_ok=True)
