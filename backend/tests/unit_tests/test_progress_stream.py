"""진행 이벤트(agents/progress.py)와 SSE 엔드포인트(POST /sessions/{sid}/messages/stream) 단위 테스트."""
import asyncio
import json
from types import SimpleNamespace

import httpx
import pytest

from agents.progress import STATUS_DONE, STATUS_START, emit_progress, iter_progress, progress_sink
from api import main


def parse_server_sent_events(body: str) -> list[tuple[str, dict]]:
    """SSE 본문을 [(event, data)]로 바꾼다. `:`로 시작하는 주석 줄(하트비트)은 건너뛴다."""
    events = []
    for block in body.strip().split("\n\n"):
        lines = [line for line in block.split("\n") if not line.startswith(":")]
        if not lines:
            continue
        name = next(line[len("event: "):] for line in lines if line.startswith("event: "))
        data = next(line[len("data: "):] for line in lines if line.startswith("data: "))
        events.append((name, json.loads(data)))
    return events


# ---------------------------------------------------------------
# emit_progress / iter_progress
# ---------------------------------------------------------------
def test_sink가_없으면_emit_progress는_아무_일도_하지_않는다():
    assert progress_sink.get() is None
    emit_progress("job_search", "plan", STATUS_START, "검색 조건을 만드는 중")   # 예외 없이 지나간다


async def test_중첩된_태스크와_병렬_태스크의_이벤트가_그_요청의_큐로_모인다():
    queue = asyncio.Queue()
    progress_sink.set(queue)

    async def parallel_node(name):
        emit_progress("company_research", f"search_topic:{name}", STATUS_START, f"{name} 조사 중")
        await asyncio.sleep(0)
        emit_progress("company_research", f"search_topic:{name}", STATUS_DONE, f"{name} 완료")

    async def outer_work():
        emit_progress("supervisor", "route:0", STATUS_START, "요청 분석")
        await asyncio.gather(*(parallel_node(name) for name in ("산업", "경쟁사")))   # gather가 만든 태스크도 같은 큐를 본다

    work = asyncio.create_task(outer_work())
    received = [event async for event in iter_progress(work, queue, heartbeat_seconds=1) if event]

    assert [(e["step"], e["status"]) for e in received][0] == ("route:0", STATUS_START)
    assert {e["step"] for e in received} == {"route:0", "search_topic:산업", "search_topic:경쟁사"}
    assert len(received) == 5


async def test_조용하면_None으로_하트비트를_내고_작업이_끝나면_남은_이벤트를_모두_내보낸다():
    queue = asyncio.Queue()

    async def slow_work():
        await asyncio.sleep(0.25)
        queue.put_nowait({"message": "마지막"})
        return "끝"

    work = asyncio.create_task(slow_work())
    received = [event async for event in iter_progress(work, queue, heartbeat_seconds=0.1)]

    assert received.count(None) >= 1                 # 조용한 동안 하트비트
    assert received[-1] == {"message": "마지막"}     # 작업이 끝난 직후 큐에 남은 이벤트도 유실되지 않음
    assert work.result() == "끝"


# ---------------------------------------------------------------
# SSE 엔드포인트
# ---------------------------------------------------------------
class FakeSupervisorGraph:
    """ainvoke 중에 진행 이벤트를 내고, aget_state로 snapshot 값을 돌려준다."""

    def __init__(self, answer_by_question=None, fail=False):
        self.answer_by_question = answer_by_question or {}
        self.fail = fail
        self.state_by_thread: dict[str, dict] = {}

    async def ainvoke(self, agent_input, config):
        question = agent_input["messages"][0].content
        emit_progress("supervisor", "route:0", STATUS_START, f"{question} 분석")
        await asyncio.sleep(0.05)
        if self.fail:
            raise RuntimeError("boom")
        emit_progress("supervisor", "route:0", STATUS_DONE, f"{question} 완료")
        self.state_by_thread[config["configurable"]["thread_id"]] = {"messages": [], "candidates": [], "answer": question}

    async def aget_state(self, config):
        return SimpleNamespace(values=self.state_by_thread.get(config["configurable"]["thread_id"], {}))


@pytest.fixture
def stream_client(monkeypatch):
    def make(graph):
        monkeypatch.setattr(main.app.state, "graph", graph, raising=False)
        monkeypatch.setattr(main, "save_gap_report_of_session", lambda sid: asyncio.sleep(0))
        return httpx.AsyncClient(transport=httpx.ASGITransport(app=main.app), base_url="http://test")
    return make


