"""Supervisor: 사용자 요청을 3개 Agent(채용공고 조회 / 기업분석 / 내 프로필)로 라우팅하고 결과를 모은다.

supervisor -> job_search -> supervisor -> (company_research ‖ profile_gap) -> supervisor -> ... -> finish
공고가 정해진 뒤의 기업분석과 내 프로필 분석은 같은 단계에서 병렬로 실행(fan-out)하고, 둘 다 끝나면
supervisor가 한 번만 다시 실행된다(fan-in). 한 턴에 같은 Agent는 한 번만, 최대 MAX_STEPS개까지 실행한다.
"""
import logging
import os

from langchain_core.messages import AIMessage, HumanMessage
from langgraph.graph import END, START, StateGraph

from agents.guardrails.material_prompt import format_external_materials
from agents.graphs import company_research_graph, job_search_graph, profile_gap_graph
from agents.llm import LLM
from agents.mcp_client import get_first_mcp_result
from agents.progress import STATUS_DONE, STATUS_START, emit_progress
from agents.state import AppState, Route
from common.schemas import JobPosting

log = logging.getLogger(__name__)

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



async def fetch_job_posting_by_id(tools: dict, posting_id: int) -> JobPosting | None:
    """get_job_posting tool로 공고를 조회해 JobPosting으로 검증해 돌려준다.

    없는 공고·tool 오류처럼 결과가 비어 있으면 None. 결과가 스키마와 다르면 ValidationError를 그대로 올린다.
    """
    raw_posting = get_first_mcp_result(await tools["get_job_posting"].ainvoke({"id": posting_id}))

    return JobPosting.model_validate(raw_posting) if raw_posting else None


def _last_user(state: AppState) -> str:
    return next((m.content for m in reversed(state["messages"]) if isinstance(m, HumanMessage)), "")


def list_agents_to_run(route: Route) -> list[str]:
    """이 route로 이번 단계에 실행할 Agent 이름들. FINISH면 빈 리스트."""
    return [] if route.next == "FINISH" else [route.next, *route.parallel_with]


def normalize_route_agents(route: Route, already_run_agents: list[str]) -> Route:
    """LLM이 고른 route를 실행 가능한 형태로 정리한다.

    - 이번 턴에 이미 실행한 Agent는 뺀다. 남는 게 없으면 FINISH.
    - job_search는 분석 대상 공고를 정하는 단계라 다른 Agent와 함께 돌릴 수 없다. 함께 골랐으면 job_search만 남긴다.
    """
    planned_agents = list_agents_to_run(route)
    runnable_agents = [a for a in dict.fromkeys(planned_agents) if a not in already_run_agents]

    if "job_search" in runnable_agents:
        runnable_agents = ["job_search"]

    # 처음부터 FINISH였던 경우는 그대로 둔다
    if not planned_agents:
        return route
    if not runnable_agents:
        return route.model_copy(update={"next": "FINISH", "parallel_with": [],
                                        "reason": f"{route.next}는 이번 턴에 이미 실행함"})
    return route.model_copy(update={"next": runnable_agents[0], "parallel_with": runnable_agents[1:]})


