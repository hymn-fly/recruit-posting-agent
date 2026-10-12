-- Neon Postgres 스키마: 채용공고 적재/조회 에이전트용
-- 적용: psql "$NEON_DATABASE_URL" -f db/schema.sql

-- (선택) 나중에 임베딩 기반 유사도 검색을 붙일 때를 위해 pgvector를 켜둡니다. 지금은 안 써도 됩니다.
CREATE EXTENSION IF NOT EXISTS vector;

-- ---------------------------------------------------------------
-- companies
-- ---------------------------------------------------------------
CREATE TABLE IF NOT EXISTS companies (
    id            SERIAL PRIMARY KEY,
    name          TEXT NOT NULL UNIQUE,
    portal_url    TEXT NOT NULL,
    access_method TEXT NOT NULL DEFAULT 'unknown',
    notes         TEXT,
    industry      TEXT,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT chk_companies_access_method CHECK (
        access_method IN ('static_html', 'js_rendered', 'robots_blocked',
                          'server_error', 'api_error', 'unknown')
    )
);

COMMENT ON TABLE  companies IS '채용공고를 수집하는 대상 회사 및 채용 포털 메타데이터';
COMMENT ON COLUMN companies.id            IS '회사 고유 번호 (자동 증가)';
COMMENT ON COLUMN companies.name          IS '회사명 (유니크). 예: 현대오토에버, KT';
COMMENT ON COLUMN companies.portal_url    IS '채용 포털 목록 페이지 URL (스크래핑 시작점)';
COMMENT ON COLUMN companies.access_method IS '스크래핑 접근 방식: static_html(단순 fetch 가능) | js_rendered(브라우저 렌더링 필요) | robots_blocked(robots.txt 차단) | server_error | api_error | unknown';
COMMENT ON COLUMN companies.notes         IS '스크래핑 시 지켜야 할 조건/특이사항. 예: 서울/경기 지역만, 롯데이노베이트 계열사만';
COMMENT ON COLUMN companies.industry      IS '회사가 속한 산업. 예: 금융, 자동차, IT서비스, 통신, 컨설팅';
COMMENT ON COLUMN companies.created_at    IS '회사 등록 시각';

-- 기존 DB에 컬럼만 추가할 때: ALTER TABLE companies ADD COLUMN IF NOT EXISTS industry TEXT;

-- ---------------------------------------------------------------
-- job_postings
-- ---------------------------------------------------------------
CREATE TABLE IF NOT EXISTS job_postings (
    id                      SERIAL PRIMARY KEY,
    company_id              INTEGER NOT NULL REFERENCES companies(id) ON DELETE CASCADE,
    title                   TEXT NOT NULL,
    url                     TEXT,
    location                TEXT,
    employment_type         TEXT,
    min_years               INTEGER,
    category                TEXT,
    responsibilities        TEXT,   -- 하는 일
    required_qualifications TEXT,   -- 필수 역량
    preferred_qualifications TEXT,  -- 우대 역량
    posted_at               DATE,
    deadline                DATE,
    source_snapshot         TEXT,
    is_active               BOOLEAN NOT NULL DEFAULT true,
    first_seen_at           TIMESTAMPTZ NOT NULL DEFAULT now(),
    last_seen_at            TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (company_id, title, url)
);

COMMENT ON TABLE  job_postings IS '수집된 채용공고. 공고 1건이 1행';
COMMENT ON COLUMN job_postings.id                       IS '공고 고유 번호 (자동 증가)';
COMMENT ON COLUMN job_postings.company_id               IS '공고를 올린 회사 (companies.id 참조, 회사 삭제 시 공고도 삭제)';
COMMENT ON COLUMN job_postings.title                    IS '공고 제목. 예: [Tech] AI Engineer - LLM 엔지니어. 중복 판별 키의 일부';
COMMENT ON COLUMN job_postings.url                      IS '개별 공고 상세 링크. 개별 URL이 없는 포털은 NULL';
COMMENT ON COLUMN job_postings.location                 IS '근무지. 예: 서울 강남구, 경기 성남시';
COMMENT ON COLUMN job_postings.employment_type          IS '고용 형태. 신입 / 경력 / 계약직 / 인턴 등';
COMMENT ON COLUMN job_postings.min_years                IS '요구 최소 경력 연차(숫자만). 명시가 없으면 NULL';
COMMENT ON COLUMN job_postings.category                 IS '직군 분류: AI Engineer / ML Engineer / LLM Engineer / MLOps / Backend 등. 적재 에이전트(LLM)가 채움';
COMMENT ON COLUMN job_postings.responsibilities         IS '하는 일: 담당 업무·프로젝트에 대한 설명';
COMMENT ON COLUMN job_postings.required_qualifications  IS '필수 역량: 지원에 반드시 필요한 자격·기술·경험';
COMMENT ON COLUMN job_postings.preferred_qualifications IS '우대 역량: 있으면 가산점이 되는 자격·기술·경험';
COMMENT ON COLUMN job_postings.posted_at                IS '공고 게시일. 페이지에 없으면 NULL';
COMMENT ON COLUMN job_postings.deadline                 IS '지원 마감일. 상시채용이거나 명시가 없으면 NULL';
COMMENT ON COLUMN job_postings.source_snapshot          IS '스크래핑 당시 원문 스냅샷. LLM 구조화 결과 검증/디버깅용';
COMMENT ON COLUMN job_postings.is_active                IS '현재 열려 있는 공고인지 여부. 이후 실행에서 안 보이면 false로 내림';
COMMENT ON COLUMN job_postings.first_seen_at            IS '이 공고를 처음 발견한 시각 (신규 공고 판별 기준)';
COMMENT ON COLUMN job_postings.last_seen_at             IS '가장 최근 스크래핑에서 확인된 시각';

