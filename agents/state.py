from typing import Literal, Optional, TypedDict

from pydantic import BaseModel, Field


# AI Engineer 관련으로 적재하는 직군. 이 밖의 공고는 저장하지 않는다.
AiCategory = Literal["AI Engineer", "ML Engineer", "LLM Engineer", "MLOps", "Data"]


class JobPostingDraft(BaseModel):
    """목록 페이지에서 뽑은 공고 요약 (AI Engineer 관련 후보만)."""
    title: str
    url: Optional[str] = Field(None, description="목록 텍스트에 [URL]로 붙어 있는 절대 URL만. 없으면 null")
    location: Optional[str] = None
    employment_type: Optional[str] = None
    min_years: Optional[int] = None
    category: AiCategory
    posted_at: Optional[str] = Field(None, description="YYYY-MM-DD")
    deadline: Optional[str] = Field(None, description="YYYY-MM-DD")


class ExtractionResult(BaseModel):
    postings: list[JobPostingDraft]


class JDDetail(BaseModel):
    """공고 상세 페이지(JD 본문) 파싱 결과."""
    is_ai_related: bool = Field(
        description="JD 본문 기준으로 AI/ML/LLM/MLOps/데이터 사이언스 업무가 핵심인 공고인가")
    category: Optional[AiCategory] = Field(None, description="is_ai_related=false면 null")
    responsibilities: Optional[str] = Field(None, description="하는 일. 본문에 적힌 내용만")
    required_qualifications: Optional[str] = Field(None, description="필수 역량 (자격요건)")
    preferred_qualifications: Optional[str] = Field(None, description="우대 역량")
    min_years: Optional[int] = Field(None, description="요구 최소 경력 연차. 명시 없으면 null")
    deadline: Optional[str] = Field(None, description="YYYY-MM-DD. 명시 없으면 null")


class IngestState(TypedDict, total=False):
    company: dict            # scrapers.companies.Company
    raw_text: str
    postings: list[JobPostingDraft]   # 목록 단계에서 걸러진 AI 관련 후보
    final_postings: list[dict]        # 상세 JD까지 파싱 완료되어 저장할 공고
    status: str              # 'ok' | 'failed' | 'unverifiable'
    detail: str


class SearchParams(BaseModel):
    keyword: Optional[str] = None
    company_name: Optional[str] = None
    category: Optional[str] = None
    location: Optional[str] = None


class QueryState(TypedDict, total=False):
    question: str
    profile: str
    params: SearchParams
    results: list[dict]
    answer: str
