import operator
from typing import Annotated, Literal, Optional, TypedDict

from langgraph.graph.message import add_messages
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


# ---------------------------------------------------------------
# 채용공고 조회 Agent (RAG)
# ---------------------------------------------------------------
class JobSearchPlan(BaseModel):
    query: str = Field(description="임베딩 검색문. 원하는 역할·하는 일·보유/요구 역량을 서술한 1~3문장")
    category: Optional[str] = Field(None, description="명시된 경우만: AI Engineer / ML Engineer / LLM Engineer / MLOps / Data")
    company_name: Optional[str] = None
    location: Optional[str] = None
    max_years: Optional[int] = Field(None, description="사용자 경력 연차가 명시된 경우만 그 숫자")


class Pick(BaseModel):
    id: int = Field(description="검색 결과의 공고 id")
    reason: str = Field(description="하는 일·자격요건 근거로 왜 맞는지 1~2문장")


class Ranking(BaseModel):
    picks: list[Pick] = Field(description="가장 잘 맞는 순서. 최대 5건, 맞는 게 없으면 빈 리스트")


class Project(BaseModel):
    name: str
    role: Optional[str] = None
    period: Optional[str] = None
    tech: list[str] = []
    achievements: list[str] = Field([], description="문서에 적힌 성과(수치 포함)만")


class ProfileSummary(BaseModel):
    """프로필 문서 전체를 요약한 구조화 프로필 (profile_summary 테이블에 저장)."""
    headline: str = Field(description="한 줄 소개. 예: '백엔드 5년차, 최근 2년 LLM 서비스 개발'")
    total_years: Optional[float] = Field(None, description="총 경력 연차. 문서로 계산 불가하면 null")
    skills: list[str] = Field(description="기술 스택·도구")
    domains: list[str] = Field(description="경험한 산업/업무 도메인")
    projects: list[Project]
    education_certs: list[str] = []
    strengths: list[str] = Field(description="문서 근거로 보이는 강점 3~5개")


class Requirement(BaseModel):
    text: str = Field(description="요구사항 1개를 독립적으로 이해되게 서술")
    type: Literal["required", "preferred"]


class Requirements(BaseModel):
    items: list[Requirement] = Field(description="중복 없이 최대 12개")


class Assessment(BaseModel):
    index: int = Field(description="요구사항 번호")
    status: Literal["충족", "부분", "미충족"]
    evidence: Optional[str] = Field(None, description="근거가 되는 프로필 원문 인용(그대로). 없으면 null")
    comment: str = Field(description="판단 이유 한 줄")


class Assessments(BaseModel):
    items: list[Assessment]


class ProfileGapState(TypedDict, total=False):
    posting: dict            # get_job_posting 결과
    research_report: str     # 기업분석 리포트(있으면 보완 방향에 반영)
    summary: dict            # get_profile_summary 결과
    requirements: list[Requirement]
    evidence: list[list[dict]]   # 요구사항별 search_profile 결과
    assessments: list[Assessment]
    answer: str


# ---------------------------------------------------------------
# 기업분석 Agent (Research)
# ---------------------------------------------------------------
# industry~company는 회사 단위(캐시 재사용), role은 공고 직무 단위(매번 새로)
TopicKey = Literal["industry", "outlook", "competitors", "company", "role"]
COMPANY_TOPICS = ("industry", "outlook", "competitors", "company")


class TopicQueries(BaseModel):
    topic: TopicKey
    queries: list[str] = Field(description="웹 검색어 2개. 회사명·산업명·연도 등 구체 키워드 포함")


class ResearchPlan(BaseModel):
    target_company: str = Field(description="실제 채용 법인/계열사명. 그룹 채용 포털이면 제목·근무지·본문에 적힌 계열사(예: 티맵모빌리티), 아니면 회사명 그대로")
    industry: str = Field(description="회사가 속한 산업(세부 분야까지). 예: 자동차 SW/모빌리티 IT서비스")
    competitors: list[str] = Field(description="같은 산업의 주요 경쟁사 3~5곳")
    topics: list[TopicQueries]


class ResearchState(TypedDict, total=False):
    posting: dict
    cached: list[dict]           # 재사용하는 회사 단위 토픽
    plan: ResearchPlan
    topics: Annotated[list[dict], operator.add]   # {topic, summary, sources}: 병렬 노드 결과 누적
    report: str
    answer: str


# ---------------------------------------------------------------
# Supervisor (3개 Agent 조율)
# ---------------------------------------------------------------
AgentName = Literal["job_search", "company_research", "profile_gap"]


class Route(BaseModel):
    next: Literal["job_search", "company_research", "profile_gap", "FINISH"]
    posting_number: Optional[int] = Field(None, description="사용자가 '2번 공고'처럼 추천 목록 번호로 가리키면 그 번호")
    posting_id: Optional[int] = Field(None, description="사용자가 공고 id를 직접 말하면 그 id")
    use_profile: bool = Field(False, description="job_search에서 '나한테 맞는'처럼 사용자 본인 경력 기준 검색을 원하면 true")
    reply: Optional[str] = Field(None, description="FINISH이면서 Agent 결과가 없을 때(인사/사용법 질문/공고 선택 요청 등) 사용자에게 할 말")
    reason: str = Field(description="이렇게 판단한 이유 한 줄")


class AppState(TypedDict, total=False):
    messages: Annotated[list, add_messages]
    candidates: list[dict]       # 최근 job_search 추천 목록 (번호 = 인덱스+1)
    selected_posting: dict       # 분석 대상 공고 (get_job_posting 결과)
    research_report: str         # selected_posting 기준 기업분석 리포트
    research_posting_id: int     # research_report가 어느 공고 기준인지
    gap_report: str
    route: dict                  # Route.model_dump()
    steps: int                   # 이번 턴에 실행한 Agent 수 (턴마다 0으로 초기화)
    turn_outputs: list[dict]     # 이번 턴 Agent 결과 [{agent, text}] (턴마다 초기화)


class JobSearchState(TypedDict, total=False):
    question: str
    profile: str             # 프로필 요약(있으면 검색문·추천에 반영)
    plan: JobSearchPlan
    results: list[dict]
    candidates: list[dict]   # 추천 순서대로 공고 + reason
    answer: str
