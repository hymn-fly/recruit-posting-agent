"""MCP 서버들이 함께 쓰는 DB 접근 코드: .env 로딩, 커넥션 풀, 회사 id 조회.

서버마다 프로세스가 따로 뜨므로 풀도 서버(프로세스)마다 하나씩 만들어진다.
"""
import os
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Optional

import asyncpg
from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parent.parent / ".env")

_pool: Optional[asyncpg.Pool] = None

# 한국 시간 기준 오늘 날짜(date). 마감일(deadline) 비교에 쓴다
KST_TODAY_SQL = "(now() AT TIME ZONE 'Asia/Seoul')::date"


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


async def get_company_id_by_name(conn, company_name: str) -> int:
    """회사명(정확히 일치)으로 companies.id를 찾는다. 등록되지 않은 회사면 ValueError."""
    company_id = await conn.fetchval("SELECT id FROM companies WHERE name = $1", company_name)
    if company_id is None:
        raise ValueError(f"등록되지 않은 회사: {company_name} (먼저 upsert_company 호출)")
    return company_id
