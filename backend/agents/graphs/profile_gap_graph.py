"""내 프로필 Agent: 공고 대비 부족한 점과 보완 방법. load -> decompose -> match -> synthesize."""
import asyncio
import json

from langgraph.graph import END, START, StateGraph

from agents.llm import LLM
from agents.mcp_client import get_first_mcp_result, parse_mcp_tool_result
from agents.state import Assessments, ProfileGapState, Requirements

EVIDENCE_K = 3   # 요구사항마다 가져올 프로필 원문 조각 수
NO_PROFILE = ("적재된 프로필 자료가 없습니다. 먼저 이력서/경력기술서/포트폴리오를 넣어주세요:\n"
              "  uv run python scripts/ingest_profile.py data/profile/resume.pdf --kind resume")



def make_graph(tools: dict):
    decomposer = LLM(schema=Requirements, primary_timeout=30)
    judge = LLM(schema=Assessments, primary_timeout=60)
    writer = LLM(primary_timeout=60)

    async def load(state: ProfileGapState) -> ProfileGapState:
        return {"summary": get_first_mcp_result(await tools["get_profile_summary"].ainvoke({}))}

    def route_after_load(state: ProfileGapState) -> str:
        return "decompose" if state["summary"] else "no_profile"

    async def no_profile(state: ProfileGapState) -> ProfileGapState:
        return {"answer": NO_PROFILE}

    async def decompose(state: ProfileGapState) -> ProfileGapState:
        p = state["posting"]
        reqs = await decomposer.ainvoke(
            "아래 채용공고의 필수 역량(required)과 우대 역량(preferred)을 평가 가능한 개별 요구사항으로 나눠라. "
            "하는 일에서 드러나는 핵심 기술 요구도 required로 포함할 수 있다. 공고에 없는 요구는 만들지 않는다.\n\n"
            f"제목: {p.get('title')}\n하는 일:\n{p.get('responsibilities') or '-'}\n"
            f"필수 역량:\n{p.get('required_qualifications') or '-'}\n"
            f"우대 역량:\n{p.get('preferred_qualifications') or '-'}\n최소 경력: {p.get('min_years')}")
        return {"requirements": reqs.items[:12]}

    async def match(state: ProfileGapState) -> ProfileGapState:
        results = await asyncio.gather(*(
            tools["search_profile"].ainvoke({"query": r.text, "limit": EVIDENCE_K})
            for r in state["requirements"]))
        evidence = [parse_mcp_tool_result(r) for r in results]
        blocks = []
        for i, (req, ev) in enumerate(zip(state["requirements"], evidence)):
            chunks = "\n".join(f"  - ({e['filename']}) {e['text']}" for e in ev)
            blocks.append(f"[{i}] ({req.type}) {req.text}\n 관련 프로필 원문:\n{chunks}")
        res = await judge.ainvoke(
            "각 요구사항을 지원자가 충족하는지 판정하라. 근거는 아래 '관련 프로필 원문'과 프로필 요약에 "
            "실제로 적힌 내용만 쓴다. 원문에 없으면 '미충족'(또는 간접 근거만 있으면 '부분')으로 판정하고, "
            "evidence에는 원문을 그대로 짧게 인용한다(지어내지 않는다).\n\n"
            f"프로필 요약: {json.dumps(state['summary'], ensure_ascii=False)}\n\n" + "\n\n".join(blocks))
        return {"evidence": evidence, "assessments": res.items}

    async def synthesize(state: ProfileGapState) -> ProfileGapState:
        p, reqs = state["posting"], state["requirements"]
        table = "\n".join(
            f"- [{a.status}] ({reqs[a.index].type}) {reqs[a.index].text} — {a.comment}"
            + (f" / 근거: \"{a.evidence}\"" if a.evidence else "")
            for a in state["assessments"] if 0 <= a.index < len(reqs))
        research = state.get("research_report")
        msg = await writer.ainvoke(
            "너는 지원자를 잘 아는 커리어 코치다. 아래 판정 결과를 바탕으로 한국어 마크다운 리포트를 써라.\n"
            "섹션: ## 요약(적합도 한 줄) / ## 강점(충족 항목, 근거 인용) / ## 부족한 점(우선순위: 필수 미충족 > "
            "필수 부분 > 우대 미충족, 항목당 한 줄) / ## 보완 방법(우선순위 상위 4개 항목만, 항목별로 ①단기 학습 "
            "②증명용 사이드 프로젝트 ③이력서·경력기술서에 이미 있는 경험을 공고 언어로 다시 쓴 예시 문장, 각 1~2문장) / "
            "## 면접에서 대비할 질문 3개.\n"
            "전체 2500자 이내로 간결하게. 판정 결과와 프로필에 없는 경력은 만들지 않는다."
            + (" 기업분석 리포트가 있으면 보완 방향을 회사의 사업 방향과 연결한다." if research else "") + "\n\n"
            f"공고: [{p.get('company')}] {p.get('title')} (최소 경력 {p.get('min_years')})\n"
            f"프로필 요약: {json.dumps(state['summary'], ensure_ascii=False)}\n\n판정 결과:\n{table}"
            + (f"\n\n기업분석 리포트:\n{research}" if research else ""))
        return {"answer": msg.text}

    g = StateGraph(ProfileGapState)
    g.add_node("load", load)
    g.add_node("no_profile", no_profile)
    g.add_node("decompose", decompose)
    g.add_node("match", match)
    g.add_node("synthesize", synthesize)
    g.add_edge(START, "load")
    g.add_conditional_edges("load", route_after_load, ["decompose", "no_profile"])
    g.add_edge("no_profile", END)
    g.add_edge("decompose", "match")
    g.add_edge("match", "synthesize")
    g.add_edge("synthesize", END)
    return g.compile()
