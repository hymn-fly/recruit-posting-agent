"""적재 에이전트 (회사 1개당 1회).

fetch_page -> extract_postings(AI 후보만) -> fetch_details(공고 링크 방문, JD 파싱) -> save_postings -> finish
"""
import asyncio
from urllib.parse import urljoin

import httpx
from bs4 import BeautifulSoup
from langgraph.graph import END, START, StateGraph

from agents.llm import LLM
from agents.state import ExtractionResult, IngestState, JDDetail

MAX_CHARS = 60_000          # 목록 페이지 텍스트 상한
MAX_DETAIL_CHARS = 20_000   # 공고 상세(JD) 텍스트 상한
DETAIL_CONCURRENCY = 4
HEADERS = {"User-Agent": "Mozilla/5.0 (job-research-agent)"}


def page_to_text(html: str, base_url: str) -> str:
    """HTML을 텍스트로 바꾸되, 링크는 앵커 텍스트 뒤에 [절대URL]로 남긴다 (공고 URL 추출용)."""
    soup = BeautifulSoup(html, "html.parser")
    for tag in soup(["script", "style", "noscript"]):
        tag.decompose()
    for a in soup.find_all("a", href=True):
        href = a["href"].strip()
        if not href or href.startswith(("#", "javascript:", "mailto:", "tel:")):
            continue
        a.append(f" [{urljoin(base_url, href)}]")
    return soup.get_text("\n", strip=True)


