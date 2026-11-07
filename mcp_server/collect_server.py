"""수집용 MCP 서버(job-db): 회사·공고 저장, 크롤링 기록, 만료 공고 정리, 임베딩. 임의 SQL은 실행하지 않는다.

Claude Desktop 예약 작업(수집 에이전트)이 쓴다. 공고 검색·상세·회사 리서치 캐시는 query_server.py(job-query)에 있다.
"""
import sys
from datetime import date
from pathlib import Path
from typing import Optional
from urllib.parse import urlparse

from mcp.server.fastmcp import FastMCP

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from mcp_server.db import get_company_id_by_name, lifespan, pool  # noqa: E402
from mcp_server.embeddings import embed_documents, text_hash, to_pg  # noqa: E402

# AI Engineer 관련으로 적재하는 직군. 이 밖의 category는 upsert_job_posting이 거부한다
ALLOWED_CATEGORIES = ("AI Engineer", "ML Engineer", "LLM Engineer", "MLOps", "Data")

mcp = FastMCP("job-db", lifespan=lifespan)


def _date(value: Optional[str]) -> Optional[date]:
    return date.fromisoformat(value) if value else None


def normalize_site_host(site_url: str) -> str:
    """사이트 링크(또는 도메인)에서 사이트 비교용 호스트를 뽑는다. 소문자, 포트·경로 제외, 앞의 'www.' 제거.

    'https://www.saramin.co.kr/zf_user/search' -> 'saramin.co.kr', 'Career.Oliveyoung.com' -> 'career.oliveyoung.com'
    """
    raw_site_url = (site_url or "").strip()
    # 스킴 없이 도메인만 와도('saramin.co.kr') 호스트로 읽히게 한다
    has_scheme_or_slashes = "://" in raw_site_url or raw_site_url.startswith("//")
    parsed_url = urlparse(raw_site_url if has_scheme_or_slashes else f"//{raw_site_url}")
    host = (parsed_url.hostname or "").lower()
    if host.startswith("www."):
        host = host[len("www."):]
    if not host:
        raise ValueError(f"site_url에서 호스트를 읽을 수 없음: {site_url!r} (예: 'https://www.saramin.co.kr')")
    return host


# 공고 URL의 사이트 호스트(소문자, 'www.' 제거)를 뽑는 SQL 식. normalize_site_host와 같은 규칙이어야 한다
POSTING_URL_HOST_SQL = r"regexp_replace(lower(substring(url FROM '^https?://([^/:?#]+)')), '^www\.', '')"


async def fetch_urls_seen_on_site(table_name: str, site_url: str, candidate_urls: Optional[list[str]],
                                  only_active: bool) -> list[str]:
    """table_name 테이블에서, 호스트가 site_url의 사이트(하위 도메인 포함)에 속하는 공고 URL을 돌려준다.

    candidate_urls를 주면 그중 이미 있는 것만 돌려준다(전체 목록이 길어지는 채용 사이트용).
    """
    assert table_name in ("job_postings", "filtered_postings")   # 내부에서만 부르는 고정 값(SQL 식별자)
    site_host = normalize_site_host(site_url)
    active_condition = "AND is_active" if only_active else ""
    p = await pool()
    rows = await p.fetch(
        f"""SELECT url FROM (
                SELECT url, {POSTING_URL_HOST_SQL} AS host
                FROM {table_name}
                WHERE url IS NOT NULL {active_condition}
                  AND ($2::text[] IS NULL OR url = ANY($2::text[]))
            ) seen
            WHERE host = $1 OR right(host, length($1) + 1) = '.' || $1
            ORDER BY url""",
        site_host, candidate_urls)
    return [r["url"] for r in rows]


