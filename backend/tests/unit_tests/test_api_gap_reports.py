"""공고를 바꿔도 세션에 저장된 역량 분석 결과를 다시 보여준다."""
import asyncio
from datetime import date
from types import SimpleNamespace
from unittest.mock import AsyncMock

from api import main
from api.schemas import PostingWithSavedReports


def make_posting_database_row(posting_id: int, **reports) -> dict:
    return {
        "id": posting_id, "title": "AI 엔지니어", "company": "테스트 회사",
        "url": None, "location": None, "deadline": date(2026, 12, 31),
        "min_years": None, "industry": None, "responsibilities": None,
        "required_qualifications": None, "preferred_qualifications": None,
        "employment_type": None, "category": None, "posted_at": None, "is_active": True,
        "cached_research_report": None, "saved_gap_report": None, **reports,
    }


async def test_공고를_다시_선택하면_기존_역량분석을_보여준다(monkeypatch):
    state = {
        "selected_posting": {"id": 1},
        "gap_report": "첫 번째 공고 분석",
        "gap_posting_id": 1,
        "gap_reports": {"2": "두 번째 공고 분석"},
    }

    class Graph:
        async def aget_state(self, config):
            return SimpleNamespace(values=state)

        async def aupdate_state(self, config, update):
            state.update(update)

    pool = SimpleNamespace(
        fetchrow=AsyncMock(side_effect=lambda query, posting_id, need_research, need_gap:
                          make_posting_database_row(posting_id)),
    )
    monkeypatch.setattr(main.app.state, "graph", Graph(), raising=False)
    monkeypatch.setattr(main.db, "pool", AsyncMock(return_value=pool))
    monkeypatch.setitem(main.sessions, "test-gap-reports", asyncio.Lock())

    second = await main.select_posting("test-gap-reports", 2)
    first = await main.select_posting("test-gap-reports", 1)

    assert second["gap_report"] == "두 번째 공고 분석"
    assert first["gap_report"] == "첫 번째 공고 분석"
    # 세션 메모리에 이미 있는 역량 분석은 DB에서 다시 가져오지 않는다(need_gap=False)
    assert [call.args[3] for call in pool.fetchrow.await_args_list] == [False, False]


async def test_DB에_저장된_역량분석을_세션에_없는_공고_선택시_불러온다(monkeypatch):
    state = {"selected_posting": {"id": 1}, "gap_report": "", "gap_reports": {}}

    class Graph:
        async def aget_state(self, config):
            return SimpleNamespace(values=state)

        async def aupdate_state(self, config, update):
            state.update(update)

    pool = SimpleNamespace(fetchrow=AsyncMock(side_effect=lambda query, posting_id, need_research, need_gap:
        make_posting_database_row(posting_id, cached_research_report="저장된 기업 분석", saved_gap_report="DB에 저장된 분석")))
    monkeypatch.setattr(main.app.state, "graph", Graph(), raising=False)
    monkeypatch.setattr(main.db, "pool", AsyncMock(return_value=pool))
    monkeypatch.setitem(main.sessions, "test-gap-db", asyncio.Lock())

    selected = await main.select_posting("test-gap-db", 7)

    assert selected["gap_report"] == "DB에 저장된 분석"
    assert selected["research_report"] == "저장된 기업 분석"
    expected_posting = make_posting_database_row(7)
    expected_posting.pop("cached_research_report")
    expected_posting.pop("saved_gap_report")
    expected_posting["deadline"] = "2026-12-31"
    assert selected["selected_posting"] == expected_posting
    assert pool.fetchrow.await_count == 1              # 공고·기업 분석·역량 분석을 DB 1회 왕복으로 조회


async def test_posting_query_returns_model_or_none(monkeypatch):
    connection_pool = SimpleNamespace(fetchrow=AsyncMock(side_effect=[make_posting_database_row(7), None]))
    monkeypatch.setattr(main.db, "pool", AsyncMock(return_value=connection_pool))

    posting = await main.get_posting_with_saved_reports(7, False, False)

    assert isinstance(posting, PostingWithSavedReports)
    assert posting.id == 7
    assert posting.deadline == date(2026, 12, 31)
    assert posting.cached_research_report is None
    assert posting.saved_gap_report is None
    assert await main.get_posting_with_saved_reports(999, True, True) is None


async def test_새_역량분석은_공고_id로_upsert하고_없으면_저장하지_않는다(monkeypatch):
    state = {"gap_posting_id": 5, "gap_report": "새 분석"}

    class Graph:
        async def aget_state(self, config):
            return SimpleNamespace(values=state)

    pool = SimpleNamespace(execute=AsyncMock())
    monkeypatch.setattr(main.app.state, "graph", Graph(), raising=False)
    monkeypatch.setattr(main.db, "pool", AsyncMock(return_value=pool))
    monkeypatch.setitem(main.sessions, "test-gap-save", asyncio.Lock())

    await main.save_gap_report_of_session("test-gap-save")
    assert pool.execute.await_args.args[1:] == (5, "새 분석")

    pool.execute.reset_mock()
    state.update(gap_report="")
    await main.save_gap_report_of_session("test-gap-save")
    pool.execute.assert_not_awaited()
