"""사용법: python agents/main_query.py "질문" [프로필 요약]"""
import asyncio
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv

load_dotenv()

# 다른 라이브러리 로그는 WARNING 이상만, 질의 그래프는 파라미터·결과까지(INFO) 본다
logging.basicConfig(level=logging.WARNING, format="  [%(name)s] %(message)s")
logging.getLogger("agents.query_graph").setLevel(logging.INFO)

from agents.mcp_client import get_tools  # noqa: E402
from agents.query_graph import make_graph  # noqa: E402


async def main(question: str, profile: str = ""):
    tools = await get_tools()
    out = await make_graph(tools).ainvoke({"question": question, "profile": profile})
    print(out["answer"])


if __name__ == "__main__":
    if len(sys.argv) < 2:
        sys.exit(__doc__)
    asyncio.run(main(sys.argv[1], sys.argv[2] if len(sys.argv) > 2 else ""))
