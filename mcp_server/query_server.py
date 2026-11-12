"""조회·분석용 MCP 서버(job-query): 공고 검색, 공고 상세, 회사 리서치 캐시.

대화형 에이전트(agents/)가 쓰고, 적재(예약 작업)용 job-db에는 노출하지 않는다.
공고 적재·크롤링 기록 tool은 collect_server.py(job-db)에 있다.
"""
import asyncio
import json
import logging
import time
import sys
from datetime import date
from pathlib import Path
from typing import Optional

from mcp.server.fastmcp import FastMCP

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from common.observability import configure_logging  # noqa: E402

log = logging.getLogger(__name__ if __name__ != "__main__" else "mcp_server.query_server")

from mcp_server.db import KST_TODAY_SQL, get_company_id_by_name, lifespan, pool  # noqa: E402
from mcp_server.embeddings import embed_query, to_pg  # noqa: E402

mcp = FastMCP("job-query", lifespan=lifespan)


def _row(record) -> dict:
    return {k: (v.isoformat() if isinstance(v, date) else v) for k, v in dict(record).items()}


# 의미 검색 질의 인자 → (비교할 컬럼별 임베딩, 결과에 붙일 유사도 키)
FIELD_QUERY_TO_EMBEDDING = {
    "q_responsibilities": ("embedding_responsibilities", "sim_responsibilities"),
    "q_required": ("embedding_required", "sim_required"),
    "q_preferred": ("embedding_preferred", "sim_preferred"),
}


def build_posting_filter_conditions(title_keywords, company_name, category, location, max_years,
                                    only_active, deadline_within_days=None,
                                    require_deadline=False) -> tuple[list[str], list]:
    """WHERE로 확정할 수 있는 조건(SQL 조각, 바인딩 값)을 만든다. 값은 모두 $n 파라미터로 넘긴다."""
    conditions, args = [], []

    def add(sql: str, value):
        args.append(value)
        conditions.append(sql.format(n=len(args)))

    titles = [t.strip() for t in (title_keywords or []) if t and t.strip()]
    if titles:   # 제목에 키워드가 하나라도 들어가면 통과
        add("j.title ILIKE ANY(${n}::text[])", [f"%{t}%" for t in titles])
    if company_name:
        add("c.name ILIKE ${n}", f"%{company_name}%")
    if category:
        add("j.category ILIKE ${n}", f"%{category}%")
    if location:
        add("j.location ILIKE ${n}", f"%{location}%")
    if max_years is not None:
        add("(j.min_years IS NULL OR j.min_years <= ${n})", int(max_years))
    if only_active:   # 활성이면서 마감이 지나지 않은 공고(마감일이 없는 상시 공고는 포함)
        conditions.append("j.is_active")
        conditions.append(f"(j.deadline IS NULL OR j.deadline >= {KST_TODAY_SQL})")
    if require_deadline:   # 마감일이 적혀 있는 공고만
        conditions.append("j.deadline IS NOT NULL")
    if deadline_within_days is not None:   # 오늘부터 N일 안에 마감 (마감일이 없는 공고는 빠진다)
        add(f"j.deadline BETWEEN {KST_TODAY_SQL} AND {KST_TODAY_SQL} + ${{n}}::int", int(deadline_within_days))
    return conditions, args


def score_posting_by_field_similarity(field_similarities: dict[str, Optional[float]]) -> Optional[float]:
    """질의가 주어진 컬럼들의 유사도를 공고 하나의 점수(클수록 잘 맞음)로 합친다. None이면 결과에서 뺀다.

    field_similarities: 질의가 주어진 컬럼만 들어 있다. 예) {"sim_responsibilities": 0.78, "sim_required": None}
      값이 None이면 그 공고에 해당 본문이 없어 비교하지 못한 것이다.
    """
    total_score = 0
    for _, score in field_similarities.items():
        if score:
            total_score += score
    return total_score


