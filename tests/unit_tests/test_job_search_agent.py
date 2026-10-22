"""채용공고 조회 Agent(agents/graphs/job_search_graph.py) 단위 테스트."""
from agents.graphs import job_search_graph
from agents.state import JobSearchPlan, Pick, Ranking


async def test_추천은_ranking_순서이고_검색결과에_없는_id는_버린다(fake_llm, make_tools, make_posting):
    results = [make_posting(id=i, title=f"공고{i}") for i in (1, 2, 3)]
    tools = make_tools(semantic_search_jobs=results)
    fake_llm.set(JobSearchPlan, JobSearchPlan(query="LLM 에이전트 개발", category="LLM Engineer"))
    fake_llm.set(Ranking, Ranking(picks=[Pick(id=3, reason="RAG 일치"), Pick(id=999, reason="없는 공고"),
                                         Pick(id=1, reason="Agent 일치")]))

    out = await job_search_graph.make_graph(tools).ainvoke({"question": "LLM 에이전트 공고"})

    assert [c["id"] for c in out["candidates"]] == [3, 1]
    assert out["candidates"][0]["reason"] == "RAG 일치"
    assert "1. [SK] 공고3" in out["answer"]
    # 명시된 필터만 넘기고(None 제외), top-k로 검색
    assert tools["semantic_search_jobs"].calls == [
        {"query": "LLM 에이전트 개발", "category": "LLM Engineer", "limit": job_search_graph.TOP_K}]


async def test_검색결과가_없으면_rerank를_부르지_않는다(fake_llm, make_tools):
    tools = make_tools(semantic_search_jobs=[])
    fake_llm.set(JobSearchPlan, JobSearchPlan(query="양자컴퓨팅", location="부산"))

    out = await job_search_graph.make_graph(tools).ainvoke({"question": "부산 양자컴퓨팅 공고"})

    assert out["candidates"] == []
    assert "공고가 없습니다" in out["answer"]
    assert fake_llm.prompts[Ranking] == []


async def test_프로필이_있으면_planner_프롬프트에_들어간다(fake_llm, make_tools, make_posting):
    tools = make_tools(semantic_search_jobs=[make_posting()])
    fake_llm.set(JobSearchPlan, JobSearchPlan(query="q"))
    fake_llm.set(Ranking, Ranking(picks=[]))

    out = await job_search_graph.make_graph(tools).ainvoke(
        {"question": "나한테 맞는 공고", "profile": "백엔드 4년 / 스킬: FastAPI"})

    assert "백엔드 4년" in fake_llm.prompts[JobSearchPlan][0]
    assert "찾지 못했습니다" in out["answer"]   # 검색은 됐지만 고른 게 없음
