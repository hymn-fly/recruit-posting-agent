"""Gemini 임베딩 공용 모듈: MCP 서버와 스크립트가 같이 쓴다.

pgvector에는 '[0.1,0.2,...]' 텍스트로 넘기고 SQL에서 `$n::vector`로 캐스팅한다 (numpy 의존 없음).
"""
import asyncio
import hashlib
import logging
from typing import Optional

from langchain_google_genai import GoogleGenerativeAIEmbeddings

log = logging.getLogger("mcp_server.embeddings")

MODEL = "gemini-embedding-001"   # GOOGLE_API_KEY 필요
DIM = 768                         # db/schema.sql의 vector(768)과 같아야 한다
BATCH = 80                        # 한 번에 보낼 텍스트 수 (무료 티어: 분당 100건)
RATE_LIMIT_WAIT = 60              # 초. 429(RESOURCE_EXHAUSTED)일 때 대기
RATE_LIMIT_RETRIES = 3

_emb: Optional[GoogleGenerativeAIEmbeddings] = None


def _client() -> GoogleGenerativeAIEmbeddings:
    global _emb
    if _emb is None:
        _emb = GoogleGenerativeAIEmbeddings(model=MODEL)
    return _emb


async def embed_documents(texts: list[str]) -> list[list[float]]:
    """저장할 문서(공고·이력서 청크) 임베딩. 무료 티어 한도(분당 100건) 때문에 나눠 보내고 429면 기다렸다 재시도."""
    out: list[list[float]] = []
    for i in range(0, len(texts), BATCH):
        batch = texts[i:i + BATCH]
        for attempt in range(RATE_LIMIT_RETRIES + 1):
            try:
                out += await _client().aembed_documents(
                    batch, task_type="RETRIEVAL_DOCUMENT", output_dimensionality=DIM)
                break
            except Exception as e:
                if "RESOURCE_EXHAUSTED" not in str(e) or attempt == RATE_LIMIT_RETRIES:
                    raise
                log.warning("임베딩 한도 초과 → %d초 대기 후 재시도 (%d/%d건 완료)",
                            RATE_LIMIT_WAIT, len(out), len(texts))
                await asyncio.sleep(RATE_LIMIT_WAIT)
    return out


async def embed_query(text: str) -> list[float]:
    """검색 질의 임베딩."""
    return await _client().aembed_query(
        text, task_type="RETRIEVAL_QUERY", output_dimensionality=DIM)


def to_pg(vec: list[float]) -> str:
    return "[" + ",".join(f"{x:.6f}" for x in vec) + "]"


def text_hash(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()
