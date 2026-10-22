"""integration 테스트 공용 도우미.

MCP 세션(anyio cancel scope)은 연 task에서 닫아야 한다. pytest-asyncio의 yield 픽스처는 setup/teardown을
다른 task에서 돌리므로, 각 테스트가 `async with open_tools() as tools:`로 직접 연다.
"""
import os
from pathlib import Path

import pytest
from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parents[2] / ".env")

from agents.mcp_client import parse_mcp_tool_result  # noqa: E402


async def pick_posting(tools) -> dict:
    """DB에서 하는 일·자격요건이 채워진 활성 공고 1건."""
    rows = parse_mcp_tool_result(await tools["semantic_search_jobs"].ainvoke({"query": "LLM 에이전트 개발", "limit": 5}))
    rows = [r for r in rows if r.get("responsibilities") and r.get("required_qualifications")]
    if not rows:
        pytest.skip("임베딩된 공고가 없음 (scripts/embed_postings.py 먼저 실행)")
    return parse_mcp_tool_result(await tools["get_job_posting"].ainvoke({"id": rows[0]["id"]}))[0]


async def profile_summary(tools) -> dict:
    items = parse_mcp_tool_result(await tools["get_profile_summary"].ainvoke({}))
    return items[0] if items else {}


def require_env(*names):
    missing = [n for n in names if not os.environ.get(n)]
    if missing:
        pytest.skip(f"환경변수 없음: {', '.join(missing)}")
