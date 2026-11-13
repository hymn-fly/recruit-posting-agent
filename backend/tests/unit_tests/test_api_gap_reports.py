"""공고를 바꿔도 세션에 저장된 역량 분석 결과를 다시 보여준다."""
import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

from api import main


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
        fetchrow=AsyncMock(side_effect=lambda query, posting_id: {"id": posting_id}),
        fetchval=AsyncMock(return_value=None),
    )
    monkeypatch.setattr(main.app.state, "graph", Graph(), raising=False)
    monkeypatch.setattr(main.db, "pool", AsyncMock(return_value=pool))
    monkeypatch.setitem(main.sessions, "test-gap-reports", asyncio.Lock())

    second = await main.select_posting("test-gap-reports", 2)
    first = await main.select_posting("test-gap-reports", 1)

    assert second["gap_report"] == "두 번째 공고 분석"
    assert first["gap_report"] == "첫 번째 공고 분석"


async def test_DB에_저장된_역량분석을_세션에_없는_공고_선택시_불러온다(monkeypatch):
    state = {"selected_posting": {"id": 1}, "gap_report": "", "gap_reports": {}}

    class Graph:
        async def aget_state(self, config):
            return SimpleNamespace(values=state)

        async def aupdate_state(self, config, update):
            state.update(update)

    async def fetchval(query, posting_id):
        return "DB에 저장된 분석" if "posting_gap_reports" in query else None

    pool = SimpleNamespace(fetchrow=AsyncMock(side_effect=lambda query, posting_id: {"id": posting_id}),
                           fetchval=AsyncMock(side_effect=fetchval))
    monkeypatch.setattr(main.app.state, "graph", Graph(), raising=False)
    monkeypatch.setattr(main.db, "pool", AsyncMock(return_value=pool))
    monkeypatch.setitem(main.sessions, "test-gap-db", asyncio.Lock())

    selected = await main.select_posting("test-gap-db", 7)

    assert selected["gap_report"] == "DB에 저장된 분석"


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