def make_graph(tools: dict):
    list_llm = LLM(16_000, schema=ExtractionResult)
    detail_llm = LLM(4_000, schema=JDDetail)

    async def fetch_page(state: IngestState) -> IngestState:
        c = state["company"]
        method = c["access_method"]
        await tools["upsert_company"].ainvoke({
            "name": c["name"], "portal_url": c["portal_url"],
            "access_method": method, "notes": c["notes"], "industry": c.get("industry")})
        if method == "robots_blocked":
            return {"status": "unverifiable", "detail": "robots.txt 차단 — 자동 크롤링 안 함"}
        if method == "js_rendered":
            return {"status": "unverifiable",
                    "detail": "JS 렌더링 필요: 브라우저 자동화(Claude in Chrome)나 잡포털 미러 필요"}
        attempts = 2 if method in ("server_error", "api_error") else 1
        last_err = ""
        for i in range(attempts):
            try:
                async with httpx.AsyncClient(headers=HEADERS, timeout=30, follow_redirects=True) as h:
                    r = await h.get(c["portal_url"])
                    r.raise_for_status()
                text = page_to_text(r.text, str(r.url))
                if len(text) < 200:
                    return {"status": "unverifiable", "detail": "본문이 거의 비어 있음(JS 렌더링 의심)"}
                return {"raw_text": text[:MAX_CHARS]}
            except httpx.HTTPError as e:
                last_err = repr(e)
                if i + 1 < attempts:
                    await asyncio.sleep(2)
        return {"status": "unverifiable" if attempts > 1 else "failed", "detail": last_err}

    async def extract_postings(state: IngestState) -> IngestState:
        """목록 페이지에서 AI Engineer 관련 '후보'만 추출한다 (1차 필터, 제목/직군 기준)."""
        c = state["company"]
        prompt = (
            f"다음은 {c['name']} 채용 포털 목록 페이지의 텍스트다. 링크는 `[URL]` 형태로 해당 공고 뒤에 붙어 있다.\n"
            "이 중 **AI Engineer 관련** 공고만 추출해라. AI/ML/LLM/MLOps/데이터 사이언스·엔지니어링 직무가 대상이며, "
            "제목·직군만으로 애매하면 포함해라(상세 JD에서 2차로 다시 거른다). "
            "그 외 직무(일반 백엔드/프런트엔드/인프라/보안/PM/경영지원/임베디드 등)는 출력하지 마라.\n"
            "텍스트에 **실제로 적혀 있는** 공고만 추출하고, 없는 공고·URL·날짜는 지어내지 말고 불확실하면 null로 둬라.\n"
            "url은 해당 공고 뒤에 붙은 `[URL]` 값만 그대로 써라.\n"
            f"필터 조건(반드시 적용): {c['notes'] or '없음'}\n"
            "category는 AI Engineer / ML Engineer / LLM Engineer / MLOps / Data 중 하나.\n\n"
            f"--- 페이지 텍스트 ---\n{state['raw_text']}")
        result = await list_llm.ainvoke(prompt)
        if not result.postings:
            return {"postings": [], "status": "ok", "detail": "AI Engineer 관련 공고 0건"}
        return {"postings": result.postings}

    async def fetch_details(state: IngestState) -> IngestState:
        """각 공고 링크를 방문해 JD 본문을 파싱하고, 본문 기준으로 AI 관련이 아니면 제외한다."""
        c = state["company"]
        sem = asyncio.Semaphore(DETAIL_CONCURRENCY)
        stats = {"not_ai": 0, "detail_failed": 0, "no_url": 0}

        async def one(h: httpx.AsyncClient, p) -> dict | None:
            row = {
                "title": p.title, "url": p.url, "location": p.location,
                "employment_type": p.employment_type, "min_years": p.min_years,
                "category": p.category, "posted_at": p.posted_at, "deadline": p.deadline,
                "responsibilities": None, "required_qualifications": None,
                "preferred_qualifications": None, "source_snapshot": None,
            }
            if not p.url:
                stats["no_url"] += 1
                return row  # 링크가 없으면 목록 정보만 저장
            async with sem:
                try:
                    r = await h.get(p.url)
                    r.raise_for_status()
                    text = page_to_text(r.text, str(r.url))[:MAX_DETAIL_CHARS]
                    jd: JDDetail = await detail_llm.ainvoke(
                        f"다음은 {c['name']}의 채용공고 상세 페이지 텍스트다. JD 본문을 분석해라.\n"
                        "- 본문에 **실제로 적혀 있는** 내용만 사용하고 지어내지 마라. 없으면 null.\n"
                        "- responsibilities(하는 일) / required_qualifications(필수 역량) / "
                        "preferred_qualifications(우대 역량)는 본문 문장을 간결히 정리하되 항목은 줄바꿈으로 구분해라.\n"
                        "- is_ai_related는 AI/ML/LLM/MLOps/데이터 사이언스 업무가 핵심일 때만 true. "
                        "단순히 'AI' 단어가 언급되거나 일반 개발/운영 직무면 false.\n"
                        "- category는 AI Engineer / ML Engineer / LLM Engineer / MLOps / Data 중 하나.\n\n"
                        f"--- 상세 페이지 텍스트 ---\n{text}")
                except Exception:  # 상세 실패는 해당 공고만 목록 정보로 저장
                    stats["detail_failed"] += 1
                    return row
            if not jd.is_ai_related:
                stats["not_ai"] += 1
                return None
            row.update({
                "category": jd.category or row["category"],
                "responsibilities": jd.responsibilities,
                "required_qualifications": jd.required_qualifications,
                "preferred_qualifications": jd.preferred_qualifications,
                "min_years": jd.min_years if jd.min_years is not None else row["min_years"],
                "deadline": jd.deadline or row["deadline"],
                "source_snapshot": text[:2000],
            })
            return row

        async with httpx.AsyncClient(headers=HEADERS, timeout=30, follow_redirects=True) as h:
            rows = await asyncio.gather(*(one(h, p) for p in state["postings"]))
        final = [r for r in rows if r is not None]
        note = (f"목록 AI 후보 {len(state['postings'])}건 → JD 확인 후 {len(final)}건 "
                f"(비AI 제외 {stats['not_ai']}, 상세 실패 {stats['detail_failed']}, URL 없음 {stats['no_url']})")
        return {"final_postings": final, "status": "ok", "detail": note}

    async def save_postings(state: IngestState) -> IngestState:
        c = state["company"]
        saved, failed = 0, 0
        for row in state["final_postings"]:
            try:
                await tools["upsert_job_posting"].ainvoke({"company_name": c["name"], **row})
                saved += 1
            except Exception:
                failed += 1
        status = "ok" if saved or not failed else "failed"
        return {"status": status, "detail": f"{state.get('detail', '')} / 저장 {saved}건, 저장 실패 {failed}건"}

    async def finish(state: IngestState) -> IngestState:
        await tools["record_scrape_run"].ainvoke({
            "company_name": state["company"]["name"],
            "status": state["status"], "detail": state.get("detail", "")})
        return {}

    g = StateGraph(IngestState)
    g.add_node("fetch_page", fetch_page)
    g.add_node("extract_postings", extract_postings)
    g.add_node("fetch_details", fetch_details)
    g.add_node("save_postings", save_postings)
    g.add_node("finish", finish)
    g.add_edge(START, "fetch_page")
    g.add_conditional_edges(
        "fetch_page",
        lambda s: "extract_postings" if s.get("raw_text") else "finish",
        ["extract_postings", "finish"])
    g.add_conditional_edges(
        "extract_postings",
        lambda s: "fetch_details" if s.get("postings") else "finish",
        ["fetch_details", "finish"])
    g.add_conditional_edges(
        "fetch_details",
        lambda s: "save_postings" if s.get("final_postings") else "finish",
        ["save_postings", "finish"])
    g.add_edge("save_postings", "finish")
    g.add_edge("finish", END)
    return g.compile()
