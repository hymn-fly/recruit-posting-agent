"""Gemini 임베딩 공용 모듈: MCP 서버와 스크립트가 같이 쓴다.

pgvector에는 '[0.1,0.2,...]' 텍스트로 넘기고 SQL에서 `$n::vector`로 캐스팅한다 (numpy 의존 없음).
"""
import hashlib
from typing import Optional

from langchain_google_genai import GoogleGenerativeAIEmbeddings

MODEL = "gemini-embedding-001"   # GOOGLE_API_KEY 필요
DIM = 768                         # db/schema.sql의 vector(768)과 같아야 한다

_emb: Optional[GoogleGenerativeAIEmbeddings] = None


def _client() -> GoogleGenerativeAIEmbeddings:
    global _emb
    if _emb is None:
        _emb = GoogleGenerativeAIEmbeddings(model=MODEL)
    return _emb


async def embed_documents(texts: list[str]) -> list[list[float]]:
    """저장할 문서(공고·이력서 청크) 임베딩."""
    if not texts:
        return []
    return await _client().aembed_documents(
        texts, task_type="RETRIEVAL_DOCUMENT", output_dimensionality=DIM)


async def embed_query(text: str) -> list[float]:
    """검색 질의 임베딩."""
    return await _client().aembed_query(
        text, task_type="RETRIEVAL_QUERY", output_dimensionality=DIM)


def to_pg(vec: list[float]) -> str:
    return "[" + ",".join(f"{x:.6f}" for x in vec) + "]"


def text_hash(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()
