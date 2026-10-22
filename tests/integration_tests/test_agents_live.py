"""각 Agent를 실제 DB·LLM·Tavily로 단독 호출한다.

실행: uv run pytest --run-integration tests/integration_tests -s
하나만: uv run pytest --run-integration -k job_search -s
"""
import pytest
from langgraph.checkpoint.memory import MemorySaver

from agents.graphs import company_research_graph, job_search_graph, profile_gap_graph, supervisor
from agents.mcp_client import open_tools, parse_mcp_tool_result
from tests.integration_tests.conftest import pick_posting, profile_summary, require_env

pytestmark = pytest.mark.integration


async def test_MCP_semantic_search_jobs는_필터_후_질의한_컬럼별_유사도로_정렬한다():
    async with open_tools() as tools:
        rows = parse_mcp_tool_result(await tools["semantic_search_jobs"].ainvoke(
            {"title_keywords": ["MLOps", "LLMOps", "Inference"], "q_required": "Kubernetes 운영과 모델 서빙 경험",
             "limit": 5}))
        unfiltered = parse_mcp_tool_result(await tools["semantic_search_jobs"].ainvoke(
            {"q_required": "Kubernetes 운영과 모델 서빙 경험", "limit": 5}))
    assert rows, "검색 결과 없음"
    assert all(any(k.lower() in r["title"].lower() for k in ("mlops", "llmops", "inference")) for r in rows)
    assert {"id", "similarity", "sim_required"} <= rows[0].keys()
    assert "sim_responsibilities" not in rows[0]   # 질의하지 않은 컬럼은 비교하지 않는다
    assert rows == sorted(rows, key=lambda r: r["similarity"], reverse=True)
    assert len(unfiltered) == 5


async def test_job_search_Agent():
    require_env("GOOGLE_API_KEY", "OPENAI_API_KEY")
    async with open_tools() as tools:
        out = await job_search_graph.make_graph(tools).ainvoke(
            {"question": "LLM 기반 에이전트를 서비스에 붙이고 RAG 파이프라인을 만드는 공고"})
    print("\n" + out["answer"])
    assert out["candidates"], out["answer"]
    result_ids = {r["id"] for r in out["results"]}
    assert all(c["id"] in result_ids for c in out["candidates"])   # 검색 결과 밖의 공고를 만들지 않음


async def test_profile_gap_Agent():
    require_env("GOOGLE_API_KEY", "OPENAI_API_KEY")
    async with open_tools() as tools:
        if not await profile_summary(tools):
            pytest.skip("프로필 자료 없음 (scripts/ingest_profile.py 먼저 실행)")
        posting = await pick_posting(tools)
        out = await profile_gap_graph.make_graph(tools).ainvoke({"posting": posting})
    print("\n" + out["answer"])
    assert out["requirements"] and out["assessments"]
    assert "## 부족한 점" in out["answer"]


async def test_company_research_Agent():
    """Tavily 쿼터를 쓴다. 같은 회사·계열사 리서치가 14일 안에 있으면 role 토픽만 검색한다."""
    require_env("GOOGLE_API_KEY", "OPENAI_API_KEY", "TAVILY_API_KEY")
    async with open_tools() as tools:
        posting = await pick_posting(tools)
        out = await company_research_graph.make_graph(tools).ainvoke({"posting": posting})
    print("\n" + out["answer"])
    assert out["report"].startswith(f"# {out['plan'].target_company} 기업분석")
    assert "## 출처" in out["report"]


async def test_supervisor_한_턴():
    require_env("GOOGLE_API_KEY", "OPENAI_API_KEY")
    async with open_tools() as tools:
        graph = supervisor.make_graph(tools, checkpointer=MemorySaver())
        out = await graph.ainvoke(supervisor.turn_input("MLOps 모델 서빙 공고 찾아줘"),
                                  {"configurable": {"thread_id": "it"}})
    print("\n" + out["messages"][-1].content)
    assert out.get("candidates"), out["messages"][-1].content
