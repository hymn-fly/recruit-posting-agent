"""채용공고 조회 Agent(agents/graphs/job_search_graph.py) 단위 테스트."""
import pytest

from agents.graphs import job_search_graph
from agents.state import JobSearchPlan, Pick, Ranking


@pytest.fixture
def with_rerank(monkeypatch):
    """LLM 재순위(rerank_explain)를 켠다. 기본은 꺼져 있다(USE_RERANK=False)."""
    monkeypatch.setattr(job_search_graph, "USE_RERANK", True)


async def test_재순위를_쓰지_않으면_검색_결과_앞에서부터_요청한_개수만큼_llm_호출_없이_돌려준다(fake_llm, make_tools, make_posting):
    results = [make_posting(id=i, title=f"공고{i}") for i in range(1, 9)]
    fake_llm.set(JobSearchPlan, JobSearchPlan(title_keywords=["LLM"]))

    out = await job_search_graph.make_graph(make_tools(semantic_search_jobs=results)).ainvoke({"question": "LLM 공고"})

    assert [c["id"] for c in out["candidates"]] == [1, 2, 3, 4, 5]   # 검색 순서 그대로, 기본 5건
    assert "reason" not in out["candidates"][0]
    assert "1. [SK] 공고1" in out["answer"] and "→" not in out["answer"]   # 근거 줄이 없다
    assert fake_llm.prompts[Ranking] == []   # 재순위 LLM은 부르지 않는다


async def test_추천은_ranking_순서이고_검색결과에_없는_id는_버린다(fake_llm, make_tools, make_posting, with_rerank):
    results = [make_posting(id=i, title=f"공고{i}") for i in (1, 2, 3)]
    tools = make_tools(semantic_search_jobs=results)
    fake_llm.set(JobSearchPlan, JobSearchPlan(title_keywords=["LLM"], q_responsibilities="LLM 에이전트 개발",
                                               q_preferred="", category="LLM Engineer"))
    fake_llm.set(Ranking, Ranking(picks=[Pick(id=3, reason="RAG 일치"), Pick(id=999, reason="없는 공고"),
                                         Pick(id=1, reason="Agent 일치")]))

    out = await job_search_graph.make_graph(tools).ainvoke({"question": "LLM 에이전트 공고"})

    assert [c["id"] for c in out["candidates"]] == [3, 1]
    assert out["candidates"][0]["reason"] == "RAG 일치"
    assert "1. [SK] 공고3" in out["answer"]
    # 값이 있는 파라미터만 넘기고(None·빈 문자열 제외), top-k로 검색
    assert tools["semantic_search_jobs"].calls == [
        {"title_keywords": ["LLM"], "q_responsibilities": "LLM 에이전트 개발", "category": "LLM Engineer",
         "limit": job_search_graph.TOP_K}]


async def test_검색결과가_없으면_rerank를_부르지_않는다(fake_llm, make_tools):
    tools = make_tools(semantic_search_jobs=[])
    fake_llm.set(JobSearchPlan, JobSearchPlan(q_responsibilities="양자컴퓨팅 알고리즘 연구", location="부산"))

    out = await job_search_graph.make_graph(tools).ainvoke({"question": "부산 양자컴퓨팅 공고"})

    assert out["candidates"] == []
    assert "공고가 없습니다" in out["answer"]
    assert fake_llm.prompts[Ranking] == []


async def test_프로필이_있으면_planner_프롬프트에_들어간다(fake_llm, make_tools, make_posting, with_rerank):
    tools = make_tools(semantic_search_jobs=[make_posting()])
    fake_llm.set(JobSearchPlan, JobSearchPlan(q_required="FastAPI 백엔드 개발 경험"))
    fake_llm.set(Ranking, Ranking(picks=[]))

    out = await job_search_graph.make_graph(tools).ainvoke(
        {"question": "나한테 맞는 공고", "profile": "백엔드 4년 / 스킬: FastAPI"})

    assert "백엔드 4년" in fake_llm.prompts[JobSearchPlan][0]
    assert "찾지 못했습니다" in out["answer"]   # 검색은 됐지만 고른 게 없음


