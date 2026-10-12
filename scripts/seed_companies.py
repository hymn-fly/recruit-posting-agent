"""scrapers/companies.py의 회사 메타데이터를 Neon companies 테이블에 넣는다 (LLM 호출 없음).

사용법: uv run python scripts/seed_companies.py
"""
import asyncio
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import asyncpg  # noqa: E402
from dotenv import load_dotenv  # noqa: E402

load_dotenv(ROOT / ".env")

from scrapers.companies import COMPANIES  # noqa: E402


async def main():
    conn = await asyncpg.connect(os.environ["NEON_DATABASE_URL"])
    try:
        for c in COMPANIES:
            await conn.execute(
                """INSERT INTO companies (name, portal_url, access_method, notes, industry)
                   VALUES ($1, $2, $3, $4, $5)
                   ON CONFLICT (name) DO UPDATE
                     SET portal_url = EXCLUDED.portal_url, access_method = EXCLUDED.access_method,
                         notes = EXCLUDED.notes, industry = EXCLUDED.industry""",
                c["name"], c["portal_url"], c["access_method"], c["notes"], c["industry"])
        n = await conn.fetchval("SELECT count(*) FROM companies")
        print(f"시딩 완료: {len(COMPANIES)}개 처리, companies 총 {n}행")
    finally:
        await conn.close()


if __name__ == "__main__":
    asyncio.run(main())
