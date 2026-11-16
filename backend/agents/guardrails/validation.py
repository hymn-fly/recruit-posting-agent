"""사용자 입력·외부 전송 텍스트의 결정적 사전 검사."""
import logging
import re

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


def validate_untrusted_content(
    text: str,
    boundary: GuardrailBoundary,
    max_chars: int,
) -> tuple[str, GuardrailDecision]:
    """검색·문서 자료를 크기 제한하고 비밀값을 제거해 프롬프트에 넣는다."""
    if len(text) > max_chars:
        decision = GuardrailDecision(GuardrailAction.BLOCK, GuardrailReason.INPUT_TOO_LARGE, boundary=boundary)
        return "", decision
    
    if any(pattern.search(text) for pattern in UNTRUSTED_INSTRUCTION_PATTERNS):
        decision = GuardrailDecision(GuardrailAction.BLOCK, GuardrailReason.UNTRUSTED_CONTENT, boundary=boundary)
        return "", decision
    return redact_sensitive_text(text, boundary)


def sanitize_material_text(value, max_chars: int = 8_000) -> str:
    """문자열 자료만 검사하며 차단 자료는 원문 없이 제외한다."""
    if not isinstance(value, str):
        return ""
    sanitized, decision = validate_untrusted_content(value, GuardrailBoundary.RETRIEVED_CONTENT, max_chars)
    if not decision.allowed:
        log.warning(
            "event=guardrail_material_excluded reason=%s policy=%s",
            decision.reason.value, decision.policy_version)
    return sanitized


def sanitize_material_url(value) -> str:
    """출처는 HTTP(S) 주소만 허용하며 자격 증명과 제어 문자를 제외한다."""
    from urllib.parse import urlsplit
    sanitized = sanitize_material_text(value, 2_000)
    try:
        parsed = urlsplit(sanitized)
        if (parsed.scheme not in ("http", "https") or not parsed.hostname
                or parsed.username or parsed.password or any(ord(char) < 33 for char in sanitized)):
            return ""
    except ValueError:
        return ""
    return sanitized


def sanitize_posting_material(posting) -> dict:
    """공고의 알려진 필드만 복사해 원본과 다른 병렬 분석의 입력을 보존한다."""
    if not isinstance(posting, dict):
        return {}
    sanitized = {field: sanitize_material_text(posting.get(field), 12_000)
                 for field in ("company", "industry", "title", "location", "category", "deadline",
                               "employment_type", "responsibilities", "required_qualifications",
                               "preferred_qualifications")}
    sanitized["url"] = sanitize_material_url(posting.get("url"))

    # ID와 점수는 문자열로 강제 변환하지 않고 실제 수치만 유지한다.
    for field in ("id", "min_years", "similarity", "sim_responsibilities", "sim_required", "sim_preferred"):
        value = posting.get(field)
        sanitized[field] = value if type(value) in (int, float) else None
    return sanitized if type(sanitized.get("id")) is int and sanitized["id"] > 0 else {}


def sanitize_profile_summary(summary) -> dict:
    """프로필의 고정 필드만 검사하고 배열 크기와 각 항목 길이를 제한한다."""
    if not isinstance(summary, dict):
        return {}
    sanitized = {}
    for field in ("headline", "skills", "domains", "projects", "strengths", "total_years", "education_certs"):
        value = summary.get(field)
        if isinstance(value, str):
            sanitized[field] = sanitize_material_text(value, 2_000)
        elif field == "projects" and isinstance(value, list):
            sanitized[field] = []
            for project in value[:20]:
                if not isinstance(project, dict):
                    continue
                cleaned = {key: sanitize_material_text(project.get(key), 2_000)
                           for key in ("name", "role", "period")}
                for key in ("tech", "achievements"):
                    items = project.get(key)
                    cleaned[key] = [text for item in items[:30]
                                    if (text := sanitize_material_text(item, 2_000))] if isinstance(items, list) else []
                if cleaned["name"]:
                    sanitized[field].append(cleaned)
        elif isinstance(value, list):
            sanitized[field] = [text for item in value[:30]
                                if (text := sanitize_material_text(item, 2_000))]
        elif type(value) in (int, float):
            sanitized[field] = value
    return {field: value for field, value in sanitized.items() if value}


def sanitize_sources(sources) -> list[dict]:
    """출처 제목과 URL을 함께 검사하고 출처 순서를 유지한다."""
    if not isinstance(sources, list):
        return []
    validated = []
    for source in sources[:20]:
        if not isinstance(source, dict):
            continue
        title = sanitize_material_text(source.get("title"), 500)
        url = sanitize_material_url(source.get("url"))
        if title and url:
            validated.append({"title": title, "url": url})
    return validated
