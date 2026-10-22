"""사용법: python agents/example_gemma.py ["질문"]

ChatGoogleGenerativeAI로 gemma-4-31b-it 를 직접 호출해 모델이 정상 동작하는지 확인한다. (GOOGLE_API_KEY 필요)
"""
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv

load_dotenv()

from langchain_google_genai import ChatGoogleGenerativeAI  # noqa: E402

MODEL = "gemma-4-31b-it"
DEFAULT_QUESTION = "한 문장으로 자기소개 해줘."


def call_gemma_with_question(question: str):
    """gemma-4-31b-it에 질문 하나를 보내고 (응답 메시지, 걸린 초)를 돌려준다."""
    chat = ChatGoogleGenerativeAI(model=MODEL, thinking_level="minimal")
    start = time.perf_counter()
    response = chat.invoke(question)
    return response, time.perf_counter() - start


if __name__ == "__main__":
    question = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_QUESTION
    response, elapsed = call_gemma_with_question(question)
    print(f"[model] {MODEL}  [time] {elapsed:.1f}s  [usage] {response.usage_metadata}")
    print(f"[answer] {response.text}")
