"""개인 프로필 MCP 서버(profile-db): 이력서·경력기술서·포트폴리오 조회 전용. 쓰기는 scripts/ingest_profile.py가 한다.

job-db와 분리해 두어, job-db만 등록된 Claude Desktop 예약 작업에는 개인 자료가 노출되지 않는다.
"""
import json
import sys
from contextlib import asynccontextmanager
from pathlib import Path

from mcp.server.fastmcp import FastMCP

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from mcp_server import server as db  # noqa: E402  (pool, .env 로딩 재사용)
from mcp_server.embeddings import embed_query, to_pg  # noqa: E402


@asynccontextmanager
async def lifespan(_server):
    async with db.lifespan(_server):
        yield


mcp = FastMCP("profile-db", lifespan=lifespan)


@mcp.tool()
async def get_profile_summary() -> dict:
    """구조화된 내 프로필 요약(스킬, 총 경력, 도메인, 주요 프로젝트 등). 적재된 자료가 없으면 빈 dict."""
    p = await db.pool()
    raw = await p.fetchval("SELECT summary FROM profile_summary WHERE id = 1")
    return json.loads(raw) if raw else {}


@mcp.tool()
async def search_profile(query: str, limit: int = 5) -> list[dict]:
    """내 이력서·경력기술서·포트폴리오에서 query와 관련된 원문 조각을 찾는다 (근거 인용용)."""
    limit = max(1, min(int(limit), 20))
    p = await db.pool()
    rows = await p.fetch(
        f"""SELECT d.filename, d.kind, c.chunk_index, c.text,
                   1 - (c.embedding <=> $1::vector) AS similarity
            FROM profile_chunks c JOIN profile_documents d ON d.id = c.document_id
            ORDER BY c.embedding <=> $1::vector LIMIT {limit}""",
        to_pg(await embed_query(query)))
    return [dict(r) for r in rows]


@mcp.tool()
async def list_profile_documents() -> list[dict]:
    """적재된 프로필 문서 목록."""
    p = await db.pool()
    rows = await p.fetch(
        """SELECT d.filename, d.kind, length(d.raw_text) AS chars, d.updated_at::text AS updated_at,
                  (SELECT count(*) FROM profile_chunks c WHERE c.document_id = d.id) AS chunks
           FROM profile_documents d ORDER BY d.kind, d.filename""")
    return [dict(r) for r in rows]


if __name__ == "__main__":
    mcp.run()
