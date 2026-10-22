"""기업분석 Agent(agents/graphs/company_research_graph.py) 단위 테스트. Tavily도 가짜로 바꾼다."""
import pytest

from agents.graphs import company_research_graph
from agents.state import ResearchPlan, TopicQueries


class FakeTavily:
    queries: list[str] = []

    def __init__(self, **_):
        pass

    async def ainvoke(self, args):
        FakeTavily.queries.append(args["query"])
        q = args["query"]
        return {"results": [{"title": f"{q} 기사", "url": f"https://news/{q}", "content": "본문"}]}


@pytest.fixture
def fake_tavily(monkeypatch):
    FakeTavily.queries = []
    monkeypatch.setattr(company_research_graph, "TavilySearch", FakeTavily)
    return FakeTavily


def _plan():
    return ResearchPlan(target_company="티맵모빌리티", industry="모빌리티 플랫폼", competitors=["카카오모빌리티"],
                        topics=[TopicQueries(topic=t, queries=[f"{t}-q"]) for t in company_research_graph.TOPIC_TITLES])


async def test_캐시가_없으면_5개_토픽을_검색하고_출처를_전체_번호로_다시_매긴다(fake_llm, make_tools, make_posting, fake_tavily):
    tools = make_tools(get_cached_research={}, save_research={"id": 1})
    fake_llm.set(ResearchPlan, _plan())
    # 토픽 요약은 각자 로컬 [1]을 인용, 리포트는 [1]과 [3]만 인용
    fake_llm.set(None, lambda prompt: "요약 [1]" if "bullet" in prompt else "리포트 본문 [1][3]")

    out = await company_research_graph.make_graph(tools).ainvoke({"posting": make_posting(id=154)})

    assert sorted(fake_tavily.queries) == sorted(f"{t}-q" for t in company_research_graph.TOPIC_TITLES)
    assert tools["get_cached_research"].calls[0]["target_company"] == "티맵모빌리티"
    writer_prompt = fake_llm.prompts[None][-1]
    for n in range(1, 6):   # 토픽 5개의 로컬 [1]이 [1]~[5]로 바뀜
        assert f"요약 [{n}]" in writer_prompt
    assert out["report"].startswith("# 티맵모빌리티 기업분석")
    refs = out["report"].split("## 출처\n")[1].splitlines()
    assert [r.split("]")[0] for r in refs] == ["[1", "[3"]   # 인용된 출처만
    saved = tools["save_research"].calls[0]
    assert saved["target_company"] == "티맵모빌리티" and saved["posting_id"] == 154


async def test_Tavily_검색어_하나가_실패해도_나머지_결과로_요약하고_경고를_남긴다(
        fake_llm, make_tools, make_posting, fake_tavily, monkeypatch, caplog):
    async def fail_on_first_query(self, args):
        if args["query"].endswith("-q1"):
            raise RuntimeError("quota exceeded")
        return {"results": [{"title": "기사", "url": f"https://news/{args['query']}", "content": "본문"}]}

    monkeypatch.setattr(fake_tavily, "ainvoke", fail_on_first_query)
    plan = _plan()
    for t in plan.topics:
        t.queries = [f"{t.topic}-q1", f"{t.topic}-q2"]
    tools = make_tools(get_cached_research={}, save_research={"id": 3})
    fake_llm.set(ResearchPlan, plan)
    fake_llm.set(None, lambda prompt: "요약 [1]" if "bullet" in prompt else "리포트 [1]")

    out = await company_research_graph.make_graph(tools).ainvoke({"posting": make_posting(id=154)})

    assert all(t["sources"] == [{"title": "기사", "url": f"https://news/{t['topic']}-q2"}] for t in out["topics"])
    warnings = [r.getMessage() for r in caplog.records if r.levelname == "WARNING"]
    assert any("Tavily 호출 실패 'industry-q1' — RuntimeError: quota exceeded" in w for w in warnings)


async def test_회사_단위_토픽이_캐시에_있으면_role만_검색한다(fake_llm, make_tools, make_posting, fake_tavily):
    cached = {"topics": [{"topic": t, "summary": f"{t} 캐시 [1]", "sources": [{"title": t, "url": f"u/{t}"}]}
                         for t in ("industry", "outlook", "competitors", "company", "role")]}
    tools = make_tools(get_cached_research=cached, save_research={"id": 2})
    fake_llm.set(ResearchPlan, _plan())
    fake_llm.set(None, lambda prompt: "요약 [1]" if "bullet" in prompt else "리포트 [1]")

    out = await company_research_graph.make_graph(tools).ainvoke({"posting": make_posting(id=154)})

    assert fake_tavily.queries == ["role-q"]   # 캐시의 role은 재사용하지 않는다
    assert len(out["cached"]) == 4
    assert "재사용" in out["answer"]
