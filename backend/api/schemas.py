"""API에서 사용하는 조회 결과 스키마."""
from pydantic import Field

from common.schemas import JobPosting


class PostingWithSavedReports(JobPosting):
    """공고 상세(JobPosting)와 요청한 저장 보고서의 조회 결과."""

    cached_research_report: str | None = Field(
        description="최신 기업 분석 본문. 조회하지 않았거나 저장된 보고서가 없으면 None")
    saved_gap_report: str | None = Field(
        description="저장된 역량 분석 본문. 조회하지 않았거나 저장된 보고서가 없으면 None")