@mcp.tool()
async def semantic_search_jobs(title_keywords: Optional[list[str]] = None,
                               q_responsibilities: Optional[str] = None,
                               q_required: Optional[str] = None,
                               q_preferred: Optional[str] = None,
                               company_name: Optional[str] = None,
                               category: Optional[str] = None,
                               location: Optional[str] = None,
                               max_years: Optional[int] = None,
                               deadline_within_days: Optional[int] = None,
                               sort_by_deadline: bool = False,
                               only_active: bool = True, limit: int = 20) -> list[dict]:
    """채용공고 검색: WHERE 조건으로 먼저 거른 뒤, 남은 공고 전부를 컬럼별 임베딩으로 정확히 비교한다.

    필터(정확히 일치/부분일치):
      title_keywords: 직무명 키워드(예: ["MLOps", "엠엘옵스"]). 제목에 하나라도 들어간 공고만.
      company_name / category / location: 부분일치. max_years: 요구 최소 경력이 이 값 이하(또는 명시 없음).
      deadline_within_days: 오늘(한국 시간)부터 N일 안에 마감하는 공고만. 마감일이 없는 상시 공고는 빠진다.
      only_active=True(기본)이면 마감이 지난 공고는 항상 빠진다(마감일이 없는 상시 공고는 남는다).
    정렬:
      sort_by_deadline=True: 마감일이 적힌 공고만, 마감이 가까운 순으로 돌려준다('마감 임박'을 물을 때).
        q_*도 주면 "어떤 공고를 고를지"는 유사도로, "보여주는 순서"는 마감일로 정한다:
        유사도 상위 limit건을 고른 뒤 그 안에서 마감이 가까운 순(같으면 유사도 높은 순)으로 정렬한다.
        q_*가 없으면 유사도 없이 마감이 가까운 순으로 limit건(similarity=None).
    의미 검색(각 질의를 해당 컬럼 임베딩과 cosine 유사도로 비교, 주어진 것만):
      q_responsibilities ↔ 하는 일, q_required ↔ 필수 역량, q_preferred ↔ 우대 역량.
      공고 본문에 적혀 있을 법한 문장으로 쓴다. 예) q_required="Kubernetes 기반 서비스 운영 경험"
    결과: similarity(컬럼별 유사도를 합친 점수) 내림차순, 질의한 컬럼의 sim_*도 포함.
      의미 검색 질의가 하나도 없으면 필터만 적용해 최근 확인 순으로 돌려준다(similarity=None).
    """
    started = time.perf_counter()
    limit = max(1, min(int(limit), 100))
    log.info("event=search_start title_keywords=%s company=%r category=%r location=%r max_years=%s active=%s deadline_days=%s deadline_sort=%s limit=%d",
             title_keywords, company_name, category, location, max_years, only_active, deadline_within_days, sort_by_deadline, limit)

    # 1) WHERE로 확정할 수 있는 조건부터 건다
    conditions, args = build_posting_filter_conditions(
        title_keywords, company_name, category, location, max_years, only_active,
        deadline_within_days, require_deadline=sort_by_deadline)

    # 2) 주어진 의미 검색 질의만 임베딩해서, 해당 컬럼과의 유사도 SELECT 식을 만든다
    field_queries = {"q_responsibilities": q_responsibilities, "q_required": q_required,
                     "q_preferred": q_preferred}
    asked_fields = [name for name, text in field_queries.items() if text and text.strip()]
    log.info("event=search_embedding_start fields=%s", asked_fields)
    query_vectors = await asyncio.gather(*(embed_query(field_queries[name]) for name in asked_fields))
    log.info("event=search_embedding_done elapsed_seconds=%.2f", time.perf_counter() - started)
    similarity_selects, similarity_keys = [], []
    for name, vector in zip(asked_fields, query_vectors):
        embedding_column, similarity_key = FIELD_QUERY_TO_EMBEDDING[name]
        args.append(to_pg(vector))
        similarity_selects.append(f"1 - (j.{embedding_column} <=> ${len(args)}::vector) AS {similarity_key}")
        similarity_keys.append(similarity_key)

    # 3) 필터를 통과한 공고 전부를 가져온다 (벡터 인덱스 없이 정확 비교: 근사 검색·후필터 누락이 없다)
    where = ("WHERE " + " AND ".join(conditions)) if conditions else ""
    select_columns = ", ".join(
        ["j.id", "c.name AS company", "c.industry", "j.title", "j.url", "j.location", "j.employment_type",
         "j.min_years", "j.category", "j.responsibilities", "j.required_qualifications",
         "j.preferred_qualifications", "j.deadline"] + similarity_selects)
    if similarity_keys:   # 유사도 점수는 아래 4)에서 계산하므로 SQL에서 자르지 않는다
        order_and_limit = ""
    elif sort_by_deadline:
        order_and_limit = f"ORDER BY j.deadline ASC, j.last_seen_at DESC LIMIT {limit}"
    else:
        order_and_limit = f"ORDER BY j.last_seen_at DESC LIMIT {limit}"
    log.info("event=search_sql_start filters=%s semantic_fields=%s", conditions, asked_fields)
    connection_pool = await pool()
    rows = [_row(r) for r in await connection_pool.fetch(
        f"SELECT {select_columns} FROM job_postings j JOIN companies c ON c.id = j.company_id "
        f"{where} {order_and_limit}", *args)]
    log.info("event=search_sql_done rows=%d elapsed_seconds=%.2f", len(rows), time.perf_counter() - started)
    if not rows:
        log.warning("event=search_empty reason=no_rows_after_sql_filters filters=%s", conditions)
    if not similarity_keys:
        return [{**row, "similarity": None} for row in rows]

    # 4) 컬럼별 유사도를 점수로 합쳐 높은 순으로 자른다 (점수가 None인 공고는 뺀다)
    scored_rows = []
    for row in rows:
        score = score_posting_by_field_similarity({key: row[key] for key in similarity_keys})
        if score is not None:
            scored_rows.append({**row, "similarity": score})
    scored_rows.sort(key=lambda row: row["similarity"], reverse=True)
    top_rows = scored_rows[:limit]

    # 5) 마감순 요청이면, 유사도로 고른 공고들을 마감이 가까운 순(같으면 유사도 높은 순)으로 다시 정렬한다
    #    deadline은 _row가 ISO 문자열(YYYY-MM-DD)로 바꿔 두었고 require_deadline으로 비어 있지 않다
    if sort_by_deadline:
        top_rows.sort(key=lambda row: (row["deadline"], -row["similarity"]))
    log.info("event=search_done sql_rows=%d scored_rows=%d returned=%d ids=%s elapsed_seconds=%.2f", len(rows), len(scored_rows), len(top_rows), [r["id"] for r in top_rows], time.perf_counter() - started)
    return top_rows


