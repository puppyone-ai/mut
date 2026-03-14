"""Server-side version history management."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from mut.foundation.fs import read_json, write_json, write_text
from mut.core.protocol import normalize_path


class HistoryManager:
    """Manages version history in .mut-server/history/."""

    LATEST_FILE = "latest"
    ROOT_FILE = "root"

    def __init__(self, history_dir: Path):
        self.dir = history_dir

    def get_latest_version(self) -> int:
        return int((self.dir / self.LATEST_FILE).read_text().strip())

    def set_latest_version(self, version: int):
        write_text(self.dir / self.LATEST_FILE, str(version))

    def get_root_hash(self) -> str:
        root_file = self.dir / self.ROOT_FILE
        if not root_file.exists():
            return ""
        return root_file.read_text().strip()

    def set_root_hash(self, h: str):
        write_text(self.dir / self.ROOT_FILE, h)

    def record(self, version: int, who: str, message: str,
               scope_path: str, changes: list,
               conflicts: list | None = None, root_hash: str = ""):
        entry: dict = {
            "id": version,
            "who": who,
            "time": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "message": message,
            "scope": scope_path,
            "root": root_hash,
            "changes": changes,
        }
        if conflicts:
            entry["conflicts"] = [
                {"path": c.path, "strategy": c.strategy, "detail": c.detail,
                 "kept": c.kept}
                for c in conflicts
            ]
        write_json(self.dir / f"{version:06d}.json", entry)

    def get_since(self, since_version: int, scope_path: str | None = None) -> list:
        """Return history entries after since_version.

        If scope_path is given, only entries whose scope overlaps
        with scope_path are included (prevents cross-scope info leak).
        """
        result: list[dict] = []
        latest = self.get_latest_version()
        norm_scope = normalize_path(scope_path) if scope_path else None

        for v in range(since_version + 1, latest + 1):
            entry = self.get_entry(v)
            if entry is None:
                continue
            if norm_scope is not None:
                if not _scopes_overlap(entry.get("scope", "/"), norm_scope):
                    continue
                entry = _redact_for_scope(entry, norm_scope)
            result.append(entry)
        return result

    def get_entry(self, version: int) -> dict | None:
        path = self.dir / f"{version:06d}.json"
        if path.exists():
            return read_json(path)
        return None


def _scopes_overlap(entry_scope: str, requesting_scope: str) -> bool:
    """Check if two scope paths overlap (one is prefix of the other)."""
    es = normalize_path(entry_scope)
    rs = requesting_scope  # already normalized by caller
    if not es or not rs:
        return True
    return (es.startswith(rs + "/") or rs.startswith(es + "/") or es == rs)


def _redact_for_scope(entry: dict, scope: str) -> dict:
    """Strip change details for paths outside the requesting scope."""
    if "changes" not in entry:
        return entry
    redacted = dict(entry)
    redacted["changes"] = [
        c for c in entry["changes"]
        if normalize_path(c["path"]).startswith(scope)
    ]
    return redacted
