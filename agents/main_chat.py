"""이직 준비 Multi-Agent 대화 CLI. 사용법: uv run python agents/main_chat.py

명령: /select N (추천 목록 N번 공고 선택) · /id ID (공고 id로 선택) · /reset (대화 초기화) · /quit
"""
import asyncio
import logging
import sys
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parent.parent / ".env")

from langgraph.checkpoint.memory import MemorySaver  # noqa: E402

from agents.mcp_client import open_tools  # noqa: E402
from agents.query_graph import _parse  # noqa: E402
from agents.supervisor import make_graph, turn_input  # noqa: E402

logging.basicConfig(level=logging.WARNING, format="  [%(name)s] %(message)s")
logging.getLogger("agents.supervisor").setLevel(logging.INFO)


async def select(graph, config, tools, posting_id: int) -> str:
    items = _parse(await tools["get_job_posting"].ainvoke({"id": posting_id}))
    if not items:
        return f"공고 id={posting_id}를 찾지 못했습니다."
    await graph.aupdate_state(config, {"selected_posting": items[0]})
    return f"선택: [{items[0]['company']}] {items[0]['title']} (id={posting_id})"


async def main():
    async with open_tools() as tools:
        graph = make_graph(tools, checkpointer=MemorySaver())
        config = {"configurable": {"thread_id": str(uuid.uuid4())}}
        print(__doc__)
        while True:
            try:
                text = (await asyncio.to_thread(input, "\n> ")).strip()
            except (EOFError, KeyboardInterrupt):
                break
            if not text:
                continue
            if text in ("/quit", "/exit"):
                break
            if text == "/reset":
                config = {"configurable": {"thread_id": str(uuid.uuid4())}}
                print("대화를 초기화했습니다.")
                continue
            if text.startswith(("/select ", "/id ")):
                cmd, _, arg = text.partition(" ")
                if not arg.strip().isdigit():
                    print("숫자를 입력하세요. 예: /select 2")
                    continue
                n = int(arg)
                if cmd == "/select":
                    cands = (await graph.aget_state(config)).values.get("candidates") or []
                    if not 1 <= n <= len(cands):
                        print(f"추천 목록에 {n}번이 없습니다.")
                        continue
                    n = cands[n - 1]["id"]
                print(await select(graph, config, tools, n))
                continue
            out = await graph.ainvoke(turn_input(text), config)
            print("\n" + out["messages"][-1].content)


if __name__ == "__main__":
    asyncio.run(main())