@mcp.tool()
async def get_job_posting(id: int) -> dict:
    """공고 1건 전체(하는 일/필수/우대 역량)와 회사·산업 정보."""
    connection_pool = await pool()
    row = await connection_pool.fetchrow(
        """SELECT j.id, c.name AS company, c.industry, j.title, j.url, j.location,
                  j.employment_type, j.min_years, j.category, j.responsibilities,
                  j.required_qualifications, j.preferred_qualifications, j.posted_at,
                  j.deadline, j.is_active
           FROM job_postings j JOIN companies c ON c.id = j.company_id WHERE j.id = $1""", id)
    if row is None:
        raise ValueError(f"공고 없음: id={id}")
    return _row(row)


@mcp.tool()
async def get_cached_research(company_name: str, target_company: str, max_age_days: int = 14) -> dict:
    """해당 회사·계열사의 가장 최근 리서치(max_age_days 이내). 없으면 빈 dict. topics의 회사 단위 토픽을 재사용한다.

    target_company: 실제 채용 계열사명 (그룹 포털이 아니면 company_name과 같아도 됨).
    """
    connection_pool = await pool()
    async with connection_pool.acquire() as conn:
        cid = await get_company_id_by_name(conn, company_name)
        row = await conn.fetchrow(
            """SELECT id, posting_id, target_company, topics::text AS topics, report_md,
                      created_at::text AS created_at
               FROM company_research
               WHERE company_id = $1 AND target_company = $2
                 AND created_at > now() - make_interval(days => $3)
               ORDER BY created_at DESC LIMIT 1""",
            cid, target_company, int(max_age_days))
    if row is None:
        return {}
    return {**dict(row), "topics": json.loads(row["topics"])}


@mcp.tool()
async def save_research(company_name: str, target_company: str, topics: list[dict], report_md: str,
                        posting_id: Optional[int] = None) -> dict:
    """리서치 결과 저장. topics: [{"topic", "summary", "sources": [{"title", "url"}]}]."""
    connection_pool = await pool()
    async with connection_pool.acquire() as conn:
        cid = await get_company_id_by_name(conn, company_name)
        rid = await conn.fetchval(
            """INSERT INTO company_research (company_id, target_company, posting_id, topics, report_md)
               VALUES ($1, $2, $3, $4::jsonb, $5) RETURNING id""",
            cid, target_company, posting_id, json.dumps(topics, ensure_ascii=False), report_md)
    return {"id": rid}


if __name__ == "__main__":
    configure_logging("job-query.log")
    log.info("event=job_query_ready")
    mcp.run()