@mcp.tool()
async def upsert_company(name: str, portal_url: str, access_method: str = "unknown",
                         notes: Optional[str] = None, industry: Optional[str] = None) -> dict:
    """채용공고를 낸 회사(고용주)를 companies 테이블에 등록하거나 갱신한다.
    - 파싱한 공고의 회사가 아직 등록되지 않았을 때, upsert_job_posting 전에 호출
    - 이미 등록된 회사면 list_companies로 확인한 정확한 이름을 그대로 쓰고, 이 tool은 다시 호출하지 않는다 (예: '현대자동차' vs '현대(현대자동차)')

    주의: name이 같으면 portal_url, access_method, notes를 전달한 값으로 덮어쓴다.
      기본값으로 재호출하면 기존 access_method가 'unknown', notes가 null로 지워진다.
      industry만 null일 때 기존 값을 유지한다.

    Args:
      name: 공고를 낸 실제 회사명. 공고를 발견한 채용 플랫폼 이름 x
      portal_url: 그 회사의 자체 채용 포털 URL. 모르면 회사 공식 홈페이지 URL을 넣는다.
        채용 플랫폼의 공고 URL은 넣지 않는다(공고 URL은 upsert_job_posting의 url에 넣는다).
      access_method: portal_url을 열어 공고를 읽을 수 있는 방식. 다음 중 하나:
        static_html(정적 HTML로 공고 확인 가능), js_rendered(JS 렌더링 필요),
        robots_blocked(robots.txt 차단), server_error(서버 오류), api_error(API 오류),
        unknown(직접 확인하지 않음). 채용 플랫폼에서 공고를 찾았다는 정보는 넣지 않는다.
      notes: 포털 접근 시 참고할 메모(상세 URL 패턴, 필터링 조건 등). 없으면 생략.
      industry: 회사 산업 분류(예: '금융(은행)', 'IT서비스'). 모르면 생략.

    Returns:
      {"id": 회사 id, "name": 회사명}
    """
    p = await pool()
    row = await p.fetchrow(
        """INSERT INTO companies (name, portal_url, access_method, notes, industry)
           VALUES ($1, $2, $3, $4, $5)
           ON CONFLICT (name) DO UPDATE
             SET portal_url = EXCLUDED.portal_url,
                 access_method = EXCLUDED.access_method,
                 notes = EXCLUDED.notes,
                 industry = COALESCE(EXCLUDED.industry, companies.industry)
           RETURNING id, name""",
        name, portal_url, access_method, notes, industry)
    return dict(row)


@mcp.tool()
async def upsert_job_posting(company_name: str, title: str, url: Optional[str] = None,
                             location: Optional[str] = None, employment_type: Optional[str] = None,
                             min_years: Optional[int] = None, category: Optional[str] = None,
                             responsibilities: Optional[str] = None,
                             required_qualifications: Optional[str] = None,
                             preferred_qualifications: Optional[str] = None,
                             posted_at: Optional[str] = None,
                             deadline: Optional[str] = None,
                             source_snapshot: Optional[str] = None) -> dict:
    """공고 1건을 저장한다. 이미 있으면 last_seen_at 등을 갱신한다. 날짜는 YYYY-MM-DD."""
    if category is not None and category not in ALLOWED_CATEGORIES:
        raise ValueError(f"category는 {ALLOWED_CATEGORIES} 중 하나여야 함: {category!r}")
    p = await pool()
    async with p.acquire() as conn:
        cid = await get_company_id_by_name(conn, company_name)
        row = await conn.fetchrow(
            """INSERT INTO job_postings
                 (company_id, title, url, location, employment_type, min_years, category,
                  responsibilities, required_qualifications, preferred_qualifications,
                  posted_at, deadline, source_snapshot)
               VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13)
               ON CONFLICT (company_id, title, url) DO UPDATE
                 SET last_seen_at = now(), is_active = true,
                     responsibilities = EXCLUDED.responsibilities,
                     required_qualifications = EXCLUDED.required_qualifications,
                     preferred_qualifications = EXCLUDED.preferred_qualifications,
                     deadline = EXCLUDED.deadline
               RETURNING id, (xmax = 0) AS inserted""",
            cid, title, url, location, employment_type, min_years, category,
            responsibilities, required_qualifications, preferred_qualifications,
            _date(posted_at), _date(deadline), source_snapshot)
    return dict(row)


@mcp.tool()
async def list_known_urls(site_url: str, candidate_urls: Optional[list[str]] = None) -> list[str]:
    """이 사이트에서 이미 저장해 둔 활성 공고의 URL 목록. 이미 저장된 공고의 JD를 다시 읽지 않으려고 쓴다.

    Args:
      site_url: 크롤링하는 사이트 링크. record_scrape_run의 source와 같은 값을 쓴다
        (회사 자사 채용 사이트면 portal_url, 채용 플랫폼이면 'https://www.saramin.co.kr' 같은 사이트 링크).
        공고 URL의 호스트가 이 사이트와 같거나 그 하위 도메인인 공고를 찾는다('www.'와 경로는 무시).
        회사명은 쓰지 않는다: 같은 회사의 공고라도 사이트가 다르면 따로 센다.
      candidate_urls: 이번 목록에서 본 후보 URL들. 주면 그중 이미 저장된 것만 돌려준다.
        사람인·원티드처럼 저장된 공고가 많은 사이트는 전체 목록 대신 이 인자를 쓴다.
    """
    return await fetch_urls_seen_on_site("job_postings", site_url, candidate_urls, only_active=True)


