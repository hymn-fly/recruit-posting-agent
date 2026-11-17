"""mcp_server와 backend가 함께 쓰는 데이터 스키마. '공고'의 모양은 여기 모델이 단일 출처다.

nullable 여부는 mcp_server/sql/schema.sql의 job_postings·companies 컬럼과 맞춘다.
"""
from datetime import date
from typing import Optional

from pydantic import BaseModel, Field


class JobPostingBase(BaseModel):
    """목록·검색 결과에 공통으로 쓰는 공고 요약 필드."""
    id: int
    company: str = Field(description="회사명 (companies.name)")
    industry: Optional[str] = Field(default=None, description="회사 소속 산업")
    title: str = Field(description="공고 제목")
    url: Optional[str] = Field(default=None, description="공고 상세 링크. 개별 URL이 없는 포털은 None")
    location: Optional[str] = Field(default=None, description="근무지")
    min_years: Optional[int] = Field(default=None, description="요구 최소 경력 연차. 명시가 없으면 None")
    category: Optional[str] = Field(default=None, description="직군 분류 (AI Engineer / MLOps / Backend 등)")
    deadline: Optional[date] = Field(default=None, description="마감일. None이면 상시 채용")


class JobPosting(JobPostingBase):
    """공고 상세 조회용 모델. JobPostingBase를 상속하고, 공고 본문·자격요건·우대사항을 추가한다."""
    employment_type: Optional[str] = Field(default=None, description="고용 형태 (정규직, 계약직 등)")
    responsibilities: Optional[str] = Field(default=None, description="하는 일")
    required_qualifications: Optional[str] = Field(default=None, description="필수 자격요건")
    preferred_qualifications: Optional[str] = Field(default=None, description="우대 자격요건")
    posted_at: Optional[date] = Field(default=None, description="공고 게시일. None이면 알 수 없음")
    is_active: Optional[bool] = Field(default=None, description="공고가 현재 활성 상태인지 여부. None이면 알 수 없음")
