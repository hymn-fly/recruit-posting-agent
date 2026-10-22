"""Supervisor: 사용자 요청을 3개 Agent(채용공고 조회 / 기업분석 / 내 프로필)로 라우팅하고 결과를 모은다.

supervisor -> (job_search | company_research | profile_gap) -> supervisor -> ... -> finish
한 턴에 같은 Agent는 한 번만, 최대 MAX_STEPS개까지 실행한다.
"""
import logging
import os

from langchain_core.messages import AIMessage, HumanMessage
from langgraph.graph import END, START, StateGraph

from agents.graphs import company_research_graph, job_search_graph, profile_gap_graph
from agents.llm import LLM
from agents.mcp_client import get_first_mcp_result
from agents.state import AppState, Route

log = logging.getLogger("agents.supervisor")

MAX_STEPS = 3
HISTORY = 6   # 라우터에 보여줄 최근 메시지 수
NEEDS_POSTING = ("company_research", "profile_gap")
AGENT_LABELS = {"job_search": "채용공고 조회", "company_research": "기업분석", "profile_gap": "내 프로필"}

# Agent별로 쓸 수 있는 MCP 도구 (필요한 것만 넘긴다)
AGENT_TOOLS = {
    "job_search": ("semantic_search_jobs",),
    "company_research": ("get_cached_research", "save_research"),
    "profile_gap": ("get_profile_summary", "search_profile"),
}



def _last_user(state: AppState) -> str:
    return next((m.content for m in reversed(state["messages"]) if isinstance(m, HumanMessage)), "")


def _profile_brief(summary: dict) -> str:
    if not summary:
        return ""
    return (f"{summary.get('headline')} / 총 경력 {summary.get('total_years')}년 / "
            f"스킬: {', '.join(summary.get('skills', [])[:15])} / 도메인: {', '.join(summary.get('domains', []))}")


