"""Local credential storage for mut API keys.

Credentials are stored per-server in ~/.mut/credentials.json:
  {
    "http://server:9742": {
      "agent_id": "agent-abc123",
      "token": "eyJ...",
      "project": "my-project",
      "scope": "/src/"
    }
  }
"""

import json
from pathlib import Path
from urllib.parse import urlparse


CREDENTIALS_DIR = Path.home() / ".mut"
CREDENTIALS_FILE = CREDENTIALS_DIR / "credentials.json"


def _server_key(server_url: str) -> str:
    """Normalize server URL to a consistent key (scheme + host + port)."""
    p = urlparse(server_url)
    port = p.port or (443 if p.scheme == "https" else 80)
    return f"{p.scheme}://{p.hostname}:{port}"


def save_credential(server_url: str, agent_id: str, token: str,
                    project: str = "", scope: str = ""):
    CREDENTIALS_DIR.mkdir(parents=True, exist_ok=True)
    creds = load_all()
    creds[_server_key(server_url)] = {
        "agent_id": agent_id,
        "token": token,
        "project": project,
        "scope": scope,
    }
    CREDENTIALS_FILE.write_text(
        json.dumps(creds, indent=2, ensure_ascii=False), encoding="utf-8"
    )


def get_credential(server_url: str) -> dict | None:
    """Return credential dict for a server, or None."""
    creds = load_all()
    return creds.get(_server_key(server_url))


def load_all() -> dict:
    if not CREDENTIALS_FILE.exists():
        return {}
    return json.loads(CREDENTIALS_FILE.read_text(encoding="utf-8"))