def _agent_output(agent: str, text: str) -> list[dict]:
    """turn_outputs reducer에 넘길 증분. 자기 결과만 담는다(다른 Agent 결과는 reducer가 합친다)."""
    return [{"agent": agent, "text": text}]


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
        emit_progress("supervisor", f"route:{state.get('steps', 0)}", STATUS_START, "요청을 분석하는 중")
        log.info("event=supervisor_start steps=%d candidates=%d selected_posting_id=%s", state.get("steps", 0), len(state.get("candidates") or []), (state.get("selected_posting") or {}).get("id"))
        done = [o["agent"] for o in state.get("turn_outputs", [])]
        
        if state.get("steps", 0) >= MAX_STEPS:
            emit_progress("supervisor", f"route:{state.get('steps', 0)}", STATUS_DONE, "답변을 정리하는 중", next="FINISH")
            return {"route": Route(next="FINISH", reason="이번 턴 최대 실행 수 도달").model_dump()}
        
        cands = "\n".join(f"{i}. [{candidate['company']}] {candidate['title']} (id={candidate['id']})"
                          for i, candidate in enumerate(state.get("candidates") or [], 1)) or "(없음)"
        sel = state.get("selected_posting")
        sel_text = f"[{sel['company']}] {sel['title']} (id={sel['id']})" if sel else "없음"
        history = "\n".join(f"{'사용자' if isinstance(m, HumanMessage) else '어시스턴트'}: {str(m.content)[:300]}"
                            for m in state["messages"][-HISTORY:])
        
        route = await router.ainvoke(
            "너는 이직 준비 도우미의 Supervisor다. 사용자의 마지막 요청을 처리할 다음 Agent를 고른다.\n"
            "- job_search: 채용공고 찾기/추천 (역할·자격요건 의미 검색)\n"
            "- company_research: 선택된 공고 회사의 산업 현황·발전 방향·경쟁사·직무 기여 방안 웹 리서치\n"
            "- profile_gap: 선택된 공고 대비 내 이력서·포트폴리오의 부족한 점과 보완 방법\n"
            "- FINISH: 요청이 모두 처리됐거나 Agent가 필요 없을 때\n"
            "규칙:\n"
            "- 이번 턴에 이미 실행한 Agent는 다시 고르지 않는다. 요청에 **여러 일이 명시된 경우에만** "
            "여러 Agent를 쓴다.\n"
            "- job_search는 항상 혼자 실행한다. 공고를 찾은 뒤 분석이 필요하면 다음 단계에서 고른다.\n"
            "- 공고가 정해진 상태에서 기업분석과 내 프로필 분석을 **둘 다** 요청했으면 "
            "next에 하나, parallel_with에 나머지 하나를 넣어 동시에 실행한다. 하나만 요청했으면 parallel_with는 비운다.\n"
            "- company_research/profile_gap은 분석할 공고가 필요하다. 사용자가 번호나 id로 가리키면 "
            "posting_number/posting_id를 채우고, 가리키지 않았으면 현재 선택된 공고를 쓴다.\n"
            "- 선택된 공고도 없고 추천 목록만 있으면 FINISH하고 reply로 몇 번 공고인지 묻는다.\n"
            "- 실행한 Agent 결과로 요청이 충족됐으면 FINISH\n\n"
            f"이번 턴에 실행한 Agent: {done or '없음'}\n"
            f"최근 대화:\n{history}\n"
            + format_external_materials(candidate_postings=cands, selected_posting=sel_text))
        
        route = normalize_route_agents(route, done)

        out: AppState = {"route": route}
        if any(agent in NEEDS_POSTING for agent in list_agents_to_run(route)):
            # 후보 번호가 유효하면 그 후보의 id를, 아니면 route.posting_id를 조회 대상으로 정한다
            candidate_list = state.get("candidates") or []
            if route.posting_number and 1 <= route.posting_number <= len(candidate_list):
                requested_posting_id = candidate_list[route.posting_number - 1]["id"]
            else:
                requested_posting_id = route.posting_id

            # 공고를 조회한다 (못 찾으면 None)
            posting = await fetch_job_posting_by_id(tools, requested_posting_id) if requested_posting_id else None

            # 찾은 공고를 선택하고, 선택된 공고가 아예 없으면 사용자에게 되묻는다
            if posting is not None:
                out["selected_posting"] = posting.model_dump(mode="json")
            elif not sel:
                out["route"] = Route(next="FINISH", reason="분석할 공고가 선택되지 않음",
                                     reply="어떤 공고를 분석할까요? 먼저 공고를 검색하거나 번호/id로 알려주세요.")
                
        agents_to_run = list_agents_to_run(out["route"])
        log.info("event=route_decided agents=%s reason=%r", agents_to_run, out["route"].reason)
        if agents_to_run:
            parallel_note = " (동시 실행)" if len(agents_to_run) > 1 else ""
            progress_message = f"{' · '.join(AGENT_LABELS[a] for a in agents_to_run)} 단계로 진행{parallel_note}"
        else:
            progress_message = "답변을 정리하는 중"
        emit_progress("supervisor", f"route:{state.get('steps', 0)}", STATUS_DONE, progress_message,
                      next=out["route"].next)
        out["route"] = out["route"].model_dump()   # checkpointer 직렬화를 위해 dict로 저장
        return out

    async def run_job_search(state: AppState) -> AppState:
        profile = ""
        if state["route"]["use_profile"]:
            profile = _profile_brief(get_first_mcp_result(await tools["get_profile_summary"].ainvoke({})))
        try:
            log.info("event=job_search_start use_profile=%s", state["route"]["use_profile"])
            res = await job_graph.ainvoke({"question": _last_user(state), "profile": profile})
        except Exception as error:
            log.exception("event=job_search_failed")
            return {"steps": 1,
                    "turn_outputs": _agent_output("job_search", f"⚠️ 채용공고 조회 실패")}
        log.info("event=job_search_done candidates=%d answer_chars=%d", len(res.get("candidates") or []), len(res.get("answer", "")))
        upd: AppState = {"steps": 1,
                         "turn_outputs": _agent_output("job_search", res["answer"])}
        if res.get("candidates"):
            upd["candidates"] = [
                {k: c.get(k) for k in ("id", "company", "title", "url", "industry", "location", "min_years", "deadline")}
                for c in res["candidates"]
            ]
        return upd

    async def run_company_research(state: AppState) -> AppState:
        posting = state["selected_posting"]
        if not os.environ.get("TAVILY_API_KEY"):
            return {"steps": 1,
                    "turn_outputs": _agent_output("company_research",
                                            "⚠️ 기업분석에는 웹 검색 키가 필요합니다. `.env`에 TAVILY_API_KEY를 넣어주세요.")}
        try:
            res = await research_graph.ainvoke({"posting": posting})
        except Exception as error:
            log.exception("event=company_research_failed")
            return {"steps": 1,
                    "turn_outputs": _agent_output("company_research", f"⚠️ 기업분석 실패")}
        return {"steps": 1, "research_report": res["report"],
                "research_posting_id": posting["id"],
                "turn_outputs": _agent_output("company_research", res["answer"])}

    async def run_profile_gap(state: AppState) -> AppState:
        posting = state["selected_posting"]
        research = state.get("research_report") if state.get("research_posting_id") == posting["id"] else None
        try:
            res = await gap_graph.ainvoke({"posting": posting, "research_report": research})
        except Exception as error:
            log.exception("event=profile_gap_failed")
            return {"steps": 1,
                    "turn_outputs": _agent_output("profile_gap", f"⚠️ 내 프로필 분석 실패")}
        reports = dict(state.get("gap_reports") or {})
        if state.get("gap_report") and state.get("gap_posting_id") is not None:
            reports[str(state["gap_posting_id"])] = state["gap_report"]
        reports[str(posting["id"])] = res["answer"]
        return {"steps": 1, "gap_report": res["answer"],
                "gap_posting_id": posting["id"], "gap_reports": reports,
                "turn_outputs": _agent_output("profile_gap", res["answer"])}

    async def finish(state: AppState) -> AppState:
        outs = state.get("turn_outputs", [])
        if outs:
            text = "\n\n---\n\n".join(f"# {AGENT_LABELS[o['agent']]}\n\n{o['text']}" if len(outs) > 1 else o["text"]
                                       for o in outs)
            if state["route"]["reply"]:
                text += f"\n\n{state['route']['reply']}"
        else:
            text = state["route"]["reply"] or "무엇을 도와드릴까요? (공고 찾기 / 기업분석 / 내 역량 비교)"
        log.info("event=finish agents=%s answer_chars=%d", [o["agent"] for o in outs], len(text))
        return {"messages": [AIMessage(content=text)]}

    def route_next(state: AppState) -> list[str]:
        route = state["route"]
        if route["next"] == "FINISH":
            return ["finish"]
        return [route["next"], *route["parallel_with"]]

    graph = StateGraph(AppState)
    graph.add_node("supervisor", supervisor)
    graph.add_node("job_search", run_job_search)
    graph.add_node("company_research", run_company_research)
    graph.add_node("profile_gap", run_profile_gap)
    graph.add_node("finish", finish)
    graph.add_edge(START, "supervisor")
    graph.add_conditional_edges("supervisor", route_next,
                            ["job_search", "company_research", "profile_gap", "finish"])
    for agent_name in ("job_search", "company_research", "profile_gap"):
        graph.add_edge(agent_name, "supervisor")
    graph.add_edge("finish", END)
    return graph.compile(checkpointer=checkpointer)


def input_to_agent_state(text: str) -> AppState:
    """한 턴의 입력. 턴 단위 값(steps, turn_outputs)은 여기서 초기화한다."""
    # steps / turn_outputs는 reducer 필드라 0/[]를 넣어도 이전 값에 더해질 뿐이다. None이 "새 턴 시작" 신호다.
    return {"messages": [HumanMessage(content=text)], "steps": None, "turn_outputs": None}