def make_graph(tools: dict, checkpointer=None):
    subset = {a: {n: tools[n] for n in names} for a, names in AGENT_TOOLS.items()}
    job_graph = job_search_graph.make_graph(subset["job_search"])
    research_graph = company_research_graph.make_graph(subset["company_research"])
    gap_graph = profile_gap_graph.make_graph(subset["profile_gap"])
    router = LLM(schema=Route, primary_timeout=15)

    async def supervisor(state: AppState) -> AppState:
        done = [o["agent"] for o in state.get("turn_outputs", [])]
        if state.get("steps", 0) >= MAX_STEPS:
            return {"route": Route(next="FINISH", reason="이번 턴 최대 실행 수 도달").model_dump()}
        cands = "\n".join(f"{i}. [{c['company']}] {c['title']} (id={c['id']})"
                          for i, c in enumerate(state.get("candidates") or [], 1)) or "(없음)"
        sel = state.get("selected_posting")
        sel_text = f"[{sel['company']}] {sel['title']} (id={sel['id']})" if sel else "없음"
        history = "\n".join(f"{'사용자' if isinstance(m, HumanMessage) else '시스템'}: {str(m.content)[:300]}"
                            for m in state["messages"][-HISTORY:])
        route = await router.ainvoke(
            "너는 이직 준비 도우미의 Supervisor다. 사용자 마지막 요청을 처리할 다음 Agent를 고른다.\n"
            "- job_search: 채용공고 찾기/추천 (역할·자격요건 의미 검색)\n"
            "- company_research: 선택된 공고 회사의 산업 현황·발전 방향·경쟁사·직무 기여 방안 웹 리서치\n"
            "- profile_gap: 선택된 공고 대비 내 이력서·포트폴리오의 부족한 점과 보완 방법\n"
            "- FINISH: 요청이 모두 처리됐거나 Agent가 필요 없을 때\n"
            "규칙: 이번 턴에 이미 실행한 Agent는 다시 고르지 않는다. 요청에 여러 일이 있으면 "
            "job_search → company_research → profile_gap 순서로 하나씩 처리한다. "
            "company_research/profile_gap은 분석할 공고가 필요하다. 사용자가 번호나 id로 가리키면 "
            "posting_number/posting_id를 채우고, 가리키지 않았으면 현재 선택된 공고를 쓴다. "
            "선택된 공고도 없고 추천 목록만 있으면 FINISH하고 reply로 몇 번 공고인지 묻는다.\n\n"
            f"이번 턴에 실행한 Agent: {done or '없음'}\n"
            f"현재 추천 목록:\n{cands}\n"
            f"선택된 공고: {sel_text}\n\n"
            f"최근 대화:\n{history}")
        if route.next in done:
            route = Route(next="FINISH", reason=f"{route.next}는 이번 턴에 이미 실행함")
        out: AppState = {"route": route}
        if route.next in NEEDS_POSTING:
            posting = None
            cands_list = state.get("candidates") or []
            if route.posting_number and 1 <= route.posting_number <= len(cands_list):
                posting = get_first_mcp_result(await tools["get_job_posting"].ainvoke({"id": cands_list[route.posting_number - 1]["id"]}))
            elif route.posting_id:
                try:
                    posting = get_first_mcp_result(await tools["get_job_posting"].ainvoke({"id": route.posting_id}))
                except Exception:
                    posting = None
            if posting:
                out["selected_posting"] = posting
            elif not sel:
                out["route"] = Route(next="FINISH", reason="분석할 공고가 선택되지 않음",
                                     reply="어떤 공고를 분석할까요? 먼저 공고를 검색하거나 번호/id로 알려주세요.")
        log.info("route -> %s (%s)", out["route"].next, out["route"].reason)
        out["route"] = out["route"].model_dump()   # checkpointer 직렬화를 위해 dict로 저장
        return out

    def _output(state: AppState, agent: str, text: str) -> list[dict]:
        return state.get("turn_outputs", []) + [{"agent": agent, "text": text}]

    async def run_job_search(state: AppState) -> AppState:
        profile = ""
        if state["route"]["use_profile"]:
            profile = _profile_brief(get_first_mcp_result(await tools["get_profile_summary"].ainvoke({})))
        try:
            res = await job_graph.ainvoke({"question": _last_user(state), "profile": profile})
        except Exception as e:
            log.exception("job_search 실패")
            return {"steps": state.get("steps", 0) + 1,
                    "turn_outputs": _output(state, "job_search", f"⚠️ 채용공고 조회 실패: {e!r}"[:300])}
        upd: AppState = {"steps": state.get("steps", 0) + 1,
                         "turn_outputs": _output(state, "job_search", res["answer"])}
        if res.get("candidates"):
            upd["candidates"] = [{k: c.get(k) for k in ("id", "company", "title", "url")} for c in res["candidates"]]
        return upd

    async def run_company_research(state: AppState) -> AppState:
        posting = state["selected_posting"]
        if not os.environ.get("TAVILY_API_KEY"):
            return {"steps": state.get("steps", 0) + 1,
                    "turn_outputs": _output(state, "company_research",
                                            "⚠️ 기업분석에는 웹 검색 키가 필요합니다. `.env`에 TAVILY_API_KEY를 넣어주세요.")}
        try:
            res = await research_graph.ainvoke({"posting": posting})
        except Exception as e:
            log.exception("company_research 실패")
            return {"steps": state.get("steps", 0) + 1,
                    "turn_outputs": _output(state, "company_research", f"⚠️ 기업분석 실패: {e!r}"[:300])}
        return {"steps": state.get("steps", 0) + 1, "research_report": res["report"],
                "research_posting_id": posting["id"],
                "turn_outputs": _output(state, "company_research", res["answer"])}

    async def run_profile_gap(state: AppState) -> AppState:
        posting = state["selected_posting"]
        research = state.get("research_report") if state.get("research_posting_id") == posting["id"] else None
        try:
            res = await gap_graph.ainvoke({"posting": posting, "research_report": research})
        except Exception as e:
            log.exception("profile_gap 실패")
            return {"steps": state.get("steps", 0) + 1,
                    "turn_outputs": _output(state, "profile_gap", f"⚠️ 내 프로필 분석 실패: {e!r}"[:300])}
        return {"steps": state.get("steps", 0) + 1, "gap_report": res["answer"],
                "turn_outputs": _output(state, "profile_gap", res["answer"])}

    async def finish(state: AppState) -> AppState:
        outs = state.get("turn_outputs", [])
        if outs:
            text = "\n\n---\n\n".join(f"# {AGENT_LABELS[o['agent']]}\n\n{o['text']}" if len(outs) > 1 else o["text"]
                                       for o in outs)
            if state["route"]["reply"]:
                text += f"\n\n{state['route']['reply']}"
        else:
            text = state["route"]["reply"] or "무엇을 도와드릴까요? (공고 찾기 / 기업분석 / 내 역량 비교)"
        return {"messages": [AIMessage(content=text)]}

    def route_next(state: AppState) -> str:
        return "finish" if state["route"]["next"] == "FINISH" else state["route"]["next"]

    g = StateGraph(AppState)
    g.add_node("supervisor", supervisor)
    g.add_node("job_search", run_job_search)
    g.add_node("company_research", run_company_research)
    g.add_node("profile_gap", run_profile_gap)
    g.add_node("finish", finish)
    g.add_edge(START, "supervisor")
    g.add_conditional_edges("supervisor", route_next,
                            ["job_search", "company_research", "profile_gap", "finish"])
    for a in ("job_search", "company_research", "profile_gap"):
        g.add_edge(a, "supervisor")
    g.add_edge("finish", END)
    return g.compile(checkpointer=checkpointer)


def turn_input(text: str) -> AppState:
    """한 턴의 입력. 턴 단위 값(steps, turn_outputs)은 여기서 초기화한다."""
    return {"messages": [HumanMessage(content=text)], "steps": 0, "turn_outputs": []}
