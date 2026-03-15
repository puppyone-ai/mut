"""mut register — Register with a server using an invite URL.

1. POST /invite/<id> to the server
2. Receive agent_id + token
3. Save credentials locally for future clone/push/pull
"""

from __future__ import annotations
from urllib.parse import urlparse

from mut.foundation.credentials import save_credential
from mut.foundation.transport import _make_request


def register(invite_url: str) -> dict:
    """Register using an invite URL. Returns registration info."""
    parsed = urlparse(invite_url)
    server_url = f"{parsed.scheme}://{parsed.hostname}:{parsed.port or 80}"

    data = _make_request(invite_url, data={})

    save_credential(
        server_url,
        agent_id=data["agent_id"],
        token=data["token"],
        project=data.get("project", ""),
        scope=data.get("scope", {}).get("path", ""),
    )

    return {
        "server": server_url,
        "agent_id": data["agent_id"],
        "project": data.get("project", ""),
        "scope": data.get("scope", {}),
    }
