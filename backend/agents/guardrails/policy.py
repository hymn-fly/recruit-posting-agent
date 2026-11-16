"""가드레일 단계들이 함께 사용하는 버전 정책, 판정 타입, 실패 동작."""
from dataclasses import dataclass
from enum import StrEnum
from types import MappingProxyType


GUARDRAIL_POLICY_VERSION = "2026-11-16.1"


class GuardrailAction(StrEnum):
    """검사 대상에 적용할 조치."""

    ALLOW = "allow"
    REDACT = "redact"
    BLOCK = "block"


class GuardrailReason(StrEnum):
    """로그와 호출자가 사용할 원문 비포함 사유 코드."""

    OK = "ok"
    INPUT_TOO_LARGE = "input_too_large"
    SECRET_DETECTED = "secret_detected"
    PERSONAL_DATA_DETECTED = "personal_data_detected"
    UNSAFE_REQUEST = "unsafe_request"
    UNTRUSTED_CONTENT = "untrusted_content"
    INVALID_TOOL = "invalid_tool"
    INVALID_TOOL_ARGUMENTS = "invalid_tool_arguments"
    BUDGET_EXCEEDED = "budget_exceeded"
    INVALID_OUTPUT = "invalid_output"
    UNSUPPORTED_PROVIDER = "unsupported_provider"
    VALIDATOR_UNAVAILABLE = "validator_unavailable"


class GuardrailBoundary(StrEnum):
    """가드레일 검사가 적용되는 명명된 경계."""

    CHAT_INPUT = "chat_input"
    PROFILE_DOCUMENT = "profile_document"
    PROFILE_SUMMARY_EGRESS = "profile_summary_egress"
    RETRIEVED_CONTENT = "retrieved_content"
    POSTING_COLLECTION = "posting_collection"
    LLM_EGRESS = "llm_egress"
    EMBEDDING_EGRESS = "embedding_egress"
    UNSPECIFIED = "unspecified"


@dataclass(frozen=True, slots=True)
class GuardrailDecision:
    """검사 결과. 원문이나 탐지한 민감값은 이 객체에 넣지 않는다."""

    action: GuardrailAction
    reason: GuardrailReason = GuardrailReason.OK
    policy_version: str = GUARDRAIL_POLICY_VERSION
    boundary: GuardrailBoundary = GuardrailBoundary.UNSPECIFIED

    @property
    def allowed(self) -> bool:
        """마스킹을 끝낸 뒤에도 처리를 계속해도 되는 판정인지 반환한다."""
        return self.action in (GuardrailAction.ALLOW, GuardrailAction.REDACT)


class GuardrailViolation(Exception):
    """정책 위반을 일반 처리 오류·모델 장애와 구분한다."""

    def __init__(self, decision: GuardrailDecision):
        self.decision = decision
        super().__init__(f"guardrail_blocked:{decision.boundary.value}:{decision.reason.value}")


# 단계별 구현에서 조정할 수 있도록 기본 한도와 집행 규칙을 한곳에 둔다.
# 시간·호출 한도는 다음 단계에서 실행 예산 구현이 이 값을 소비한다.
GUARDRAIL_POLICY = MappingProxyType({
    "version": GUARDRAIL_POLICY_VERSION,
    "input": MappingProxyType({
        "max_message_chars": 1_000,
        "max_upload_bytes": 20 * 1024 * 1024,
    }),
    "untrusted_content": MappingProxyType({
        "max_web_topics": 5,
        "max_queries_per_topic": 2,
        "max_query_chars": 200,
        "max_web_search_calls_per_turn": 10,
        "max_parallel_web_searches": 3,
        "max_profile_requirements": 12,
        "max_profile_evidence_per_requirement": 3,
    }),
    "execution": MappingProxyType({
        "max_turn_seconds": 180,
        "retry_policy": "count_all_providers_and_retries",
        "policy_violation_fallback": False,
        "validator_failure": "block_sensitive_operation",
    }),
    "output": MappingProxyType({
        "max_company_report_chars": 3_500,
        "max_profile_report_chars": 2_500,
    }),
    "logging": MappingProxyType({
        "include_raw_content": False,
        "include_detected_values": False,
    }),
})


def require_guardrail_allow(decision: GuardrailDecision) -> None:
    """허용 또는 마스킹 판정만 진행시키고 나머지는 전용 오류로 차단한다."""
    if not decision.allowed:
        raise GuardrailViolation(decision)