@mcp.tool()
async def touch_postings(site_url: str, urls: list[str]) -> dict:
    """이번에도 이 사이트의 목록에서 확인된 기존 공고의 last_seen_at을 갱신한다 (JD 재파싱 없이).

    Args:
      site_url: 크롤링하는 사이트 링크. list_known_urls와 같은 기준이다(회사명은 쓰지 않는다).
      urls: 이번에 본 공고 URL들. 호스트가 site_url의 사이트에 속하는 저장된 공고만 갱신한다.
        그래서 list_known_urls가 돌려준 URL을 회사별로 나눌 필요 없이 한 번에 넘기면 된다.
    """
    site_host = normalize_site_host(site_url)
    p = await pool()
    res = await p.execute(
        f"""UPDATE job_postings SET last_seen_at = now(), is_active = true
            WHERE id IN (
                SELECT id FROM (SELECT id, {POSTING_URL_HOST_SQL} AS host
                                FROM job_postings WHERE url = ANY($2::text[])) seen
                WHERE host = $1 OR right(host, length($1) + 1) = '.' || $1)""",
        site_host, urls)
    return {"updated": int(res.split()[-1])}


@mcp.tool()
async def record_filtered_postings(company_name: str, items: list[dict]) -> dict:
    """AI 관련이 아니라서 걸러낸 공고들을 한 번에 기록한다 (필터 품질 검수용).

    items의 각 원소: {"title": str, "stage": "list"|"detail", "reason": str(제외 이유 한 줄),
                      "url": str(선택), "location": str(선택)}
    stage: list=목록의 제목·직군만 보고 제외, detail=JD 본문을 읽고 제외.
    이미 기록된 공고는 stage/reason/last_seen_at만 갱신하며, 사람이 매긴 is_actually_ai는 유지한다.
    """
    rows = []
    for it in items:
        title, stage = it.get("title"), it.get("stage")
        if not title or stage not in ("list", "detail"):
            raise ValueError(f"title과 stage(list|detail)는 필수: {it!r}")
        rows.append((title, it.get("url"), it.get("location"), stage, it.get("reason")))
    p = await pool()
    async with p.acquire() as conn:
        cid = await get_company_id_by_name(conn, company_name)
        async with conn.transaction():
            await conn.executemany(
                """INSERT INTO filtered_postings (company_id, title, url, location, stage, reason)
                   VALUES ($1, $2, $3, $4, $5, $6)
                   ON CONFLICT (company_id, title, url) DO UPDATE
                     SET stage = EXCLUDED.stage, reason = EXCLUDED.reason,
                         location = COALESCE(EXCLUDED.location, filtered_postings.location),
                         last_seen_at = now()""",
                [(cid, *r) for r in rows])
    return {"recorded": len(rows)}


@mcp.tool()
async def list_filtered_urls(site_url: str, candidate_urls: Optional[list[str]] = None) -> list[str]:
    """이 사이트에서 이미 걸러낸(AI 공고가 아니라고 기록한) 공고의 URL 목록. 같은 공고의 JD를 매번 다시 읽지 않으려고 쓴다.

    Args:
      site_url, candidate_urls: list_known_urls와 같다.
    """
    return await fetch_urls_seen_on_site("filtered_postings", site_url, candidate_urls, only_active=False)


SCRAPE_RUN_STATUSES = ("ok", "failed", "unverifiable")


@mcp.tool()
async def record_scrape_run(source: str, status: str, detail: Optional[str] = None) -> dict:
    """크롤링한 사이트 1곳의 실행 결과를 scrape_runs에 기록한다. 사이트 1곳당 실행마다 1번 호출한다.

    Args:
      source: 크롤링한 사이트. scrape_runs의 키라서 같은 사이트는 매번 같은 문자열로 넣는다.
        - (A) 회사 채용 포털을 크롤링했을 때: list_companies의 portal_url을 그대로 넣는다.
        - (B) 채용 플랫폼(사람인·잡코리아 등)을 크롤링했을 때: 그 사이트 링크를 넣는다
          (예: 'https://www.saramin.co.kr'). 회사명은 넣지 않는다.
      status: 'ok'(정상 확인) | 'failed'(오류) | 'unverifiable'(JS 렌더링·robots 차단 등으로 확인 불가)
      detail: ok면 건수 요약, failed/unverifiable이면 사유.

    Returns:
      {"id": 기록 id}
    """
    if status not in SCRAPE_RUN_STATUSES:
        raise ValueError(f"status는 {SCRAPE_RUN_STATUSES} 중 하나여야 함: {status!r}")
    normalized_source = source.strip()
    if not normalized_source:
        raise ValueError("source는 비어 있을 수 없음 (portal_url 또는 사이트 링크)")

    p = await pool()
    run_id = await p.fetchval(
        "INSERT INTO scrape_runs (source, status, detail) VALUES ($1,$2,$3) RETURNING id",
        normalized_source, status, detail)
    return {"id": run_id}