async def test_의미검색_질의가_없으면_유사도_대신_대시로_표시한다(fake_llm, make_tools, make_posting):
    tools = make_tools(semantic_search_jobs=[make_posting(id=7, similarity=None)])
    fake_llm.set(JobSearchPlan, JobSearchPlan(title_keywords=["MLOps"]))
    fake_llm.set(Ranking, Ranking(picks=[Pick(id=7, reason="직무명 일치")]))

    out = await job_search_graph.make_graph(tools).ainvoke({"question": "MLOps 공고 보여줘"})

    assert "(id=7, 유사도 -)" in out["answer"]


async def test_마감_임박_요청은_마감순_검색_조건을_넘기고_개수는_요청대로_추천한다(fake_llm, make_tools, make_posting, with_rerank):
    results = [make_posting(id=i, title=f"공고{i}", deadline=f"2026-11-{10 + i:02d}") for i in range(1, 13)]
    tools = make_tools(semantic_search_jobs=results)
    fake_llm.set(JobSearchPlan, JobSearchPlan(category="AI Engineer", sort_by_deadline=True, result_count=10))
    fake_llm.set(Ranking, Ranking(picks=[Pick(id=i, reason="마감 임박") for i in range(1, 13)]))

    out = await job_search_graph.make_graph(tools).ainvoke({"question": "마감 임박한 AI Engineer 공고 10개"})

    # result_count는 검색 도구에 넘기지 않고, 정렬 조건만 넘긴다
    assert tools["semantic_search_jobs"].calls == [
        {"category": "AI Engineer", "sort_by_deadline": True, "limit": job_search_graph.TOP_K}]
    assert [c["id"] for c in out["candidates"]] == list(range(1, 11))   # 10개까지 (예전엔 5개가 상한이었다)
    assert "마감 2026-11-11" in out["answer"]
    prompt = fake_llm.prompts[Ranking][0]
    assert "최대 10건" in prompt and "마감이 가까운 순으로 이미 정렬" in prompt
    assert '"deadline": "2026-11-11"' in prompt   # 재순위 프롬프트에 마감일이 들어간다


async def test_개수를_말하지_않으면_5건까지_추천하고_상한은_10건이다(fake_llm, make_tools, make_posting):
    results = [make_posting(id=i, title=f"공고{i}") for i in range(1, 16)]
    fake_llm.set(Ranking, Ranking(picks=[Pick(id=i, reason="일치") for i in range(1, 16)]))

    for requested, expected_count in ((None, 5), (3, 3), (50, 10)):
        fake_llm.set(JobSearchPlan, JobSearchPlan(title_keywords=["LLM"], result_count=requested))
        out = await job_search_graph.make_graph(make_tools(semantic_search_jobs=results)).ainvoke(
            {"question": "LLM 공고"})
        assert len(out["candidates"]) == expected_count


async def test_재순위_프롬프트는_후보가_이미_걸러졌음을_알리고_빈_목록을_막는다(fake_llm, make_tools, make_posting, with_rerank):
    # 마감 같은 조건을 후보에서 확인할 수 없다고 LLM이 판단해 빈 목록을 내던 문제(CHANGELOG #37)의 회귀 방지
    tools = make_tools(semantic_search_jobs=[make_posting(id=1, deadline="2026-11-13")])
    fake_llm.set(JobSearchPlan, JobSearchPlan(category="AI Engineer", sort_by_deadline=True))
    fake_llm.set(Ranking, Ranking(picks=[Pick(id=1, reason="마감 임박")]))

    await job_search_graph.make_graph(tools).ainvoke({"question": "마감이 얼마 남지 않은 AI Engineer 공고"})

    prompt = fake_llm.prompts[Ranking][0]
    assert "이미 검색 조건" in prompt and "다시 판단하지 말고" in prompt
    assert "빈 목록을 돌려주지 말고" in prompt
    assert '"deadline": "2026-11-13"' in prompt
