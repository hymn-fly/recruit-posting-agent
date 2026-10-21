"""커스텀 MCP 서버: 정해진 tool만 노출하며 임의 SQL은 실행하지 않는다."""
import json
import os
import sys
from contextlib import asynccontextmanager
from datetime import date
from pathlib import Path
from typing import Optional

import asyncpg
from dotenv import load_dotenv
from mcp.server.fastmcp import FastMCP

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
load_dotenv(Path(__file__).resolve().parent.parent / ".env")

from mcp_server.embeddings import embed_documents, embed_query, text_hash, to_pg  # noqa: E402

_pool: Optional[asyncpg.Pool] = None

# AI Engineer 관련으로 적재하는 직군 (agents/state.py의 AiCategory와 동일)
ALLOWED_CATEGORIES = ("AI Engineer", "ML Engineer", "LLM Engineer", "MLOps", "Data")


async def pool() -> asyncpg.Pool:
    global _pool
    if _pool is None:
        _pool = await asyncpg.create_pool(os.environ["NEON_DATABASE_URL"], min_size=1, max_size=5)
    return _pool


@asynccontextmanager
async def lifespan(_server):
    await pool()
    try:
        yield
    finally:
        if _pool is not None:
            await _pool.close()


mcp = FastMCP("job-db", lifespan=lifespan)


def _date(value: Optional[str]) -> Optional[date]:
    return date.fromisoformat(value) if value else None


async def _company_id(conn, company_name: str) -> int:
    cid = await conn.fetchval("SELECT id FROM companies WHERE name = $1", company_name)
    if cid is None:
        raise ValueError(f"등록되지 않은 회사: {company_name} (먼저 upsert_company 호출)")
    return cid


@mcp.tool()
async def upsert_company(name: str, portal_url: str, access_method: str = "unknown",
                         notes: Optional[str] = None, industry: Optional[str] = None) -> dict:
    """회사/포털 메타데이터를 등록하거나 갱신한다."""
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
        cid = await _company_id(conn, company_name)
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
async def list_known_urls(company_name: str) -> list[str]:
    """해당 회사의 활성 공고 URL 목록. 이미 적재된 공고는 JD를 다시 읽지 않고 건너뛰기 위해 쓴다."""
    p = await pool()
    async with p.acquire() as conn:
        cid = await _company_id(conn, company_name)
        rows = await conn.fetch(
            "SELECT url FROM job_postings WHERE company_id = $1 AND is_active AND url IS NOT NULL",
            cid)
    return [r["url"] for r in rows]


@mcp.tool()
async def touch_postings(company_name: str, urls: list[str]) -> dict:
    """이번에도 목록에서 확인된 기존 공고의 last_seen_at을 갱신한다 (JD 재파싱 없이)."""
    p = await pool()
    async with p.acquire() as conn:
        cid = await _company_id(conn, company_name)
        res = await conn.execute(
            """UPDATE job_postings SET last_seen_at = now(), is_active = true
               WHERE company_id = $1 AND url = ANY($2::text[])""",
            cid, urls)
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
        cid = await _company_id(conn, company_name)
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
async def list_filtered_urls(company_name: str) -> list[str]:
    """이미 걸러낸 공고의 URL 목록. 같은 공고의 JD를 매번 다시 읽지 않기 위해 쓴다."""
    p = await pool()
    async with p.acquire() as conn:
        cid = await _company_id(conn, company_name)
        rows = await conn.fetch(
            "SELECT url FROM filtered_postings WHERE company_id = $1 AND url IS NOT NULL", cid)
    return [r["url"] for r in rows]


@mcp.tool()
async def record_scrape_run(company_name: str, status: str, detail: Optional[str] = None) -> dict:
    """회사별 스크래핑 결과 기록. status: 'ok' | 'failed' | 'unverifiable'."""
    if status not in ("ok", "failed", "unverifiable"):
        raise ValueError("status must be ok | failed | unverifiable")
    p = await pool()
    async with p.acquire() as conn:
        cid = await _company_id(conn, company_name)
        rid = await conn.fetchval(
            "INSERT INTO scrape_runs (company_id, status, detail) VALUES ($1,$2,$3) RETURNING id",
            cid, status, detail)
    return {"id": rid}