@mcp.tool()
async def list_latest_scrape_status_by_source() -> list[dict]:
    """크롤링한 사이트(source)별 가장 최근 실행 결과를 반환한다.

    회사 목록(list_companies)과 별개로, 어떤 사이트가 최근에 실패/확인불가였는지 점검할 때 쓴다.
    source는 회사 portal_url이거나 채용 플랫폼 링크다.

    Returns:
      [{"source", "status", "detail", "run_at"(ISO 8601)}], 최근 실행 순.
    """
    p = await pool()
    rows = await p.fetch(
        """SELECT DISTINCT ON (source) source, status, detail, run_at
           FROM scrape_runs
           ORDER BY source, run_at DESC""")
    latest_runs = [{**dict(r), "run_at": r["run_at"].isoformat()} for r in rows]
    return sorted(latest_runs, key=lambda run: run["run_at"], reverse=True)


def _posting_text(r) -> str:
    """임베딩 원문: 역할·자격요건 중심으로 합친다."""
    parts = [r["title"], r["category"], r["responsibilities"],
             r["required_qualifications"], r["preferred_qualifications"]]
    return "\n".join(p for p in parts if p)


# 임베딩 방식이 바뀌면 올린다 (hash가 달라져 전체 재임베딩됨)
EMBED_VERSION = "v3-fields-raw"
FIELD_COLUMNS = ("responsibilities", "required_qualifications", "preferred_qualifications")


def _field_texts(r) -> list[Optional[str]]:
    """컬럼별 임베딩 원문(본문만). 값이 없으면 None.

    제목을 앞에 붙이면 세 컬럼 벡터가 제목 쪽으로 쏠려 컬럼 간 유사도 차이가 1/2~1/3로 줄었다(CHANGELOG #20).
    직무명(제목)은 semantic_search_jobs의 title_keywords 키워드 필터로 다룬다.
    """
    return [r[col] or None for col in FIELD_COLUMNS]


async def embed_pending(limit: int = 100) -> dict:
    """embedding이 없거나 원문이 바뀐 활성 공고를 배치 임베딩한다."""
    limit = max(1, min(int(limit), 500))
    p = await pool()
    rows = await p.fetch(
        """SELECT id, title, category, responsibilities, required_qualifications,
                  preferred_qualifications, embedding_hash
           FROM job_postings WHERE is_active ORDER BY id""")
    todo = []
    for r in rows:
        texts = [_posting_text(r)] + _field_texts(r)   # 통합 1개 + 컬럼별 3개(없으면 None)
        h = text_hash(EMBED_VERSION + "\x00".join(t or "" for t in texts))
        if h != r["embedding_hash"]:
            todo.append((r["id"], texts, h))
    remaining = max(0, len(todo) - limit)
    todo = todo[:limit]
    flat = [t for _, texts, _ in todo for t in texts if t]
    it = iter(await embed_documents(flat))
    params = []
    for pid, texts, h in todo:
        vecs = [to_pg(next(it)) if t else None for t in texts]
        params.append((pid, *vecs, h))
    async with p.acquire() as conn, conn.transaction():
        await conn.executemany(
            """UPDATE job_postings
               SET embedding = $2::vector, embedding_responsibilities = $3::vector,
                   embedding_required = $4::vector, embedding_preferred = $5::vector,
                   embedding_hash = $6
               WHERE id = $1""",
            params)
    return {"embedded": len(todo), "remaining": remaining}


