"""Server-side scope management with pluggable backends.

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

import abc
from pathlib import Path

from mut.foundation.fs import read_json, write_json


class ScopeBackend(abc.ABC):
    """Abstract interface for scope definition storage."""

    @abc.abstractmethod
    def get(self, scope_id: str) -> dict | None: ...

    @abc.abstractmethod
    def put(self, scope_id: str, scope: dict) -> None: ...

    @abc.abstractmethod
    def delete(self, scope_id: str) -> bool: ...

    @abc.abstractmethod
    def list_all(self) -> list[dict]: ...


class FileSystemScopeBackend(ScopeBackend):
    """One JSON file per scope in .mut-server/scopes/."""

    def __init__(self, scopes_dir: Path):
        self.dir = scopes_dir

    def get(self, scope_id: str) -> dict | None:
        path = self.dir / f"{scope_id}.json"
        if not path.exists():
            return None
        return read_json(path)

    def put(self, scope_id: str, scope: dict) -> None:
        write_json(self.dir / f"{scope_id}.json", scope)

    def delete(self, scope_id: str) -> bool:
        path = self.dir / f"{scope_id}.json"
        if path.exists():
            path.unlink()
            return True
        return False

    def list_all(self) -> list[dict]:
        if not self.dir.exists():
            return []
        scopes = []
        for f in sorted(self.dir.iterdir()):
            if f.suffix == ".json":
                scopes.append(read_json(f))
        return scopes


class ScopeManager:
    """Manages scope definitions via a pluggable ScopeBackend."""

    def __init__(self, backend: ScopeBackend):
        self._backend = backend

    def add(self, scope_id: str, path: str,
            exclude: list | None = None) -> dict:
        scope = {"id": scope_id, "path": path, "exclude": exclude or []}
        self._backend.put(scope_id, scope)
        return scope

    def get_by_id(self, scope_id: str) -> dict | None:
        return self._backend.get(scope_id)

    def delete(self, scope_id: str) -> bool:
        return self._backend.delete(scope_id)

    def list_all(self) -> list[dict]:
        return self._backend.list_all()

    def update_path(self, scope_id: str, new_path: str) -> dict | None:
        """Update the path of an existing scope (e.g. after folder rename)."""
        scope = self._backend.get(scope_id)
        if not scope:
            return None
        scope["path"] = new_path
        self._backend.put(scope_id, scope)
        return scope
