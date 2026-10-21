import sys
from contextlib import AsyncExitStack, asynccontextmanager
from pathlib import Path

from langchain_mcp_adapters.client import MultiServerMCPClient
from langchain_mcp_adapters.tools import load_mcp_tools

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
    """호출마다 MCP 서버 프로세스를 새로 띄운다 (단발성 실행용)."""
    client = make_client()
    return {t.name: t for t in await client.get_tools()}


@asynccontextmanager
async def open_tools():
    """서버별 세션을 계속 열어 두고 도구를 돌려준다 (대화형 REPL처럼 호출이 많을 때)."""
    client = make_client()
    async with AsyncExitStack() as stack:
        tools = {}
        for name in client.connections:
            session = await stack.enter_async_context(client.session(name))
            tools.update({t.name: t for t in await load_mcp_tools(session)})
        yield tools
