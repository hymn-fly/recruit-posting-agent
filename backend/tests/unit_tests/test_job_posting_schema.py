"""공고 공통 스키마(common.schemas.JobPosting)와 이를 쓰는 query_server·supervisor 경계 테스트."""
import json
from datetime import date
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from pydantic import ValidationError

from agents.graphs.supervisor import fetch_job_posting_by_id
from common.schemas import JobPosting
from mcp_server import query_server


def make_posting_database_row(**overrides) -> dict:
    return {
        "id": 7, "company": "테스트 회사", "industry": "핀테크", "title": "AI 엔지니어", "url": None,
        "location": "서울", "employment_type": "정규직", "min_years": 3, "category": "AI Engineer",
        "responsibilities": "LLM 서비스 개발", "required_qualifications": "Python", "preferred_qualifications": None,
        "posted_at": date(2026, 11, 1), "deadline": date(2026, 12, 31), "is_active": True, **overrides,
    }


async def test_get_job_posting_returns_job_posting_model(monkeypatch):
    connection_pool = SimpleNamespace(fetchrow=AsyncMock(return_value=make_posting_database_row()))
    monkeypatch.setattr(query_server, "pool", AsyncMock(return_value=connection_pool))

    posting = await query_server.get_job_posting(7)

    assert isinstance(posting, JobPosting)
    assert posting.company == "테스트 회사"
    assert posting.deadline == date(2026, 12, 31)


async def test_get_job_posting_raises_when_posting_is_missing(monkeypatch):
    connection_pool = SimpleNamespace(fetchrow=AsyncMock(return_value=None))
    monkeypatch.setattr(query_server, "pool", AsyncMock(return_value=connection_pool))

    with pytest.raises(ValueError, match="공고 없음"):
        await query_server.get_job_posting(999)


async def test_get_job_posting_tool_exposes_output_schema_with_field_descriptions():
    tools = {tool.name: tool for tool in await query_server.mcp.list_tools()}
    output_schema = tools["get_job_posting"].outputSchema

    assert set(JobPosting.model_fields) == set(output_schema["properties"])
    assert set(output_schema["required"]) == {"id", "company", "title"}
    assert output_schema["properties"]["company"]["description"] == "회사명 (companies.name)"


def make_get_job_posting_tools(mcp_text: str) -> dict:
    """get_job_posting tool이 주어진 텍스트 하나를 content block으로 돌려주는 가짜 tools."""
    tool = SimpleNamespace(ainvoke=AsyncMock(return_value=[{"type": "text", "text": mcp_text}]))
    return {"get_job_posting": tool}


async def test_fetch_job_posting_by_id_returns_validated_model_and_dumps_json_dict():
    raw_posting_from_mcp = make_posting_database_row(deadline="2026-12-31", posted_at="2026-11-01")
    tools = make_get_job_posting_tools(json.dumps(raw_posting_from_mcp, ensure_ascii=False))

    posting = await fetch_job_posting_by_id(tools, 7)

    assert isinstance(posting, JobPosting)
    assert posting.deadline == date(2026, 12, 31)
    assert tools["get_job_posting"].ainvoke.await_args.args == ({"id": 7},)
    state_posting = posting.model_dump(mode="json")   # 그래프 상태에는 이 JSON dict가 저장된다
    assert state_posting["deadline"] == "2026-12-31"
    assert set(state_posting) == set(JobPosting.model_fields)


async def test_fetch_job_posting_by_id_returns_none_when_tool_result_is_empty():
    tools = make_get_job_posting_tools("Error executing tool get_job_posting: 공고 없음: id=999")

    assert await fetch_job_posting_by_id(tools, 999) is None


async def test_fetch_job_posting_by_id_raises_when_result_breaks_schema():
    raw_posting_without_title = make_posting_database_row()
    raw_posting_without_title.pop("title")
    tools = make_get_job_posting_tools(json.dumps(raw_posting_without_title, ensure_ascii=False, default=str))

    with pytest.raises(ValidationError):
        await fetch_job_posting_by_id(tools, 7)
