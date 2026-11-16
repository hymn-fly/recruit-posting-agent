"""사용자 입력·외부 전송 텍스트의 결정적 사전 검사."""
import logging
import re
from urllib.parse import urlsplit

from agents.guardrails.policy import (
    GUARDRAIL_POLICY,
    GuardrailAction,
    GuardrailBoundary,
    GuardrailDecision,
    GuardrailReason,
    require_guardrail_allow,
)

log = logging.getLogger(__name__)

SECRET_PATTERNS = (
    re.compile(r"\bsk-[A-Za-z0-9_-]{20,}\b"),
    re.compile(r"\bAIza[0-9A-Za-z_-]{30,}\b"),
    re.compile(r"\b(?:api[_-]?key|secret|password|token)\s*[:=]\s*[^\s,;]{8,}", re.I),
)
PERSONAL_PATTERNS = (
    re.compile(r"\b[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}\b"),
    re.compile(r"(?<!\d)(?:\+?82[- .]?)?0?1[016789][- .]?\d{3,4}[- .]?\d{4}(?!\d)"),
)
UNTRUSTED_INSTRUCTION_PATTERNS = (
    re.compile(r"\bignore\s+(?:all\s+)?(?:previous|prior)\s+instructions\b", re.I),
    re.compile(r"(?:reveal|print|show|출력|공개).{0,30}(?:system prompt|시스템 프롬프트|api key|비밀키)", re.I),
    re.compile(r"(?:override|bypass|override the|우회|무시).{0,30}(?:policy|instruction|guardrail|규칙|지침)", re.I),
)


def redact_sensitive_text(text: str, boundary: GuardrailBoundary) -> tuple[str, GuardrailDecision]:
    """비밀값과 연락처를 마스킹하고 원문 없이 판정 로그를 한 번 남긴다.

    길이 제한은 이 함수의 책임이 아니다. 채팅 입력은 API 요청 모델이 검증한다.
    """
    redacted_text = text
    found_secret = False
    found_personal = False

    # 비밀값을 먼저 마스킹한 뒤 개인 연락처를 마스킹한다.
    for pattern in SECRET_PATTERNS:
        redacted_text, count = pattern.subn("[REDACTED_SECRET]", redacted_text)
        found_secret |= count > 0
    for pattern in PERSONAL_PATTERNS:
        redacted_text, count = pattern.subn("[REDACTED_PERSONAL_DATA]", redacted_text)
        found_personal |= count > 0

    # 탐지 결과로 판정을 만든다. 비밀값이 개인정보보다 우선한다.
    reason = (GuardrailReason.SECRET_DETECTED if found_secret else
              GuardrailReason.PERSONAL_DATA_DETECTED if found_personal else GuardrailReason.OK)
    action = GuardrailAction.REDACT if found_secret or found_personal else GuardrailAction.ALLOW
    decision = GuardrailDecision(action, reason, boundary=boundary)

    # 모든 호출 경계에서 같은 형식으로 기록하며 민감한 입력은 포함하지 않는다.
    log.info("event=guardrail_decision boundary=%s action=%s reason=%s policy=%s",
             decision.boundary, decision.action, decision.reason, decision.policy_version)
    require_guardrail_allow(decision)
    return redacted_text, decision


def remove_evil_instruction_lines(text: str, boundary: GuardrailBoundary) -> str:
    """모델에게 지시하는 문장으로 보이는 줄을 제거하고 제거한 줄 수만 로그에 남긴다."""
    kept_lines = []
    removed_line_count = 0

    # 지시문 패턴이 한 줄이라도 걸리면 그 줄 전체를 버린다.
    for line in text.splitlines():
        if any(pattern.search(line) for pattern in UNTRUSTED_INSTRUCTION_PATTERNS):
            removed_line_count += 1
            continue
        kept_lines.append(line)

    # 제거할 줄이 없으면 줄바꿈 형식까지 원문 그대로 돌려준다.
    if removed_line_count == 0:
        return text

    log.warning("event=guardrail_instruction_removed boundary=%s removed_lines=%d policy=%s",
                boundary, removed_line_count, GUARDRAIL_POLICY["version"])
    return "\n".join(kept_lines)


def is_http_url(value) -> bool:
    """http(s) 주소인지만 판정한다. 자격 증명·공백·제어 문자가 있으면 거짓이다."""
    if not isinstance(value, str):
        return False

    try:
        parsed_url = urlsplit(value)
    except ValueError:
        return False

    has_control_character = any(ord(character) < 33 for character in value)
    return (parsed_url.scheme in ("http", "https") and bool(parsed_url.hostname)
            and not parsed_url.username and not parsed_url.password and not has_control_character)
