"""중복 제거 후에도 입력·저장·최종 전송 경계의 마스킹을 검증한다."""
import asyncio
import logging
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import httpx
import pytest

from agents import llm
from agents.guardrails.policy import GuardrailBoundary
from agents.guardrails.validation import redact_sensitive_text
from api import main
from scripts import ingest_profile


@pytest.mark.parametrize("suffix", ["", "/stream"])
async def test_chat_redacts_once_before_graph_execution(monkeypatch, suffix, caplog):
    caplog.set_level(logging.INFO)
    graph = SimpleNamespace(ainvoke=AsyncMock())
    redactor = Mock(wraps=redact_sensitive_text)
    monkeypatch.setattr(main.app.state, "graph", graph, raising=False)
    monkeypatch.setattr(main, "redact_sensitive_text", redactor)
    monkeypatch.setattr(main, "save_gap_report_of_session", AsyncMock())
    monkeypatch.setattr(main, "snapshot", AsyncMock(return_value={
        "candidates": [], "messages": [], "selected_posting": None}))
    monkeypatch.setitem(main.sessions, "redaction", asyncio.Lock())

    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=main.app),
                                 base_url="http://test") as client:
        response = await client.post(f"/sessions/redaction/messages{suffix}",
                                     json={"text": "연락처 person@example.com"})

    assert response.status_code == 200
    redactor.assert_called_once_with("연락처 person@example.com", GuardrailBoundary.CHAT_INPUT)
    assert graph.ainvoke.call_args.args[0]["messages"][0].content == "연락처 [REDACTED_PERSONAL_DATA]"
    assert sum("event=guardrail_decision" in record.message for record in caplog.records) == 1


@pytest.mark.parametrize("boundary", list(GuardrailBoundary))
@pytest.mark.parametrize("text, action, reason", [
    ("개발자", "allow", "ok"),
    ("person@example.com", "redact", "personal_data_detected"),
    ("password=private-value", "redact", "secret_detected"),
])
def test_redaction_logs_decision_once_without_input(caplog, boundary, text, action, reason):
    with caplog.at_level(logging.INFO):
        _, decision = redact_sensitive_text(text, boundary)

    assert len(caplog.records) == 1
    record = caplog.records[0]
    assert record.name == "agents.guardrails.validation"
    assert record.message == (
        f"event=guardrail_decision boundary={boundary} action={action} "
        f"reason={reason} policy={decision.policy_version}")
    assert text not in record.message


async def test_upload_passes_original_file_to_ingest(monkeypatch):
    async def ingest_uploaded_file(path, kind):
        assert path.name == "resume.txt"
        assert path.read_bytes() == b"person@example.com"
        assert kind == "resume"
        return "저장 완료"

    ingest_document = AsyncMock(side_effect=ingest_uploaded_file)
    monkeypatch.setattr(main, "ingest", ingest_document)
    monkeypatch.setattr(main, "summarize", AsyncMock())
    monkeypatch.setattr(main.db, "pool", AsyncMock(return_value=SimpleNamespace(execute=AsyncMock())))
    monkeypatch.setattr(main, "sessions", {})
    redactor = Mock(wraps=redact_sensitive_text)
    monkeypatch.setattr(main, "redact_sensitive_text", redactor)

    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=main.app),
                                 base_url="http://test") as client:
        response = await client.post("/documents", data={"kind": "resume"},
                                     files={"file": ("resume.txt", b"person@example.com", "text/plain")})

    assert response.status_code == 200
    ingest_document.assert_awaited_once()
    assert ingest_document.call_args.kwargs == {}
    redactor.assert_not_called()


@pytest.mark.parametrize("content, detail", [
    (b" \n ", "추출할 텍스트가 없습니다."),
    (b"\xff", "문서를 읽지 못했습니다."),
])
async def test_upload_returns_422_for_unusable_text(monkeypatch, content, detail):
    summarize_profile = AsyncMock()
    connection_pool = AsyncMock()
    monkeypatch.setattr(main, "summarize", summarize_profile)
    monkeypatch.setattr(ingest_profile.db, "pool", connection_pool)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=main.app),
                                 base_url="http://test") as client:
        response = await client.post("/documents", data={"kind": "resume"},
                                     files={"file": ("resume.txt", content, "text/plain")})

    assert response.status_code == 422
    assert response.json()["detail"].startswith(detail)
    connection_pool.assert_not_awaited()
    summarize_profile.assert_not_awaited()


async def test_ingest_extracts_once_and_masks_before_hashing(monkeypatch, tmp_path):
    path = tmp_path / "resume.txt"
    path.write_text("person@example.com", encoding="utf-8")
    sanitized_text = "[REDACTED_PERSONAL_DATA]"
    content_hash = ingest_profile.text_hash(sanitized_text)
    connection_pool = SimpleNamespace(fetchrow=AsyncMock(return_value={"id": 1, "content_hash": content_hash}))
    monkeypatch.setattr(ingest_profile.db, "pool", AsyncMock(return_value=connection_pool))
    hash_text = Mock(wraps=ingest_profile.text_hash)
    redactor = Mock(wraps=redact_sensitive_text)
    monkeypatch.setattr(ingest_profile, "text_hash", hash_text)
    monkeypatch.setattr(ingest_profile, "redact_sensitive_text", redactor)
    extract_document_text = Mock(wraps=ingest_profile.extract_text)
    monkeypatch.setattr(ingest_profile, "extract_text", extract_document_text)

    result = await ingest_profile.ingest(path, "resume")

    assert "변경 없음" in result
    extract_document_text.assert_called_once_with(path)
    redactor.assert_called_once_with("person@example.com", GuardrailBoundary.PROFILE_DOCUMENT)
    hash_text.assert_called_once_with(sanitized_text)


async def test_summary_masks_final_prompt_once_in_llm_wrapper(monkeypatch):
    connection_pool = SimpleNamespace(fetch=AsyncMock(return_value=[{
        "kind": "resume", "filename": "person@example.com.txt", "raw_text": "010-1234-5678"}]),
        execute=AsyncMock())
    monkeypatch.setattr(ingest_profile.db, "pool", AsyncMock(return_value=connection_pool))
    summary_model = SimpleNamespace(model_dump=lambda: {"headline": "개발자"})
    model = object.__new__(llm.LLM)
    model.schema_name = "ProfileSummary"
    model.primary_name = "test"
    model._invoke_impl = AsyncMock(return_value=summary_model)
    monkeypatch.setattr(ingest_profile, "LLM", Mock(return_value=model))
    redactor = Mock(wraps=redact_sensitive_text)
    monkeypatch.setattr(ingest_profile, "redact_sensitive_text", redactor)
    monkeypatch.setattr(llm, "redact_sensitive_text", redactor)

    assert await ingest_profile.summarize() == {"headline": "개발자"}

    assert redactor.call_count == 1
    assert redactor.call_args.args[1] == GuardrailBoundary.LLM_EGRESS
    sent_prompt = model._invoke_impl.call_args.args[0]
    assert "person@example.com" not in sent_prompt and "010-1234-5678" not in sent_prompt
    assert sent_prompt.count("[REDACTED_PERSONAL_DATA]") == 2
    connection_pool.execute.assert_awaited_once()
