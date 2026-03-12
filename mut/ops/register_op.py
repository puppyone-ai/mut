"""mut register — Register with a server using an invite URL.

1. POST /invite/<id> to the server
2. Receive agent_id + token
3. Save credentials locally for future clone/push/pull
"""

import json
import urllib.request
import urllib.error
from urllib.parse import urlparse

from mut.foundation.error import NetworkError
from mut.foundation.credentials import save_credential


def register(invite_url: str) -> dict:
    """Register using an invite URL. Returns registration info."""
    parsed = urlparse(invite_url)
    server_url = f"{parsed.scheme}://{parsed.hostname}:{parsed.port or 80}"

    req = urllib.request.Request(
        invite_url,
        data=b"{}",
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            data = json.loads(resp.read().decode())
    except urllib.error.HTTPError as e:
        try:
            detail = json.loads(e.read().decode())
            msg = detail.get("error", str(e))
        except Exception:
            msg = str(e)
        raise NetworkError(f"registration failed ({e.code}): {msg}")
    except urllib.error.URLError as e:
        raise NetworkError(f"cannot reach server: {e.reason}")

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
