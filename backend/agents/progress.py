"""진행 상황 이벤트: 그래프 노드가 "지금 하는 일"을 내보내고, SSE 엔드포인트가 그것을 받아 클라이언트로 보낸다.

노드는 `emit_progress(...)`만 부른다. 받는 쪽(큐)은 ContextVar `progress_sink`에 붙어 있어서,
요청마다 따로 만든 큐가 그 요청의 하위 태스크(중첩 ainvoke, Send 병렬 노드)에만 전달된다.
sink가 없으면(일반 ainvoke, 단위 테스트, CLI) 아무 일도 하지 않는다.
"""
import asyncio
from contextvars import ContextVar
from typing import AsyncIterator, Optional

progress_sink: ContextVar[Optional[asyncio.Queue]] = ContextVar("progress_sink", default=None)

STATUS_START = "start"
STATUS_DONE = "done"


def emit_progress(agent: str, step: str, status: str, message: str, **detail) -> None:
    """현재 요청의 진행 큐에 이벤트 하나를 넣는다. 큐가 없으면 무시한다.

    agent: 어느 Agent의 일인지(supervisor, job_search ...), step: 그 안의 단계 이름.
    같은 (agent, step)의 start와 done이 한 쌍으로 화면에서 "진행 중 → 완료"가 된다.
    """
    sink = progress_sink.get()
    if sink is None:
        return
    sink.put_nowait({"agent": agent, "step": step, "status": status, "message": message, "detail": detail or None})


async def iter_progress(work: asyncio.Task, queue: asyncio.Queue, heartbeat_seconds: float) -> AsyncIterator[Optional[dict]]:
    """work가 끝날 때까지 큐의 이벤트를 도착 즉시 내보낸다. heartbeat_seconds 동안 조용하면 None(하트비트)을 낸다.

    work가 끝나면 큐에 남은 이벤트를 모두 내보낸 뒤 종료한다. work의 예외는 여기서 다루지 않는다(호출한 쪽이 task.result()로 확인).
    """
    while True:
        next_event = asyncio.ensure_future(queue.get())
        finished, _ = await asyncio.wait({next_event, work}, timeout=heartbeat_seconds,
                                         return_when=asyncio.FIRST_COMPLETED)

        # 1) 이벤트가 도착했으면 바로 내보낸다
        if next_event in finished:
            yield next_event.result()
            continue

        # 2) 이벤트를 못 받았으니 대기용 get()은 거둔다 (큐에서 값을 가져가기 전이라 유실되지 않는다)
        next_event.cancel()

        # 3) 작업이 끝났으면 남은 이벤트를 비우고 끝낸다
        if work in finished:
            while not queue.empty():
                yield queue.get_nowait()
            return

        # 4) 둘 다 아니면 heartbeat_seconds 동안 조용했던 것이다
        yield None
