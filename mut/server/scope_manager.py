"""Server-side scope (permission) management."""

from pathlib import Path

from mut.foundation.fs import read_json, write_json


class ScopeManager:
    """Manages scope definition files in .mut-server/scopes/."""

    def __init__(self, scopes_dir: Path):
        self.dir = scopes_dir

    def add(self, scope_id: str, path: str, agents: list,
            mode: str = "rw", exclude: list = None) -> dict:
        scope = {
            "id": scope_id,
            "path": path,
            "exclude": exclude or [],
            "agents": agents,
            "mode": mode,
        }
        write_json(self.dir / f"{scope_id}.json", scope)
        return scope

    def get_for_agent(self, agent_id: str) -> dict:
        """Return the scope config for a given agent, or None."""
        if not self.dir.exists():
            return None
        for f in sorted(self.dir.iterdir()):
            if f.suffix == ".json":
                scope = read_json(f)
                if agent_id in scope.get("agents", []):
                    return scope
        return None
