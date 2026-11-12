"""기업분석 Agent: plan_queries(실제 계열사 판별 포함) -> load_cache -> search_topic(토픽별 병렬, Tavily) -> synthesize.

회사 단위 토픽(industry/outlook/competitors/company)은 CACHE_DAYS 이내 결과가 있으면 재사용하고,
직무 토픽(role)과 최종 리포트는 공고마다 새로 만든다.
"""
import asyncio
import logging
import re

from langchain_tavily import TavilySearch
from langgraph.graph import END, START, StateGraph
from langgraph.types import Send

from agents.llm import LLM
from agents.mcp_client import get_first_mcp_result
from agents.state import COMPANY_TOPICS, ResearchPlan, ResearchState

log = logging.getLogger(__name__)

CACHE_DAYS = 14
RESULTS_PER_QUERY = 5
SNIPPET_LEN = 1200

TOPIC_TITLES = {
    "industry": "산업 현황",
    "outlook": "산업 발전 방향",
    "competitors": "주요 경쟁사와 동향",
    "company": "해당 기업의 전략·최근 행보",
    "role": "직무 관련 기술·업무 동향",
}



def _posting_brief(p: dict) -> str:
    return (f"회사: {p.get('company')} (산업: {p.get('industry') or '미상'})\n"
            f"공고: {p.get('title')} / 직군: {p.get('category')}\n"
            f"하는 일: {(p.get('responsibilities') or '-')[:800]}")


