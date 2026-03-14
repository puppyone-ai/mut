"""Server-side scope (permission) management with sync and async support."""

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

    def _make_scope(self, scope_id: str, path: str, agents: list,
                    mode: str, exclude: list) -> dict:
        return {
            "id": scope_id,
            "path": path,
            "exclude": exclude or [],
            "agents": agents,
            "mode": mode,
        }

    def add(self, scope_id: str, path: str, agents: list,
            mode: str = "rw", exclude: list = None) -> dict:
        scope = self._make_scope(scope_id, path, agents, mode, exclude)
        write_json(self.dir / f"{scope_id}.json", scope)
        return scope

    def get_for_agent(self, agent_id: str) -> dict | None:
        """Return the scope config for a given agent, or None."""
        if not self.dir.exists():
            return None
        for f in sorted(self.dir.iterdir()):
            if f.suffix == ".json":
                scope = read_json(f)
                if agent_id in scope.get("agents", []):
                    return scope
        return None

    async def async_add(self, scope_id: str, path: str, agents: list,
                        mode: str = "rw", exclude: list = None) -> dict:
        scope = self._make_scope(scope_id, path, agents, mode, exclude)
        await async_write_json(self.dir / f"{scope_id}.json", scope)
        return scope

    async def async_get_for_agent(self, agent_id: str) -> dict | None:
        """Async: return the scope config for a given agent, or None."""
        children = await async_iterdir(self.dir)
        for f in children:
            if f.suffix == ".json":
                scope = await async_read_json(f)
                if agent_id in scope.get("agents", []):
                    return scope
        return None