@mcp.tool()
async def search_jobs(keyword: Optional[str] = None, company_name: Optional[str] = None,
                      category: Optional[str] = None, location: Optional[str] = None,
                      only_active: bool = True, limit: int = 20) -> list[dict]:
    """조건에 맞는 공고를 검색한다 (파라미터 바인딩된 SQL만 실행)."""
    conds, args = [], []

    def add(sql: str, value):
        args.append(value)
        conds.append(sql.format(n=len(args)))

    if keyword:
        add("(j.title ILIKE ${n} OR j.responsibilities ILIKE ${n} "
            "OR j.required_qualifications ILIKE ${n} OR j.preferred_qualifications ILIKE ${n})",
            f"%{keyword}%")
    if company_name:
        add("c.name ILIKE ${n}", f"%{company_name}%")
    if category:
        add("j.category ILIKE ${n}", f"%{category}%")
    if location:
        add("j.location ILIKE ${n}", f"%{location}%")
    if only_active:
        conds.append("j.is_active")
    where = ("WHERE " + " AND ".join(conds)) if conds else ""
    limit = max(1, min(int(limit), 100))
    sql = f"""SELECT c.name AS company, c.industry, j.title, j.url, j.location, j.employment_type,
                     j.min_years, j.category, j.responsibilities, j.required_qualifications,
                     j.preferred_qualifications, j.posted_at, j.deadline
              FROM job_postings j JOIN companies c ON c.id = j.company_id
              {where} ORDER BY j.last_seen_at DESC LIMIT {limit}"""
    p = await pool()
    rows = await p.fetch(sql, *args)
    return [{k: (v.isoformat() if isinstance(v, date) else v) for k, v in dict(r).items()}
            for r in rows]


def _row(r) -> dict:
    return {k: (v.isoformat() if isinstance(v, date) else v) for k, v in dict(r).items()}


def _posting_text(r) -> str:
    """임베딩 원문: 역할·자격요건 중심으로 합친다."""
    parts = [r["title"], r["category"], r["responsibilities"],
             r["required_qualifications"], r["preferred_qualifications"]]
    return "\n".join(p for p in parts if p)


# 임베딩 방식이 바뀌면 올린다 (hash가 달라져 전체 재임베딩됨)
EMBED_VERSION = "v3-fields-raw"
FIELD_COLUMNS = ("responsibilities", "required_qualifications", "preferred_qualifications")
# 컬럼별 검색 기본 가중치 (하는 일 / 필수 / 우대)
DEFAULT_WEIGHTS = (0.5, 0.35, 0.15)
CANDIDATE_POOL = 200   # 통합 벡터 HNSW로 먼저 뽑는 후보 수. 이 안에서 컬럼별 가중 점수로 재정렬