CREATE INDEX IF NOT EXISTS idx_job_postings_category ON job_postings(category);
CREATE INDEX IF NOT EXISTS idx_job_postings_active   ON job_postings(is_active);
CREATE INDEX IF NOT EXISTS idx_job_postings_company  ON job_postings(company_id);

-- ---------------------------------------------------------------
-- scrape_runs
-- ---------------------------------------------------------------
CREATE TABLE IF NOT EXISTS scrape_runs (
    id          SERIAL PRIMARY KEY,
    company_id  INTEGER NOT NULL REFERENCES companies(id) ON DELETE CASCADE,
    status      TEXT NOT NULL,
    detail      TEXT,
    run_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT chk_scrape_runs_status CHECK (status IN ('ok', 'failed', 'unverifiable'))
);

COMMENT ON TABLE  scrape_runs IS '회사별 스크래핑 실행 기록. "자동 확인 불가" 회사를 추적하는 용도';
COMMENT ON COLUMN scrape_runs.id         IS '실행 기록 고유 번호 (자동 증가)';
COMMENT ON COLUMN scrape_runs.company_id IS '스크래핑 대상 회사 (companies.id 참조)';
COMMENT ON COLUMN scrape_runs.status     IS '실행 결과: ok(정상 확인) | failed(오류) | unverifiable(JS 렌더링/robots 차단 등으로 확인 불가)';
COMMENT ON COLUMN scrape_runs.detail     IS '실패/확인불가 사유 등 상세 메모';
COMMENT ON COLUMN scrape_runs.run_at     IS '실행 시각';

CREATE INDEX IF NOT EXISTS idx_scrape_runs_company ON scrape_runs(company_id);
-- ---------------------------------------------------------------
-- filtered_postings: LLM 필터가 걸러낸(= job_postings에 저장하지 않은) 공고
-- 필터가 얼마나 잘 거르는지(놓친 AI 공고/잘못 거른 공고) 사람이 검수하기 위한 테이블
-- ---------------------------------------------------------------
CREATE TABLE IF NOT EXISTS filtered_postings (
    id              SERIAL PRIMARY KEY,
    company_id      INTEGER NOT NULL REFERENCES companies(id) ON DELETE CASCADE,
    title           TEXT NOT NULL,
    url             TEXT,
    location        TEXT,
    stage           TEXT NOT NULL,
    reason          TEXT,
    is_actually_ai  BOOLEAN,
    first_seen_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    last_seen_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT chk_filtered_postings_stage CHECK (stage IN ('list', 'detail')),
    -- url이 NULL인 공고도 같은 (회사, 제목)이면 중복으로 본다
    CONSTRAINT uq_filtered_postings UNIQUE NULLS NOT DISTINCT (company_id, title, url)
);

COMMENT ON TABLE  filtered_postings IS 'AI Engineer 관련이 아니라고 LLM이 걸러낸 공고. 필터 품질 검수용';
COMMENT ON COLUMN filtered_postings.id             IS '고유 번호 (자동 증가)';
COMMENT ON COLUMN filtered_postings.company_id     IS '공고를 올린 회사 (companies.id 참조)';
COMMENT ON COLUMN filtered_postings.title          IS '공고 제목';
COMMENT ON COLUMN filtered_postings.url            IS '공고 상세 링크. 없으면 NULL';
COMMENT ON COLUMN filtered_postings.location       IS '근무지 (목록에 있을 때만)';
COMMENT ON COLUMN filtered_postings.stage          IS '어느 단계에서 걸러졌는지: list(목록 제목·직군만 보고 제외) | detail(JD 본문을 읽고 제외)';
COMMENT ON COLUMN filtered_postings.reason         IS 'LLM이 제외한 이유 (한 줄)';
COMMENT ON COLUMN filtered_postings.is_actually_ai IS '사람이 검수한 정답. NULL=미검수, true=실제로는 AI 공고(필터가 놓침), false=제외가 맞음';
COMMENT ON COLUMN filtered_postings.first_seen_at  IS '처음 걸러진 시각';
COMMENT ON COLUMN filtered_postings.last_seen_at   IS '가장 최근에 다시 걸러진 시각';

CREATE INDEX IF NOT EXISTS idx_filtered_postings_company ON filtered_postings(company_id);
CREATE INDEX IF NOT EXISTS idx_filtered_postings_stage   ON filtered_postings(stage);
