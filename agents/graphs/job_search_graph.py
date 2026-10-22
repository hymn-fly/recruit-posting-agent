"""채용공고 조회 Agent (RAG): plan -> retrieve -> rerank_explain."""
import json

from langgraph.graph import END, START, StateGraph

from agents.llm import LLM
from agents.mcp_client import parse_mcp_tool_result
from agents.state import JobSearchPlan, JobSearchState, Ranking

TOP_K = 20
BRIEF_LEN = 400   # rerank 프롬프트에 넣는 필드별 최대 글자 수


def _brief(r: dict) -> dict:
    keys = ("id", "company", "industry", "title", "location", "min_years", "category",
            "responsibilities", "required_qualifications", "preferred_qualifications")
    return {k: (r[k][:BRIEF_LEN] if isinstance(r.get(k), str) else r.get(k)) for k in keys}


def format_candidates(candidates: list[dict]) -> str:
    lines = []
    for i, c in enumerate(candidates, 1):
        meta = " · ".join(str(v) for v in (c.get("category"), c.get("location"),
                                           f"경력 {c['min_years']}년+" if c.get("min_years") else None,
                                           f"마감 {c['deadline']}" if c.get("deadline") else None) if v)
        lines.append(f"{i}. [{c['company']}] {c['title']} (id={c['id']}, 유사도 {c['similarity']:.2f})\n"
                     f"   {meta}\n   → {c['reason']}" + (f"\n   {c['url']}" if c.get("url") else ""))
    return "\n".join(lines)


def make_graph(tools: dict):
    planner = LLM(schema=JobSearchPlan)
    ranker = LLM(schema=Ranking, primary_timeout=30)

    async def plan(state: JobSearchState) -> JobSearchState:
        p = await planner.ainvoke(
            "사용자 요청을 채용공고 의미 검색 계획으로 바꿔라. query는 공고의 '하는 일/자격요건'과 "
            "비교될 문장이므로 원하는 역할과 경험·기술을 구체적으로 서술한다. "
            "필터(category/company_name/location/max_years)는 사용자가 명시한 것만 채우고 나머지는 null.\n\n"
            f"사용자 프로필 요약: {state.get('profile') or '(없음)'}\n"
            f"요청: {state['question']}")
        return {"plan": p}

    async def retrieve(state: JobSearchState) -> JobSearchState:
        args = {k: v for k, v in state["plan"].model_dump().items() if v is not None}
        raw = await tools["semantic_search_jobs"].ainvoke({**args, "limit": TOP_K})
        return {"results": parse_mcp_tool_result(raw)}

    async def rerank_explain(state: JobSearchState) -> JobSearchState:
        if not state["results"]:
            cond = state["plan"].model_dump(exclude_none=True)
            return {"candidates": [], "answer": f"현재 조건에 맞는 공고가 없습니다. (검색 조건: {cond})"}
        ranking = await ranker.ainvoke(
            "아래 후보 공고 중 사용자 요청(과 프로필)에 가장 잘 맞는 것을 최대 5건 골라 순서대로 나열하라. "
            "판단 근거는 공고의 하는 일·필수/우대 역량에 실제로 적힌 내용만 쓴다. 후보에 없는 id는 쓰지 않는다.\n\n"
            f"사용자 프로필 요약: {state.get('profile') or '(없음)'}\n"
            f"요청: {state['question']}\n\n"
            f"후보(JSON):\n{json.dumps([_brief(r) for r in state['results']], ensure_ascii=False)}")
        by_id = {r["id"]: r for r in state["results"]}
        candidates = [{**by_id[p.id], "reason": p.reason} for p in ranking.picks if p.id in by_id][:5]
        if not candidates:
            return {"candidates": [], "answer": "검색은 됐지만 요청과 잘 맞는 공고를 찾지 못했습니다."}
        return {"candidates": candidates,
                "answer": "추천 공고:\n" + format_candidates(candidates)
                          + "\n\n번호로 골라 기업분석이나 내 역량 비교를 요청할 수 있습니다."}

    g = StateGraph(JobSearchState)
    g.add_node("plan", plan)
    g.add_node("retrieve", retrieve)
    g.add_node("rerank_explain", rerank_explain)
    g.add_edge(START, "plan")
    g.add_edge("plan", "retrieve")
    g.add_edge("retrieve", "rerank_explain")
    g.add_edge("rerank_explain", END)
    return g.compile()
