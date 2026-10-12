"""커스텀 MCP 서버: 정해진 tool만 노출하며 임의 SQL은 실행하지 않는다."""
import os
from contextlib import asynccontextmanager
from datetime import date
from typing import Optional

import asyncpg
from dotenv import load_dotenv
from mcp.server.fastmcp import FastMCP

load_dotenv()

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