def _field_texts(r) -> list[Optional[str]]:
    """컬럼별 임베딩 원문(본문만). 값이 없으면 None.

    제목을 앞에 붙이면 세 컬럼 벡터가 제목 쪽으로 쏠려 컬럼 간 유사도 차이가 1/2~1/3로 줄었다(CHANGELOG #20).
    제목은 통합 벡터(embedding)에 들어 있고 후보 선정에 쓰인다.
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


@mcp.tool()
async def embed_pending_postings(limit: int = 100) -> dict:
    """임베딩이 없거나 본문이 바뀐 공고를 임베딩한다. 적재가 끝난 뒤 한 번 호출한다."""
    return await embed_pending(limit)


@mcp.tool()
async def semantic_search_jobs(query: str, category: Optional[str] = None,
                               company_name: Optional[str] = None,
                               location: Optional[str] = None,
                               max_years: Optional[int] = None,
                               w_responsibilities: float = DEFAULT_WEIGHTS[0],
                               w_required: float = DEFAULT_WEIGHTS[1],
                               w_preferred: float = DEFAULT_WEIGHTS[2],
                               only_active: bool = True, limit: int = 20) -> list[dict]:
    """역할·자격요건 의미 기반(임베딩) 공고 검색. query는 원하는 역할/경험을 서술한 문장.

    하는 일/필수/우대 역량을 컬럼별로 비교해 가중합한 similarity(0~1, 클수록 유사)로 정렬한다.
    값이 없는 컬럼은 빼고 나머지 가중치로 정규화하며, 세 컬럼이 모두 없으면 통합 임베딩 유사도를 쓴다.
    w_*: 컬럼별 가중치(예: 자격요건 위주로 찾을 때 w_required를 높임). sim_*에 컬럼별 유사도도 돌려준다.
    max_years: 요구 최소 경력이 이 값 이하인 공고(또는 명시 없음)만.
    """
    conds, args = ["j.embedding IS NOT NULL"], [to_pg(await embed_query(query))]
    weights = [max(0.0, float(w)) for w in (w_responsibilities, w_required, w_preferred)]
    if not any(weights):
        weights = list(DEFAULT_WEIGHTS)
    args += weights   # $2, $3, $4

    def add(sql: str, value):
        args.append(value)
        conds.append(sql.format(n=len(args)))

    if company_name:
        add("c.name ILIKE ${n}", f"%{company_name}%")
    if category:
        add("j.category ILIKE ${n}", f"%{category}%")
    if location:
        add("j.location ILIKE ${n}", f"%{location}%")
    if max_years is not None:
        add("(j.min_years IS NULL OR j.min_years <= ${n})", int(max_years))
    if only_active:
        conds.append("j.is_active")
    limit = max(1, min(int(limit), 100))
    sql = f"""
        WITH cand AS (
            SELECT j.id, c.name AS company, c.industry, j.title, j.url, j.location,
                   j.employment_type, j.min_years, j.category, j.responsibilities,
                   j.required_qualifications, j.preferred_qualifications, j.deadline,
                   1 - (j.embedding <=> $1::vector)                  AS sim_all,
                   1 - (j.embedding_responsibilities <=> $1::vector) AS sim_responsibilities,
                   1 - (j.embedding_required <=> $1::vector)         AS sim_required,
                   1 - (j.embedding_preferred <=> $1::vector)        AS sim_preferred
            FROM job_postings j JOIN companies c ON c.id = j.company_id
            WHERE {" AND ".join(conds)}
            ORDER BY j.embedding <=> $1::vector LIMIT {CANDIDATE_POOL}
        ), scored AS (
            SELECT *,
                   COALESCE($2::float8 * sim_responsibilities, 0) + COALESCE($3::float8 * sim_required, 0)
                     + COALESCE($4::float8 * sim_preferred, 0) AS num,
                   (CASE WHEN sim_responsibilities IS NULL THEN 0 ELSE $2::float8 END)
                     + (CASE WHEN sim_required IS NULL THEN 0 ELSE $3::float8 END)
                     + (CASE WHEN sim_preferred IS NULL THEN 0 ELSE $4::float8 END) AS den
            FROM cand
        )
        SELECT id, company, industry, title, url, location, employment_type, min_years, category,
               responsibilities, required_qualifications, preferred_qualifications, deadline,
               CASE WHEN den > 0 THEN num / den ELSE sim_all END AS similarity,
               sim_responsibilities, sim_required, sim_preferred
        FROM scored ORDER BY similarity DESC LIMIT {limit}"""
    p = await pool()
    return [_row(r) for r in await p.fetch(sql, *args)]


@mcp.tool()
async def get_job_posting(id: int) -> dict:
    """공고 1건 전체(하는 일/필수/우대 역량)와 회사·산업 정보."""
    p = await pool()
    r = await p.fetchrow(
        """SELECT j.id, c.name AS company, c.industry, j.title, j.url, j.location,
                  j.employment_type, j.min_years, j.category, j.responsibilities,
                  j.required_qualifications, j.preferred_qualifications, j.posted_at,
                  j.deadline, j.is_active
           FROM job_postings j JOIN companies c ON c.id = j.company_id WHERE j.id = $1""", id)
    if r is None:
        raise ValueError(f"공고 없음: id={id}")
    return _row(r)


@mcp.tool()
async def get_cached_research(company_name: str, target_company: str, max_age_days: int = 14) -> dict:
    """해당 회사·계열사의 가장 최근 리서치(max_age_days 이내). 없으면 빈 dict. topics의 회사 단위 토픽을 재사용한다.

    target_company: 실제 채용 계열사명 (그룹 포털이 아니면 company_name과 같아도 됨).
    """
    p = await pool()
    async with p.acquire() as conn:
        cid = await _company_id(conn, company_name)
        r = await conn.fetchrow(
            """SELECT id, posting_id, target_company, topics::text AS topics, report_md,
                      created_at::text AS created_at
               FROM company_research
               WHERE company_id = $1 AND target_company = $2
                 AND created_at > now() - make_interval(days => $3)
               ORDER BY created_at DESC LIMIT 1""",
            cid, target_company, int(max_age_days))
    if r is None:
        return {}
    return {**dict(r), "topics": json.loads(r["topics"])}


@mcp.tool()
async def save_research(company_name: str, target_company: str, topics: list[dict], report_md: str,
                        posting_id: Optional[int] = None) -> dict:
    """리서치 결과 저장. topics: [{"topic", "summary", "sources": [{"title", "url"}]}]."""
    p = await pool()
    async with p.acquire() as conn:
        cid = await _company_id(conn, company_name)
        rid = await conn.fetchval(
            """INSERT INTO company_research (company_id, target_company, posting_id, topics, report_md)
               VALUES ($1, $2, $3, $4::jsonb, $5) RETURNING id""",
            cid, target_company, posting_id, json.dumps(topics, ensure_ascii=False), report_md)
    return {"id": rid}


@mcp.tool()
async def list_companies() -> list[dict]:
    """등록된 회사와 접근성 상태, 최근 스크래핑 결과를 반환한다."""
    p = await pool()
    rows = await p.fetch(
        """SELECT c.name, c.industry, c.portal_url, c.access_method, c.notes,
                  (SELECT status FROM scrape_runs s WHERE s.company_id = c.id
                   ORDER BY run_at DESC LIMIT 1) AS last_status
           FROM companies c ORDER BY c.name""")
    return [dict(r) for r in rows]


if __name__ == "__main__":
    mcp.run()
