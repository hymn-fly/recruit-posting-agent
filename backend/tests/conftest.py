"""공용 테스트 픽스처.

- unit_tests: LLM / MCP 도구 / Tavily를 가짜로 바꿔 네트워크 없이 각 Agent 그래프의 흐름만 검증한다.
- integration_tests: 실제 Neon DB·LLM·Tavily를 호출한다. `--run-integration`을 줄 때만 실행된다.
"""
import json
from collections import defaultdict
from types import SimpleNamespace

import pytest


# ---------------------------------------------------------------
# 통합 테스트 opt-in
# ---------------------------------------------------------------
def pytest_addoption(parser):
    parser.addoption("--run-integration", action="store_true", default=False,
                     help="실제 DB/LLM/Tavily를 호출하는 integration 테스트도 실행")


def pytest_collection_modifyitems(config, items):
    if config.getoption("--run-integration"):
        return
    skip = pytest.mark.skip(reason="--run-integration을 주면 실행")
    for item in items:
        if "integration" in item.keywords:
            item.add_marker(skip)


# ---------------------------------------------------------------
# 가짜 MCP 도구
# ---------------------------------------------------------------
def mcp_result(value):
    """실제 MCP 어댑터처럼 text content block 리스트로 감싼다 (agents.mcp_client.parse_mcp_tool_result가 푸는 형식)."""
    return [{"type": "text", "text": json.dumps(value, ensure_ascii=False)}]


class FakeTool:
    """`ainvoke(args)`만 흉내 낸다. response는 값 또는 args를 받는 함수."""

    def __init__(self, name: str, response=None):
        self.name = name
        self.response = response
        self.calls: list[dict] = []

    async def ainvoke(self, args: dict):
        self.calls.append(args)
        value = self.response(args) if callable(self.response) else self.response
        return mcp_result(value)


@pytest.fixture
def make_tools():
    """`make_tools(semantic_search_jobs=[...], get_profile_summary=lambda a: {...})` → {name: FakeTool}."""
    def _make(**responses):
        return {name: FakeTool(name, resp) for name, resp in responses.items()}
    return _make


# ---------------------------------------------------------------
# 가짜 LLM
# ---------------------------------------------------------------
class FakeLLMRegistry:
    """schema(pydantic 클래스, 텍스트 응답은 None)별로 돌려줄 응답을 등록한다.

    값이 list면 호출마다 앞에서부터 하나씩, 함수면 prompt를 받아 계산한다.
    prompts[schema]에 실제로 받은 prompt가 쌓인다.
    """

    def __init__(self):
        self.responses: dict = {}
        self.prompts: dict = defaultdict(list)

    def set(self, schema, response):
        self.responses[schema] = list(response) if isinstance(response, list) else response

    def answer(self, schema, prompt: str):
        self.prompts[schema].append(prompt)
        if schema not in self.responses:
            raise AssertionError(f"FakeLLM: {getattr(schema, '__name__', schema)} 응답이 등록되지 않음")
        r = self.responses[schema]
        if isinstance(r, list):
            if not r:
                raise AssertionError(f"FakeLLM: {getattr(schema, '__name__', schema)} 응답을 다 썼음")
            r = r.pop(0)
        elif callable(r) and not isinstance(r, type):
            r = r(prompt)
        return SimpleNamespace(text=r) if schema is None else r


@pytest.fixture
def fake_llm(monkeypatch):
    """agents.* 모듈의 LLM 클래스를 가짜로 바꾼다. 반환값(registry)에 응답을 등록해서 쓴다."""
    registry = FakeLLMRegistry()

    class FakeLLM:
        def __init__(self, max_output_tokens=None, schema=None, **_):
            self.schema = schema

        async def ainvoke(self, prompt: str):
            return registry.answer(self.schema, prompt)

    for mod in ("agents.graphs.job_search_graph", "agents.graphs.profile_gap_graph",
                "agents.graphs.company_research_graph", "agents.graphs.supervisor"):
        monkeypatch.setattr(f"{mod}.LLM", FakeLLM)
    return registry


# ---------------------------------------------------------------
# 테스트 데이터
# ---------------------------------------------------------------
def posting(id=149, company="SK", title="AI Agent 개발자", **kw) -> dict:
    base = {"id": id, "company": company, "industry": "IT서비스", "title": title,
            "url": f"https://example.com/{id}", "location": "서울", "employment_type": "경력",
            "min_years": 3, "category": "LLM Engineer", "deadline": "2026-12-31",
            "responsibilities": "LLM Agent 백엔드 개발", "required_qualifications": "Python, RAG 경험",
            "preferred_qualifications": "LangGraph 운영 경험", "similarity": 0.8}
    return {**base, **kw}


@pytest.fixture
def make_posting():
    return posting
