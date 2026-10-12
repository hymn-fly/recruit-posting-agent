"""사용법: python agents/main_ingest.py [회사명 ...]  (인자 없으면 23개 전체)"""
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv

load_dotenv()

from agents.ingest_graph import make_graph  # noqa: E402
from agents.mcp_client import get_tools  # noqa: E402
from scrapers.companies import COMPANIES, COMPANY_BY_NAME  # noqa: E402


async def main(names: list[str]):
    targets = [COMPANY_BY_NAME[n] for n in names] if names else COMPANIES
    tools = await get_tools()
    graph = make_graph(tools)
    for c in targets:
        try:
            out = await graph.ainvoke({"company": c})
            print(f"[{c['name']}] {out.get('status')} - {out.get('detail')}")
        except Exception as e:  # 한 회사 실패가 전체를 막지 않도록
            print(f"[{c['name']}] 예외: {e!r}")
            try:
                await tools["record_scrape_run"].ainvoke(
                    {"company_name": c["name"], "status": "failed", "detail": repr(e)[:500]})
            except Exception:
                pass


if __name__ == "__main__":
    asyncio.run(main(sys.argv[1:]))
