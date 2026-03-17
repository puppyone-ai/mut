"""Scope permission management — path prefix matching + exclude lists.

A scope defines which directory subtree an agent can access:
  {
    "id": "scope-abc123",
    "path": "/src/",
    "exclude": ["/src/components/"],
    "agents": ["agent-A"],
    "mode": "rw"
  }
"""

from mut.core.protocol import normalize_path


def check_path_permission(scope: dict, file_path: str, action: str = "read") -> bool:
    """Check if file_path is within scope and not excluded.

    file_path should be relative to project root, e.g. "src/main.py".
    scope["path"] is like "/src/".
    """
    scope_path = normalize_path(scope["path"])
    norm_path = normalize_path(file_path)

    if scope_path:
        if not (norm_path.startswith(scope_path + "/") or norm_path == scope_path):
            return False

    for excluded in scope.get("exclude", []):
        exc = normalize_path(excluded)
        if norm_path.startswith(exc + "/") or norm_path == exc:
            return False

    if action == "write" and scope.get("mode", "r") == "r":
        return False

    return True