def make_graph(tools: dict):
    _tavily: list[TavilySearch] = []   # TAVILY_API_KEY가 없어도 다른 Agent는 뜨도록 처음 쓸 때 만든다

    def tavily() -> TavilySearch:
        if not _tavily:
            _tavily.append(TavilySearch(max_results=RESULTS_PER_QUERY, search_depth="advanced"))
        return _tavily[0]

    planner = LLM(schema=ResearchPlan, primary_timeout=30)
    summarizer = LLM(primary_timeout=30)
    writer = LLM(primary_timeout=60)

    async def plan_queries(state: ResearchState) -> ResearchState:
        plan = await planner.ainvoke(
            "채용 지원자를 위한 기업분석 리서치 계획을 세워라. 먼저 실제 채용 법인/계열사(target_company)를 정한다 "
            "— 회사명이 그룹(SK, LG, 현대, 롯데, CJ 등)이면 제목·근무지·하는 일에 적힌 계열사가 대상이다. "
            "산업·경쟁사·검색어는 모두 그 계열사 기준으로 만든다. 아래 토픽마다 웹 검색어 2개를 만든다 "
            "(한국 기업이면 한국어 검색어, 최신 동향을 위해 연도 포함 가능).\n"
            + "\n".join(f"- {t}: {TOPIC_TITLES[t]}" for t in TOPIC_TITLES)
            + f"\n\n{_posting_brief(state['posting'])}\n근무지: {state['posting'].get('location') or '-'}"
        )
        log.info("plan_queries: 공고 id=%s [%s] %s\n  → 분석 대상 %s / 산업 %s / 경쟁사 %s\n%s",
                 state["posting"].get("id"), state["posting"].get("company"), state["posting"].get("title"),
                 plan.target_company, plan.industry, ", ".join(plan.competitors),
                 "\n".join(f"    {t.topic}: {t.queries}" for t in plan.topics))
        return {"plan": plan}

    async def load_cache(state: ResearchState) -> ResearchState:
        cache_lookup_args = {"company_name": state["posting"]["company"],
                             "target_company": state["plan"].target_company, "max_age_days": CACHE_DAYS}
        cached = get_first_mcp_result(await tools["get_cached_research"].ainvoke(cache_lookup_args))

        cached_company_topics = [t for t in cached.get("topics", []) if t["topic"] in COMPANY_TOPICS]
        log.info("load_cache: %s → %s", cache_lookup_args,
                 f"캐시 적중(research id={cached.get('id')}, {cached.get('created_at')}), 재사용 토픽 "
                 f"{[t['topic'] for t in cached_company_topics]}" if cached else "캐시 없음")
        return {"cached": cached_company_topics}

    def fan_out(state: ResearchState):
        done = {c["topic"] for c in state["cached"]}
        sends = [Send("search_topic", {"posting": state["posting"], "plan": state["plan"],
                                       "topic": t.topic, "queries": t.queries})
                 for t in state["plan"].topics if t.topic not in done]
        log.info("fan_out: 검색할 토픽 %s", [s.arg["topic"] for s in sends] or "없음 → 바로 synthesize")
        return sends or ["synthesize"]

    async def search_topic(arg: dict) -> ResearchState:
        topic, queries = arg["topic"], arg["queries"]

        # 1) 토픽의 검색어들을 Tavily에 동시에 보낸다 (하나가 실패해도 나머지는 쓴다)
        responses = await asyncio.gather(*(tavily().ainvoke({"query": q}) for q in queries),
                                         return_exceptions=True)

        # 2) 검색어별 응답을 확인하고, URL 중복을 빼며 출처를 모은다
        seen_urls, sources = set(), []
        for query, response in zip(queries, responses):
            if isinstance(response, Exception):
                # 예외 객체는 JSON 직렬화가 안 되므로 타입명과 메시지만 남긴다 (로그 코드가 다시 예외를 내면 토픽 전체가 실패)
                log.warning("search_topic[%s]: Tavily 호출 실패 %r — %s: %s",
                            topic, query, type(response).__name__, str(response)[:200])
                continue
            results = response.get("results", []) if isinstance(response, dict) else []
            log.info("search_topic[%s]: %r → %d건 %s", topic, query, len(results),
                     [r["url"] for r in results])
            for item in results:
                if item["url"] not in seen_urls:
                    seen_urls.add(item["url"])
                    sources.append({"title": item.get("title", ""), "url": item["url"],
                                    "content": (item.get("content") or "")[:SNIPPET_LEN]})

        if not sources:
            log.warning("search_topic[%s]: 사용할 검색 결과가 없어 '확인 불가'로 둔다", topic)
            return {"topics": [{"topic": topic, "summary": "검색 결과 없음 — 확인 불가", "sources": []}]}

        # 3) 모은 출처만 근거로 토픽 요약 ([n] 번호는 이 토픽 안에서의 번호)
        plan = arg["plan"]
        msg = await summarizer.ainvoke(
            f"주제 '{TOPIC_TITLES[topic]}'에 대해 아래 검색 결과만 근거로 핵심을 한국어 bullet 5~8개로 요약하라. "
            "각 bullet 끝에 근거 출처 번호를 [n]으로 단다. 검색 결과에 없는 내용은 쓰지 않는다. "
            "수치·연도는 출처에 적힌 그대로.\n"
            f"분석 대상: {plan.target_company} / 산업: {plan.industry} / 경쟁사: {', '.join(plan.competitors)}\n"
            f"{_posting_brief(arg['posting'])}\n\n"
            + "\n\n".join(f"[{i}] {s['title']} ({s['url']})\n{s['content']}" for i, s in enumerate(sources, 1)))
        log.info("search_topic[%s]: 출처 %d개(중복 제거) → 요약 %d자", topic, len(sources), len(msg.text))

        return {"topics": [{"topic": topic, "summary": msg.text,
                            "sources": [{"title": s["title"], "url": s["url"]} for s in sources]}]}

    async def synthesize(state: ResearchState) -> ResearchState:
        p, plan = state["posting"], state["plan"]
        order = {t: i for i, t in enumerate(TOPIC_TITLES)}
        topics = sorted(state["cached"] + state.get("topics", []), key=lambda t: order[t["topic"]])
        # 토픽별 로컬 [n]을 전체 출처 번호로 바꾼다
        all_sources, blocks = [], []
        for t in topics:
            base = len(all_sources)
            all_sources.extend(t["sources"])
            summary = re.sub(r"\[(\d+)\]",
                             lambda m: f"[{base + int(m.group(1))}]" if 0 < int(m.group(1)) <= len(t["sources"])
                             else "", t["summary"])
            blocks.append(f"### {TOPIC_TITLES[t['topic']]}\n{summary}")

        msg = await writer.ainvoke(
            "너는 지원자를 돕는 기업분석 리서처다. 아래 토픽별 리서치 요약만 근거로 한국어 마크다운 리포트를 써라.\n"
            "섹션: ## 산업 현황 / ## 산업 발전 방향 / ## 경쟁사 vs 해당 기업(경쟁사들이 하는 것과 이 회사가 하는 것을 "
            "비교, 가능하면 표) / ## 이 직무로 회사에 기여할 수 있는 것(공고의 하는 일과 회사·산업 과제를 연결한 "
            "구체적 아이디어 3~5개, 면접·자소서에서 쓸 수 있게) / ## 한 줄 요약.\n"
            "모든 사실 문장에는 요약에 있는 출처 번호 [n]을 그대로 유지한다. 근거가 없으면 '확인 불가'라고 쓴다. "
            "기여 아이디어는 근거를 바탕으로 한 제안임을 분명히 한다. 3500자 이내.\n\n"
            f"분석 대상 회사: {plan.target_company} (채용 포털: {p['company']}) / 산업: {plan.industry}\n"
            f"{_posting_brief(p)}\n필수 역량: {(p.get('required_qualifications') or '-')[:600]}\n\n"
            + "\n\n".join(blocks))
        cited = {int(n) for n in re.findall(r"\[(\d+)\]", msg.text)}   # 본문에 인용된 출처만 목록에 남긴다
        refs = "\n".join(f"[{i}] {s['title']} — {s['url']}"
                         for i, s in enumerate(all_sources, 1) if i in cited)
        report = f"# {plan.target_company} 기업분석\n\n{msg.text}\n\n## 출처\n{refs}"

        saved = get_first_mcp_result(await tools["save_research"].ainvoke(
            {"company_name": p["company"], "target_company": plan.target_company,
             "posting_id": p.get("id"), "topics": topics, "report_md": report}))
        log.info("synthesize: 토픽 %d개(캐시 %d) / 출처 %d개 중 인용 %d개 / 리포트 %d자 → research id=%s 저장",
                 len(topics), len(state["cached"]), len(all_sources), len(cited & set(range(1, len(all_sources) + 1))),
                 len(report), saved.get("id"))
        cache_note = (f"\n\n_(회사 단위 토픽 {len(state['cached'])}개는 최근 {CACHE_DAYS}일 내 리서치를 재사용)_"
                      if state["cached"] else "")
        
        return {"report": report, "answer": report + cache_note}

    g = StateGraph(ResearchState)
    g.add_node("load_cache", load_cache)
    g.add_node("plan_queries", plan_queries)
    g.add_node("search_topic", search_topic)
    g.add_node("synthesize", synthesize)
    g.add_edge(START, "plan_queries")
    g.add_edge("plan_queries", "load_cache")
    g.add_conditional_edges("load_cache", fan_out, ["search_topic", "synthesize"])
    g.add_edge("search_topic", "synthesize")
    g.add_edge("synthesize", END)
    return g.compile()
