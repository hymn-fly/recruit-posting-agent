"""내 프로필 Agent(agents/graphs/profile_gap_graph.py) 단위 테스트."""
from agents.graphs import profile_gap_graph
from agents.state import Assessment, Assessments, Requirement, Requirements

SUMMARY = {"headline": "백엔드 4년", "total_years": 4, "skills": ["Python"], "domains": [],
           "projects": [], "education_certs": [], "strengths": []}


def _search_profile(args):
    return [{"filename": "resume.md", "kind": "resume", "chunk_index": 0,
             "text": f"근거: {args['query']}", "similarity": 0.8}]


async def test_프로필이_없으면_적재_안내만_한다(fake_llm, make_tools, make_posting):
    tools = make_tools(get_profile_summary={}, search_profile=[])

    out = await profile_gap_graph.make_graph(tools).ainvoke({"posting": make_posting()})

    assert out["answer"] == profile_gap_graph.NO_PROFILE
    assert fake_llm.prompts[Requirements] == []
    assert tools["search_profile"].calls == []


async def test_요구사항마다_근거를_검색하고_판정표로_리포트를_쓴다(fake_llm, make_tools, make_posting):
    tools = make_tools(get_profile_summary=SUMMARY, search_profile=_search_profile)
    fake_llm.set(Requirements, Requirements(items=[
        Requirement(text="경력 5년 이상", type="required"),
        Requirement(text="Kubernetes 운영", type="preferred")]))
    fake_llm.set(Assessments, Assessments(items=[
        Assessment(index=0, status="미충족", evidence="백엔드 4년", comment="1년 부족"),
        Assessment(index=1, status="충족", evidence=None, comment="근거 있음"),
        Assessment(index=7, status="충족", comment="범위 밖 index는 무시")]))
    fake_llm.set(None, "## 요약\n리포트")

    out = await profile_gap_graph.make_graph(tools).ainvoke(
        {"posting": make_posting(), "research_report": "SK는 에이전틱 엔터프라이즈를 지향"})

    assert out["answer"] == "## 요약\n리포트"
    assert [c["query"] for c in tools["search_profile"].calls] == ["경력 5년 이상", "Kubernetes 운영"]
    assert "근거: Kubernetes 운영" in fake_llm.prompts[Assessments][0]   # 검색된 원문이 판정 프롬프트에 들어감
    writer_prompt = fake_llm.prompts[None][0]
    assert '[미충족] (required) 경력 5년 이상 — 1년 부족 / 근거: "백엔드 4년"' in writer_prompt
    assert "범위 밖" not in writer_prompt
    assert "에이전틱 엔터프라이즈" in writer_prompt   # 기업분석 리포트 반영