async def delete_expired_and_urlless_postings_in(conn) -> list[dict]:
    """마감일이 오늘(한국 시간) 이전인 공고와 URL이 없는 공고를 지우고, 지운 공고를 돌려준다."""
    rows = await conn.fetch(
        """DELETE FROM job_postings j
           USING companies c
           WHERE c.id = j.company_id
             AND ((j.deadline IS NOT NULL AND j.deadline < (now() AT TIME ZONE 'Asia/Seoul')::date)
                  OR j.url IS NULL OR btrim(j.url) = '')
           RETURNING j.id, c.name AS company, j.title, j.url, j.deadline,
                     CASE WHEN j.url IS NULL OR btrim(j.url) = '' THEN 'url_missing' ELSE 'expired' END AS reason""")
    return [{**dict(r), "deadline": r["deadline"].isoformat() if r["deadline"] else None} for r in rows]


@mcp.tool()
async def delete_expired_and_urlless_postings() -> dict:
    """마감일이 지난 공고와 URL이 비어 있는 공고를 job_postings에서 삭제한다. 수집이 모두 끝난 뒤 한 번 호출한다.

    - 만료: deadline이 오늘(한국 시간)보다 이전인 공고. 마감일이 오늘이면 아직 열려 있으므로 남긴다.
      deadline이 비어 있는 공고(상시채용 등)는 만료로 보지 않는다.
    - URL 없음: url이 NULL이거나 공백뿐인 공고. URL이 없으면 이후 갱신·중복 확인이 안 된다.
    삭제는 되돌릴 수 없다. 이 공고를 분석 기준으로 쓴 회사 리서치(company_research)는 posting_id만 비워지고 남는다.
    임베딩 대상이 줄도록 embed_pending_postings보다 먼저 호출한다.

    Returns:
      {"deleted": 삭제 건수, "expired": 만료로 지운 건수, "url_missing": URL 없어서 지운 건수,
       "postings": [{"id", "company", "title", "url", "deadline", "reason"}]}
    """
    p = await pool()
    async with p.acquire() as conn, conn.transaction():
        deleted_postings = await delete_expired_and_urlless_postings_in(conn)
    return {"deleted": len(deleted_postings),
            "expired": sum(1 for posting in deleted_postings if posting["reason"] == "expired"),
            "url_missing": sum(1 for posting in deleted_postings if posting["reason"] == "url_missing"),
            "postings": deleted_postings}


@mcp.tool()
async def embed_pending_postings(limit: int = 100) -> dict:
    """임베딩이 없거나 본문이 바뀐 공고를 임베딩한다. 적재가 끝난 뒤 한 번 호출한다."""
    return await embed_pending(limit)


@mcp.tool()
async def find_active_postings_by_company_and_title(company_name: str,
                                                    title_keywords: Optional[list[str]] = None) -> list[dict]:
    """회사명과 제목 키워드로 이미 저장된 활성 공고를 찾는다. 같은 공고가 다른 사이트에서 이미 저장됐는지(중복) 확인하는 용도.

    임베딩 없이 SQL 필터만 쓰므로 가볍다. 등록되지 않은 회사여도 오류 없이 빈 목록을 돌려준다.

    Args:
      company_name: 회사명. 부분일치(대소문자 무시)라서 표기가 조금 달라도 찾는다.
      title_keywords: 직무명 키워드(예: ["MLOps", "LLM"]). 제목에 하나라도 들어간 공고만. 없으면 그 회사의 활성 공고 전체.

    Returns:
      [{"id", "company", "title", "url", "location"}], 최근 확인 순 최대 20건.
    """
    clean_keywords = [keyword.strip() for keyword in (title_keywords or []) if keyword and keyword.strip()]
    p = await pool()
    rows = await p.fetch(
        """SELECT j.id, c.name AS company, j.title, j.url, j.location
           FROM job_postings j JOIN companies c ON c.id = j.company_id
           WHERE j.is_active AND c.name ILIKE $1
             AND ($2::text[] IS NULL OR j.title ILIKE ANY($2::text[]))
           ORDER BY j.last_seen_at DESC LIMIT 20""",
        f"%{company_name}%", [f"%{keyword}%" for keyword in clean_keywords] or None)
    return [dict(r) for r in rows]


@mcp.tool()
async def list_companies() -> list[dict]:
    """등록된 회사(고용주)와 채용 포털 접근 방식을 반환한다.

    사이트별 최근 크롤링 결과는 list_latest_scrape_status_by_source로 따로 조회한다.
    """
    p = await pool()
    rows = await p.fetch(
        """SELECT name, industry, portal_url, access_method, notes
           FROM companies ORDER BY name""")
    return [dict(r) for r in rows]


if __name__ == "__main__":
    mcp.run()