async def test_스트림은_step들을_순서대로_보내고_done으로_snapshot을_보낸다(stream_client, monkeypatch):
    monkeypatch.setitem(main.sessions, "s-ok", asyncio.Lock())
    async with stream_client(FakeSupervisorGraph()) as client:
        response = await client.post("/sessions/s-ok/messages/stream", json={"text": "공고 찾아줘"})

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
    events = parse_server_sent_events(response.text)
    assert [name for name, _ in events] == ["step", "step", "done"]
    assert events[0][1]["message"] == "공고 찾아줘 분석" and events[0][1]["status"] == STATUS_START
    assert events[1][1]["status"] == STATUS_DONE
    assert events[2][1]["session_id"] == "s-ok"   # done은 기존 /messages 응답과 같은 snapshot


async def test_그래프가_실패하면_error_이벤트로_알린다(stream_client, monkeypatch):
    monkeypatch.setitem(main.sessions, "s-fail", asyncio.Lock())
    async with stream_client(FakeSupervisorGraph(fail=True)) as client:
        response = await client.post("/sessions/s-fail/messages/stream", json={"text": "실패할 요청"})

    events = parse_server_sent_events(response.text)
    assert events[-1][0] == "error" and "처리에 실패" in events[-1][1]["message"]
    assert "done" not in [name for name, _ in events]


async def test_없는_세션은_스트림을_열기_전에_404이다(stream_client):
    async with stream_client(FakeSupervisorGraph()) as client:
        response = await client.post("/sessions/없는-세션/messages/stream", json={"text": "안녕"})

    assert response.status_code == 404


async def test_동시에_온_두_세션의_이벤트는_섞이지_않는다(stream_client, monkeypatch):
    monkeypatch.setitem(main.sessions, "s-a", asyncio.Lock())
    monkeypatch.setitem(main.sessions, "s-b", asyncio.Lock())
    async with stream_client(FakeSupervisorGraph()) as client:
        response_a, response_b = await asyncio.gather(
            client.post("/sessions/s-a/messages/stream", json={"text": "요청A"}),
            client.post("/sessions/s-b/messages/stream", json={"text": "요청B"}))

    messages_a = [data["message"] for name, data in parse_server_sent_events(response_a.text) if name == "step"]
    messages_b = [data["message"] for name, data in parse_server_sent_events(response_b.text) if name == "step"]
    assert messages_a == ["요청A 분석", "요청A 완료"]
    assert messages_b == ["요청B 분석", "요청B 완료"]


async def test_같은_세션에_요청이_겹치면_대기_이벤트를_먼저_보낸다(stream_client, monkeypatch):
    session_lock = asyncio.Lock()
    monkeypatch.setitem(main.sessions, "s-busy", session_lock)
    async with stream_client(FakeSupervisorGraph()) as client:
        await session_lock.acquire()                      # 앞선 요청이 처리 중인 상태를 흉내 낸다
        pending = asyncio.create_task(client.post("/sessions/s-busy/messages/stream", json={"text": "뒤따르는 요청"}))
        await asyncio.sleep(0.05)
        session_lock.release()
        response = await pending

    events = parse_server_sent_events(response.text)
    assert events[0][1]["step"] == "queued"
    assert [name for name, _ in events][-1] == "done"


# ---------------------------------------------------------------
# 노드 계측
# ---------------------------------------------------------------
async def test_job_search_그래프는_단계마다_start_done_이벤트를_낸다(fake_llm, make_tools, make_posting):
    from agents.graphs import job_search_graph
    from agents.state import JobSearchPlan

    fake_llm.set(JobSearchPlan, JobSearchPlan(title_keywords=["LLM"]))
    tools = make_tools(semantic_search_jobs=[make_posting(id=i) for i in (1, 2, 3)])
    queue = asyncio.Queue()
    progress_sink.set(queue)

    await job_search_graph.make_graph(tools).ainvoke({"question": "LLM 공고"})

    events = []
    while not queue.empty():
        events.append(queue.get_nowait())
    assert [(e["step"], e["status"]) for e in events] == [
        ("plan", STATUS_START), ("plan", STATUS_DONE), ("retrieve", STATUS_START), ("retrieve", STATUS_DONE)]
    assert events[-1]["message"] == "공고 3건 검색됨" and events[-1]["detail"] == {"result_count": 3}
