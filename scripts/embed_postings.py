"""임베딩이 없거나 본문이 바뀐 공고를 임베딩한다 (기존 행 백필용). 사용법: uv run python scripts/embed_postings.py"""
import asyncio
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from mcp_server import server  # noqa: E402

logging.basicConfig(level=logging.WARNING)


async def main():
    total = 0
    while True:
        res = await server.embed_pending(25)   # 공고당 최대 4개 텍스트 → 약 100건
        total += res["embedded"]
        print(res)
        if not res["remaining"]:
            break
    print(f"총 {total}건 임베딩")


if __name__ == "__main__":
    asyncio.run(main())
