"""OpenAI 임베딩 공용 모듈: MCP 서버와 스크립트가 같이 쓴다.

pgvector에는 '[0.1,0.2,...]' 텍스트로 넘기고 SQL에서 `$n::vector`로 캐스팅한다 (numpy 의존 없음).
"""
import hashlib
import logging
from typing import Optional

from langchain_openai import OpenAIEmbeddings

log = logging.getLogger("mcp_server.embeddings")

MODEL = "text-embedding-3-small"   # OPENAI_API_KEY 필요 (ChatGPT Plus와 별개인 API 결제)
DIM = 768                          # db/schema.sql의 vector(768)과 같아야 한다. text-embedding-3의 dimensions 옵션으로 줄여 받는다
BATCH = 100                        # 한 번에 보낼 텍스트 수
REQUEST_TIMEOUT = 30               # 초. 응답이 없으면 이 시간 뒤 실패한다
MAX_RETRIES = 3                    # 일시적인 429·5xx는 SDK가 짧게 백오프하며 재시도한다. 한도 소진(insufficient_quota)은 몇 초 안에 실패로 끝난다

_emb: Optional[OpenAIEmbeddings] = None


def _client() -> OpenAIEmbeddings:
    global _emb
    if _emb is None:
        _emb = OpenAIEmbeddings(model=MODEL, dimensions=DIM, timeout=REQUEST_TIMEOUT, max_retries=MAX_RETRIES)
    return _emb


async def embed_documents(texts: list[str]) -> list[list[float]]:
    """저장할 문서(공고·이력서 청크) 임베딩. BATCH개씩 나눠 보낸다."""
    embedded_vectors: list[list[float]] = []
    for start in range(0, len(texts), BATCH):
        embedded_vectors += await _client().aembed_documents(texts[start:start + BATCH])
    return embedded_vectors


async def embed_query(text: str) -> list[float]:
    """검색 질의 임베딩. OpenAI는 문서/질의 구분(task_type)이 없어 같은 방식으로 임베딩한다."""
    return await _client().aembed_query(text)


def to_pg(vec: list[float]) -> str:
    return "[" + ",".join(f"{x:.6f}" for x in vec) + "]"


def text_hash(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()
