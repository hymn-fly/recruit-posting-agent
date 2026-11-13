"""Supervisor 스트림 확인용 CLI.

저장소 루트에서 실행:
    PYTHONPATH=backend uv run python -m agents.stream_supervisor "MLOps 공고 찾아줘"

질문을 생략하면 대화형으로 실행한다. 같은 프로세스의 질문들은 같은 thread_id를 쓴다.
"""

import argparse
import asyncio
from pathlib import Path
from pprint import pprint
from uuid import uuid4

from dotenv import load_dotenv
from langgraph.checkpoint.memory import InMemorySaver

from common.observability import configure_logging
from agents.graphs.supervisor import input_to_agent_state, make_graph
from agents.mcp_client import open_tools


async def run(question: str | None, thread_id: str) -> None:
    load_dotenv(Path(__file__).resolve().parents[2] / ".env")
    configure_logging("stream_supervisor.log")
    config = {"configurable": {"thread_id": thread_id}}

    async with open_tools() as tools:
        graph = make_graph(tools, checkpointer=InMemorySaver())
        print(f"thread_id: {thread_id}", flush=True)

        while True:
            if question is None:
                try:
                    current_question = input("\n질문 (종료: Enter): ").strip()
                except EOFError:
                    break
                if not current_question:
                    break
            else:
                current_question = question

            async for event in graph.astream(
                input_to_agent_state(current_question),
                config=config,
                stream_mode=["updates", "checkpoints"],
                version="v2",
            ):
                print(f"\n[{event['type']}] namespace={event['ns']}", flush=True)
                pprint(event["data"], sort_dicts=False)

            if question is not None:
                break


def main() -> None:
    parser = argparse.ArgumentParser(description="Supervisor 그래프의 노드 업데이트와 체크포인트 출력")
    parser.add_argument("question", nargs="?", help="한 번 실행할 질문. 생략하면 대화형 모드")
    parser.add_argument("--thread-id", default=None, help="이번 프로세스에서 사용할 thread_id")
    args = parser.parse_args()
    asyncio.run(run(args.question, args.thread_id or uuid4().hex))


if __name__ == "__main__":
    main()
