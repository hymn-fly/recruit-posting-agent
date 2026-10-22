"""질의 에이전트: plan_search -> run_search -> synthesize_answer.

로그(INFO, logger "agents.query_graph"): 질문이 어떤 검색 파라미터로 변환됐는지, search_jobs에 어떤 인자로 호출해
어떤 공고가 돌아왔는지(id·회사·제목·유사도), 답변 길이를 남긴다.
"""
import json
import logging

from langgraph.graph import END, START, StateGraph

from agents.llm import LLM
from agents.state import QueryState, SearchParams

log = logging.getLogger("agents.query_graph")


def format_similarity_for_log(value) -> str:
    return "-" if value is None else f"{value:.3f}"


def format_search_rows_for_log(rows: list[dict]) -> str:
    """검색 결과를 로그용 한 줄씩으로 줄인다 (본문 필드는 빼고 식별 정보와 유사도만)."""
    lines = []
    for rank, row in enumerate(rows, 1):
        similarity_by_field = " ".join(
            f"{label}={format_similarity_for_log(row.get(key))}"
            for label, key in (("전체", "similarity"), ("하는일", "sim_responsibilities"),
                               ("필수", "sim_required"), ("우대", "sim_preferred"))
            if key in row)
        lines.append(f"    {rank:>2}. id={row.get('id')} [{row.get('company')}] {row.get('title')}  {similarity_by_field}")
    return "\n".join(lines)


def _parse(raw) -> list[dict]:
    """MCP tool 결과(list[dict], JSON 문자열, text content block)를 list[dict]로 정규화."""
    if isinstance(raw, tuple):
        raw = raw[0]
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except json.JSONDecodeError:
            return []
    out: list[dict] = []
    if isinstance(raw, list):
        for item in raw:
            if isinstance(item, dict) and item.get("type") == "text":
                try:
                    v = json.loads(item["text"])
                except json.JSONDecodeError:
                    continue
                out.extend(v if isinstance(v, list) else [v])
            elif isinstance(item, dict):
                out.append(item)
    return out


def make_graph(tools: dict):
    llm = LLM()
    planner = LLM(schema=SearchParams)

    async def plan_search(state: QueryState) -> QueryState:
        params = await planner.ainvoke(
            "사용자 질문을 채용공고 검색 파라미터로 변환해라. 필요 없는 필드는 null.\n"
            "- 직무명(예: 백엔드 개발자, ML 엔지니어)을 찾는 질문이면 title_keywords에 짧은 핵심 단어(한글/영문 표기 포함)를 넣는다. "
            "제목에 부분일치로 걸리므로 '개발자' 같은 흔한 단어는 피한다.\n"
            "- 하는 일에 대한 질문이면 q_responsibilities, 필수 자격요건이면 q_required, 우대사항이면 q_preferred에 "
            "공고 본문에 실제로 적혀 있을 법한 문장 형태(예: 'PyTorch 실무 경력 3년 이상')로 서술한다. 해당되는 필드만 채운다(여러 개 가능).\n"
            "- category 예: AI Engineer / ML Engineer / LLM Engineer / MLOps / Data. 명시된 경우만.\n\n"
            f"질문: {state['question']}")
        log.info("plan_search: 질문 %r\n  → 검색 파라미터 %s", state["question"],
                 json.dumps(params.model_dump(exclude_none=True), ensure_ascii=False))
        return {"params": params}

    async def run_search(state: QueryState) -> QueryState:
        # 값이 있는 파라미터만 넘긴다 (빈 리스트·빈 문자열도 제외)
        filled_params = {k: v for k, v in state["params"].model_dump().items() if v}
        search_jobs_args = {**filled_params, "only_active": True, "limit": 20}
        log.info("run_search: search_jobs 호출 인자 %s", json.dumps(search_jobs_args, ensure_ascii=False))

        raw = await tools["search_jobs"].ainvoke(search_jobs_args)
        results = _parse(raw)
        log.info("run_search: search_jobs 결과 %d건%s", len(results),
                 "\n" + format_search_rows_for_log(results) if results else "")
        return {"results": results}

    async def synthesize_answer(state: QueryState) -> QueryState:
        if not state["results"]:
            cond = state["params"].model_dump(exclude_none=True)
            log.info("synthesize_answer: 결과 없음 → LLM 호출 없이 안내")
            return {"answer": f"현재 조건에 맞는 공고가 없습니다. (검색 조건: {cond})"}
        msg = await llm.ainvoke(
            "아래 검색 결과만 근거로 질문에 답하고, 사용자 프로필과 맞는 공고를 추천 이유와 함께 설명해라. "
            "결과에 없는 공고·정보는 절대 만들지 말고, 링크가 있으면 포함해라.\n\n"
            f"사용자 프로필: {state.get('profile') or '(제공되지 않음)'}\n"
            f"질문: {state['question']}\n\n"
            f"검색 결과(JSON):\n{json.dumps(state['results'], ensure_ascii=False, default=str)}")
        log.info("synthesize_answer: 결과 %d건으로 답변 %d자 생성", len(state["results"]), len(msg.text))
        return {"answer": msg.text}  # content는 thinking/text 블록 리스트일 수 있어 text만 사용

    g = StateGraph(QueryState)
    g.add_node("plan_search", plan_search)
    g.add_node("run_search", run_search)
    g.add_node("synthesize_answer", synthesize_answer)
    g.add_edge(START, "plan_search")
    g.add_edge("plan_search", "run_search")
    g.add_edge("run_search", "synthesize_answer")
    g.add_edge("synthesize_answer", END)
    return g.compile()
