"""mcp_server.query_server.semantic_search_jobs의 정렬 규칙 단위 테스트. DB·임베딩 API는 가짜로 바꾼다.

마감순(sort_by_deadline)과 의미 검색(q_*)을 함께 주면: 유사도 상위 limit건을 고르고, 그 안에서 마감이 가까운 순으로 정렬한다.
"""
from datetime import date

import pytest

from mcp_server import query_server


class FakePool:
    """fetch에 넘어온 SQL을 기록하고, 미리 정한 행을 돌려준다."""

    def __init__(self, rows: list[dict]):
        self.rows = rows
        self.sql_calls: list[str] = []

    async def fetch(self, sql: str, *args):
        self.sql_calls.append(sql)
        return self.rows


def make_row(posting_id: int, deadline: date, sim_required: float) -> dict:
    return {"id": posting_id, "company": f"회사{posting_id}", "industry": None, "title": f"공고{posting_id}",
            "url": f"https://example.com/{posting_id}", "location": "서울", "employment_type": None,
            "min_years": None, "category": "AI Engineer", "responsibilities": None,
            "required_qualifications": "Kubernetes", "preferred_qualifications": None,
            "deadline": deadline, "sim_required": sim_required}


@pytest.fixture
def fake_search(monkeypatch):
    """유사도와 마감일이 엇갈리는 4건: 유사도가 높다고 마감이 가까운 것이 아니다."""
    rows = [make_row(1, date(2026, 11, 30), 0.90),   # 유사도 1등, 마감은 가장 늦음
            make_row(2, date(2026, 11, 12), 0.80),   # 유사도 2등, 마감 가장 이름
            make_row(3, date(2026, 11, 20), 0.70),   # 유사도 3등
            make_row(4, date(2026, 11, 10), 0.10)]   # 마감은 가장 이르지만 자격요건이 안 맞음
    fake_pool = FakePool(rows)

    async def fake_pool_factory():
        return fake_pool

    async def fake_embed_query(text: str) -> list[float]:
        return [0.0] * 3

    monkeypatch.setattr(query_server, "pool", fake_pool_factory)
    monkeypatch.setattr(query_server, "embed_query", fake_embed_query)
    return fake_pool


async def test_마감순과_유사도를_함께_주면_유사도로_고르고_마감순으로_정렬한다(fake_search):
    results = await query_server.semantic_search_jobs(
        q_required="Kubernetes 운영 경험", sort_by_deadline=True, limit=3)

    # 유사도 상위 3건(1, 2, 3)을 고르고 → 마감이 가까운 순(2: 11-12, 3: 11-20, 1: 11-30)
    assert [row["id"] for row in results] == [2, 3, 1]
    assert all(row["similarity"] is not None for row in results)   # 유사도 점수가 함께 나간다
    # 유사도를 파이썬에서 계산해야 하므로 SQL에서는 자르지 않는다
    assert "LIMIT" not in fake_search.sql_calls[0]
    assert "j.deadline IS NOT NULL" in fake_search.sql_calls[0]


async def test_마감순만_주면_SQL에서_마감순으로_자르고_유사도는_없다(fake_search):
    results = await query_server.semantic_search_jobs(sort_by_deadline=True, limit=3)

    assert "ORDER BY j.deadline ASC" in fake_search.sql_calls[0] and "LIMIT 3" in fake_search.sql_calls[0]
    assert all(row["similarity"] is None for row in results)


async def test_마감순이_아니면_유사도_높은_순이다(fake_search):
    results = await query_server.semantic_search_jobs(q_required="Kubernetes 운영 경험", limit=3)

    assert [row["id"] for row in results] == [1, 2, 3]
