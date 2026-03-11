"""Server-side version history management."""

from datetime import datetime, timezone
from pathlib import Path

from mut.foundation.fs import read_json, write_json, write_text


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
               conflicts: list = None, root_hash: str = ""):
        entry = {
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

    def get_since(self, since_version: int, scope_path: str = None) -> list:
        """Return history entries after since_version.

        If scope_path is given, only entries whose scope overlaps
        with scope_path are included (prevents cross-scope info leak).
        """
        result = []
        latest = self.get_latest_version()
        norm_scope = scope_path.strip("/") if scope_path else None
        for v in range(since_version + 1, latest + 1):
            path = self.dir / f"{v:06d}.json"
            if path.exists():
                entry = read_json(path)
                if norm_scope is not None:
                    entry_scope = entry.get("scope", "/").strip("/")
                    if entry_scope and norm_scope and entry_scope != norm_scope:
                        if not (entry_scope.startswith(norm_scope + "/") or
                                norm_scope.startswith(entry_scope + "/")):
                            continue
                    entry = self._redact_for_scope(entry, norm_scope)
                result.append(entry)
        return result

    @staticmethod
    def _redact_for_scope(entry: dict, scope: str) -> dict:
        """Strip change details for paths outside the requesting scope."""
        if "changes" in entry:
            entry = dict(entry)
            entry["changes"] = [
                c for c in entry["changes"]
                if c["path"].strip("/").startswith(scope)
            ]
        return entry

    def get_entry(self, version: int) -> dict:
        path = self.dir / f"{version:06d}.json"
        if path.exists():
            return read_json(path)
        return None
