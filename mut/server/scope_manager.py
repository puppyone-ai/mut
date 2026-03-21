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

    def find_by_path_prefix(self, path_prefix: str) -> list[dict]:
        """Find scopes whose path starts with the given prefix.

        Default implementation filters list_all(); backends with indexed
        storage (e.g. PuppyOne's SupabaseScopeBackend) can override for
        better performance.
        """
        from mut.foundation.config import normalize_path
        prefix = normalize_path(path_prefix)
        results = []
        for scope in self.list_all():
            sp = normalize_path(scope.get("path", ""))
            if not prefix or sp.startswith(prefix + "/") or sp == prefix:
                results.append(scope)
        return results


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

    def find_by_path_prefix(self, path_prefix: str) -> list[dict]:
        return self._backend.find_by_path_prefix(path_prefix)

    def update_path(self, scope_id: str, new_path: str) -> dict | None:
        """Update the path of an existing scope (e.g. after folder rename)."""
        scope = self._backend.get(scope_id)
        if not scope:
            return None
        scope["path"] = new_path
        self._backend.put(scope_id, scope)
        return scope

    def split_scope(self, old_scope_id: str,
                    new_scopes: list[dict]) -> list[dict]:
        """Split one scope into multiple new scopes.

        Each entry in new_scopes should be {"id": ..., "path": ..., "exclude": [...]}.
        The old scope is deleted after new scopes are created.
        Returns the list of created scope dicts.
        """
        old = self._backend.get(old_scope_id)
        if not old:
            raise ValueError(f"scope '{old_scope_id}' not found")

        created = []
        for ns in new_scopes:
            scope = self.add(ns["id"], ns["path"], ns.get("exclude"))
            created.append(scope)

        self._backend.delete(old_scope_id)
        return created

    def merge_scopes(self, scope_ids: list[str],
                     new_scope_id: str, new_path: str,
                     new_exclude: list | None = None) -> dict:
        """Merge multiple scopes into a single new scope.

        Old scopes are deleted. Returns the new scope dict.
        """
        for sid in scope_ids:
            if not self._backend.get(sid):
                raise ValueError(f"scope '{sid}' not found")

        new_scope = self.add(new_scope_id, new_path, new_exclude)

        for sid in scope_ids:
            self._backend.delete(sid)

        return new_scope
