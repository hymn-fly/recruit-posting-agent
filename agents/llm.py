"""LLM 공용 인터페이스: 모델명만으로 Gemma(Gemini API) / OpenAI를 바꿔 쓴다.

- `make_chat(model)`: 모델명 접두사로 제공자를 고른다 (gemma*/gemini* -> Google, gpt*/o* -> OpenAI).
- `LLM`: 기본(Gemma)이 PRIMARY_TIMEOUT초 안에 응답하지 못하거나 에러가 나면 OpenAI 모델로 자동 전환한다.
"""
import asyncio
import logging
import time

from langchain_core.language_models.chat_models import BaseChatModel
from langchain_google_genai import ChatGoogleGenerativeAI
from langchain_openai import ChatOpenAI

log = logging.getLogger("agents.llm")

PRIMARY_MODEL = "gemma-4-31b-it"   # GOOGLE_API_KEY 필요
FALLBACK_MODEL = "gpt-6-luna"      # OPENAI_API_KEY 필요
PRIMARY_TIMEOUT = 10               # 초. 이 시간 안에 응답이 없으면 FALLBACK_MODEL로 전환
FALLBACK_RETRIES = 3
PRIMARY_COOLDOWN = 300             # 초. 기본 모델이 실패하면 이 시간 동안 건너뛰고 바로 폴백(서킷 브레이커)

_primary_down_until: dict[str, float] = {}   # 모델명 -> 다시 시도할 시각 (프로세스 내 모든 LLM 인스턴스 공유)


def make_chat(model: str, max_output_tokens: int | None = None) -> BaseChatModel:
    """모델명만 바꿔서 제공자별 chat 모델을 만든다."""
    if model.startswith(("gemma", "gemini")):
        # thinking 최소화: 생각 블록을 없애 응답을 짧게 한다
        return ChatGoogleGenerativeAI(
            model=model, thinking_level="minimal", max_output_tokens=max_output_tokens)
    if model.startswith(("gpt", "o1", "o3", "o4")):
        return ChatOpenAI(model=model, max_tokens=max_output_tokens)
    raise ValueError(f"지원하지 않는 모델명: {model}")


class LLM:
    """`ainvoke(prompt)` 하나로 쓰는 래퍼. schema를 주면 구조화 출력(pydantic)을 돌려준다."""

    def __init__(self, max_output_tokens: int | None = None, schema=None,
                 primary: str = PRIMARY_MODEL, fallback: str = FALLBACK_MODEL,
                 primary_timeout: float = PRIMARY_TIMEOUT):
        self.primary_name, self.fallback_name = primary, fallback
        self.primary_timeout = primary_timeout

        self._primary = make_chat(primary, max_output_tokens)
        self._fallback = make_chat(fallback, max_output_tokens)
        
        if schema is not None:
            self._primary = self._primary.with_structured_output(schema)
            self._fallback = self._fallback.with_structured_output(schema)

    async def ainvoke(self, prompt: str):
        if time.monotonic() >= _primary_down_until.get(self.primary_name, 0):
            try:
                return await asyncio.wait_for(self._primary.ainvoke(prompt), self.primary_timeout)
            except Exception as e:  # 타임아웃(10초 초과) 포함, 기본 모델 실패 시 전환
                reason = f"{self.primary_timeout}초 초과" if isinstance(e, TimeoutError) else repr(e)[:120]
                log.warning("%s 실패(%s) -> %s로 전환, %d초간 건너뜀",
                            self.primary_name, reason, self.fallback_name, PRIMARY_COOLDOWN)
                _primary_down_until[self.primary_name] = time.monotonic() + PRIMARY_COOLDOWN
        for i in range(FALLBACK_RETRIES):
            try:
                return await self._fallback.ainvoke(prompt)
            except Exception:
                if i == FALLBACK_RETRIES - 1:
                    raise
                await asyncio.sleep(2 ** i)
