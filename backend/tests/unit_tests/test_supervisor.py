"""Supervisor(agents/graphs/supervisor.py) 단위 테스트: 라우팅과 공고 선택만 보고, 하위 Agent 그래프는 가짜로 바꾼다."""
from types import SimpleNamespace

import pytest
from langgraph.checkpoint.memory import MemorySaver

from agents.graphs import company_research_graph, job_search_graph, profile_gap_graph, supervisor
from agents.state import Route

ALL_TOOLS = ("semantic_search_jobs", "get_cached_research", "save_research",
             "get_profile_summary", "search_profile", "get_job_posting")


class FakeGraph:
    def __init__(self, result):
        self.result = result
        self.inputs: list[dict] = []

    async def ainvoke(self, state):
        self.inputs.append(state)
        return self.result


@pytest.fixture
def agents(monkeypatch):
    fakes = SimpleNamespace(
        job=FakeGraph({"answer": "추천 공고 목록", "candidates": [
            {"id": 146, "company": "SK", "title": "A", "url": None},
            {"id": 149, "company": "SK", "title": "B", "url": None}]}),
        research=FakeGraph({"report": "리서치 리포트", "answer": "리서치 리포트"}),
        gap=FakeGraph({"answer": "갭 리포트"}))
    monkeypatch.setattr(job_search_graph, "make_graph", lambda tools: fakes.job)
    monkeypatch.setattr(company_research_graph, "make_graph", lambda tools: fakes.research)
    monkeypatch.setattr(profile_gap_graph, "make_graph", lambda tools: fakes.gap)
    monkeypatch.setenv("TAVILY_API_KEY", "test")
    return fakes


@pytest.fixture
def app(make_tools, make_posting):
    tools = make_tools(**{n: [] for n in ALL_TOOLS})
    tools["get_job_posting"].response = lambda a: make_posting(id=a["id"], title=f"공고{a['id']}")
    graph = supervisor.make_graph(tools, checkpointer=MemorySaver())
    config = {"configurable": {"thread_id": "t"}}

    async def turn(text):
        out = await graph.ainvoke(supervisor.turn_input(text), config)
        return out
    return SimpleNamespace(graph=graph, tools=tools, turn=turn)


async def test_Agent가_필요없으면_reply로_바로_답한다(fake_llm, agents, app):
    fake_llm.set(Route, [Route(next="FINISH", reply="안녕하세요", reason="인사")])

    out = await app.turn("안녕")

    assert out["messages"][-1].content == "안녕하세요"
    assert agents.job.inputs == []


async def test_공고검색_후_FINISH하고_추천목록을_state에_남긴다(fake_llm, agents, app):
    fake_llm.set(Route, [Route(next="job_search", reason="검색"), Route(next="FINISH", reason="끝")])

    out = await app.turn("LLM 공고 찾아줘")

    assert agents.job.inputs[0]["question"] == "LLM 공고 찾아줘"
    assert [c["id"] for c in out["candidates"]] == [146, 149]
    assert out["messages"][-1].content == "추천 공고 목록"


async def test_번호로_공고를_골라_기업분석_후_갭분석까지_이어간다(fake_llm, agents, app):
    fake_llm.set(Route, [
        Route(next="job_search", reason="검색"), Route(next="FINISH", reason="끝"),
        Route(next="company_research", posting_number=2, reason="2번 분석"),
        Route(next="profile_gap", reason="이어서 갭"), Route(next="FINISH", reason="끝")])

    await app.turn("LLM 공고 찾아줘")
    out = await app.turn("2번 회사 분석하고 내 부족한 점도")

    assert app.tools["get_job_posting"].calls == [{"id": 149}]
    assert agents.research.inputs[0]["posting"]["id"] == 149
    gap_in = agents.gap.inputs[0]
    assert gap_in["posting"]["id"] == 149 and gap_in["research_report"] == "리서치 리포트"
    text = out["messages"][-1].content
    assert "# 기업분석" in text and "# 내 프로필" in text   # 여러 Agent 결과를 묶어서 답함


async def test_공고가_선택되지_않았으면_되묻는다(fake_llm, agents, app):
    fake_llm.set(Route, [Route(next="profile_gap", reason="갭 분석 요청")])

    out = await app.turn("내 부족한 점 알려줘")

    assert agents.gap.inputs == []
    assert "어떤 공고" in out["messages"][-1].content


async def test_같은_Agent를_한_턴에_두번_실행하지_않는다(fake_llm, agents, app):
    fake_llm.set(Route, [Route(next="job_search", reason="검색"), Route(next="job_search", reason="또 검색")])

    await app.turn("공고 찾아줘")

    assert len(agents.job.inputs) == 1


async def test_TAVILY_키가_없으면_기업분석_대신_안내한다(fake_llm, agents, app, monkeypatch):
    monkeypatch.delenv("TAVILY_API_KEY")
    fake_llm.set(Route, [Route(next="company_research", posting_id=154, reason="분석"),
                         Route(next="FINISH", reason="끝")])

    out = await app.turn("154번 공고 기업분석")

    assert agents.research.inputs == []
    assert "TAVILY_API_KEY" in out["messages"][-1].content
