"""질의 에이전트: plan_search -> run_search -> synthesize_answer."""
import json

from langgraph.graph import END, START, StateGraph

from agents.llm import LLM
from agents.state import QueryState, SearchParams


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
            "공고 본문에 실제로 적혀 있을 법한 문장 형태(예: "PyTorch 실무 경력 3년 이상")로 서술한다. 해당되는 필드만 채운다(여러 개 가능).\n"
            "- category 예: AI Engineer / ML Engineer / LLM Engineer / MLOps / Data. 명시된 경우만.\n\n"
            f"질문: {state['question']}")
        return {"params": params}

    async def run_search(state: QueryState) -> QueryState:
        args = {k: v for k, v in state["params"].model_dump().items() if v}
        raw = await tools["search_jobs"].ainvoke({**args, "only_active": True, "limit": 20})
        return {"results": _parse(raw)}

    async def synthesize_answer(state: QueryState) -> QueryState:
        if not state["results"]:
            cond = state["params"].model_dump(exclude_none=True)
            return {"answer": f"현재 조건에 맞는 공고가 없습니다. (검색 조건: {cond})"}
        msg = await llm.ainvoke(
            "아래 검색 결과만 근거로 질문에 답하고, 사용자 프로필과 맞는 공고를 추천 이유와 함께 설명해라. "
            "결과에 없는 공고·정보는 절대 만들지 말고, 링크가 있으면 포함해라.\n\n"
            f"사용자 프로필: {state.get('profile') or '(제공되지 않음)'}\n"
            f"질문: {state['question']}\n\n"
            f"검색 결과(JSON):\n{json.dumps(state['results'], ensure_ascii=False, default=str)}")
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
