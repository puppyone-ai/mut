"""Server-side scope management — subtree boundary definitions.

A scope defines a subtree of the project tree:
  {
      "id": "scope-src",
      "path": "/src/",
      "exclude": ["/src/vendor/"]
  }

Scopes are pure geometry — they define WHERE a subtree is, not WHO
can access it. Access control is handled by the auth layer.
"""

from __future__ import annotations

from pathlib import Path

from mut.foundation.fs import (
    read_json, write_json,
    async_read_json, async_write_json, async_iterdir,
)


class ScopeManager:
    """Manages scope definition files in .mut-server/scopes/."""

    def __init__(self, scopes_dir: Path):
        self.dir = scopes_dir

    def add(self, scope_id: str, path: str,
            exclude: list | None = None) -> dict:
        """Create a new scope definition."""
        scope = {"id": scope_id, "path": path, "exclude": exclude or []}
        write_json(self.dir / f"{scope_id}.json", scope)
        return scope

    def get_by_id(self, scope_id: str) -> dict | None:
        """Look up a scope by its ID. Returns a copy or None."""
        path = self.dir / f"{scope_id}.json"
        if not path.exists():
            return None
        return read_json(path)

    async def async_add(self, scope_id: str, path: str,
                        exclude: list | None = None) -> dict:
        scope = {"id": scope_id, "path": path, "exclude": exclude or []}
        await async_write_json(self.dir / f"{scope_id}.json", scope)
        return scope

    async def async_get_by_id(self, scope_id: str) -> dict | None:
        path = self.dir / f"{scope_id}.json"
        if not path.exists():
            return None
        return await async_read_json(path)

    def list_all(self) -> list[dict]:
        """Return all scope definitions."""
        if not self.dir.exists():
            return []
        scopes = []
        for f in sorted(self.dir.iterdir()):
            if f.suffix == ".json":
                scopes.append(read_json(f))
        return scopes
