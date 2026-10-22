"""채용공고 조회 Agent (RAG): plan -> retrieve -> rerank_explain.

plan에서 질문을 semantic_search_jobs 파라미터로 바꾼다. 직무명(제목)은 키워드 필터(title_keywords),
하는 일·필수/우대 역량은 컬럼별 의미 검색 질의(q_*)로 채운다.
로그(INFO, logger "agents.graphs.job_search_graph"): 변환된 파라미터, 검색 결과(id·회사·제목·유사도), 최종 추천.
"""
import json
import logging

from langgraph.graph import END, START, StateGraph

from agents.llm import LLM
from agents.mcp_client import parse_mcp_tool_result
from agents.state import JobSearchPlan, JobSearchState, Ranking

log = logging.getLogger(__name__)

TOP_K = 20
BRIEF_LEN = 400   # rerank 프롬프트에 넣는 필드별 최대 글자 수
SIMILARITY_LOG_LABELS = (("점수", "similarity"), ("하는일", "sim_responsibilities"),
                         ("필수", "sim_required"), ("우대", "sim_preferred"))

PLAN_PROMPT = (
    "사용자 요청을 채용공고 검색 파라미터로 바꿔라. 필요 없는 필드는 null.\n"
    "- 직무명(예: MLOps, 백엔드, LLM 엔지니어)을 말했으면 title_keywords에 제목에 들어갈 짧은 키워드를 "
    "한글/영문 표기로 넣는다. 제목 부분일치 필터라서 '개발자'·'엔지니어' 같은 흔한 단어는 넣지 않는다.\n"
    "- 원하는 업무는 q_responsibilities, 갖춘(요구되는) 기술·경력은 q_required, 있으면 좋은 경험은 q_preferred에 "
    "공고 본문에 실제로 적혀 있을 법한 문장으로 쓴다(예: 'PyTorch 실무 경력 3년 이상'). 해당되는 것만, 여러 개 가능. "
    "직무명만 말한 요청이 아니면 최소 하나는 채운다.\n"
    "- 프로필 요약이 주어지고 '나한테 맞는'처럼 본인 기준을 원하면, 프로필의 기술·경험을 q_required/q_preferred에 반영한다.\n"
    "- category/company_name/location/max_years는 사용자가 명시한 것만.\n\n"
)


def _brief(r: dict) -> dict:
    keys = ("id", "company", "industry", "title", "location", "min_years", "category",
            "responsibilities", "required_qualifications", "preferred_qualifications")
    return {k: (r[k][:BRIEF_LEN] if isinstance(r.get(k), str) else r.get(k)) for k in keys}


def format_similarity_for_display(value) -> str:
    return "-" if value is None else f"{value:.2f}"


def format_search_rows_for_log(rows: list[dict]) -> str:
    """검색 결과를 로그용 한 줄씩으로 줄인다 (본문은 빼고 식별 정보와 질의한 컬럼의 유사도만)."""
    lines = []
    for rank, row in enumerate(rows, 1):
        similarities = " ".join(f"{label}={format_similarity_for_display(row.get(key))}"
                                for label, key in SIMILARITY_LOG_LABELS if key in row)
        lines.append(f"    {rank:>2}. id={row.get('id')} [{row.get('company')}] {row.get('title')}  {similarities}")
    return "\n".join(lines)


def format_candidates(candidates: list[dict]) -> str:
    lines = []
    for i, c in enumerate(candidates, 1):
        meta = " · ".join(str(v) for v in (c.get("category"), c.get("location"),
                                           f"경력 {c['min_years']}년+" if c.get("min_years") else None,
                                           f"마감 {c['deadline']}" if c.get("deadline") else None) if v)
        lines.append(f"{i}. [{c['company']}] {c['title']} "
                     f"(id={c['id']}, 유사도 {format_similarity_for_display(c.get('similarity'))})\n"
                     f"   {meta}\n   → {c['reason']}" + (f"\n   {c['url']}" if c.get("url") else ""))
    return "\n".join(lines)


def make_graph(tools: dict):
    planner = LLM(schema=JobSearchPlan)
    ranker = LLM(schema=Ranking, primary_timeout=30)

    async def plan(state: JobSearchState) -> JobSearchState:
        p = await planner.ainvoke(
            PLAN_PROMPT
            + f"사용자 프로필 요약: {state.get('profile') or '(없음)'}\n"
            + f"요청: {state['question']}")
        log.info("plan: 요청 %r\n  → 검색 파라미터 %s", state["question"],
                 json.dumps(p.model_dump(exclude_none=True), ensure_ascii=False))
        return {"plan": p}

    async def retrieve(state: JobSearchState) -> JobSearchState:
        # 값이 있는 파라미터만 넘긴다 (빈 리스트·빈 문자열도 제외)
        search_args = {k: v for k, v in state["plan"].model_dump().items() if v}
        raw = await tools["semantic_search_jobs"].ainvoke({**search_args, "limit": TOP_K})
        results = parse_mcp_tool_result(raw)
        log.info("retrieve: semantic_search_jobs 결과 %d건%s", len(results),
                 "\n" + format_search_rows_for_log(results) if results else "")
        return {"results": results}

    async def rerank_explain(state: JobSearchState) -> JobSearchState:
        if not state["results"]:
            cond = state["plan"].model_dump(exclude_none=True)
            return {"candidates": [], "answer": f"현재 조건에 맞는 공고가 없습니다. (검색 조건: {cond})"}

        # 후보 중 최대 5건을 LLM이 고르고, 검색 결과에 없는 id는 버린다
        ranking = await ranker.ainvoke(
            "아래 후보 공고 중 사용자 요청(과 프로필)에 가장 잘 맞는 것을 최대 5건 골라 순서대로 나열하라. "
            "판단 근거는 공고의 하는 일·필수/우대 역량에 실제로 적힌 내용만 쓴다. 후보에 없는 id는 쓰지 않는다.\n\n"
            f"사용자 프로필 요약: {state.get('profile') or '(없음)'}\n"
            f"요청: {state['question']}\n\n"
            f"후보(JSON):\n{json.dumps([_brief(r) for r in state['results']], ensure_ascii=False)}")
        by_id = {r["id"]: r for r in state["results"]}
        candidates = [{**by_id[p.id], "reason": p.reason} for p in ranking.picks if p.id in by_id][:5]
        log.info("rerank_explain: 추천 %s", [c["id"] for c in candidates])

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
