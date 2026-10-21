import sys
from pathlib import Path

from langchain_mcp_adapters.client import MultiServerMCPClient

ROOT = Path(__file__).resolve().parent.parent


def make_client() -> MultiServerMCPClient:
    return MultiServerMCPClient({
        "job-db": {
            "command": sys.executable,
            "args": [str(ROOT / "mcp_server" / "server.py")],
            "transport": "stdio",
        },
        "profile-db": {
            "command": sys.executable,
            "args": [str(ROOT / "mcp_server" / "profile_server.py")],
            "transport": "stdio",
        },
    })


async def get_tools() -> dict:
    client = make_client()
    return {t.name: t for t in await client.get_tools()}
