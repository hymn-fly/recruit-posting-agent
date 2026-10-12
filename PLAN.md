# 채용공고 리서치 에이전트 — 구현 계획

이 문서는 Claude Code로 직접 구현할 때 따라갈 작업 계획입니다. 큰 그림은 두 개의 독립된 파이프라인입니다.

- **적재 에이전트(ingestion agent)**: 회사 채용 포털을 스크래핑 → 구조화 파싱 → Neon Postgres에 저장
- **질의 에이전트(query agent)**: 사용자의 자연어 질문 → DB 조회(커스텀 MCP 서버 경유) → 추천 답변 생성

두 에이전트는 **같은 Neon DB**를 공유하지만 서로 직접 호출하지 않습니다. DB가 둘 사이의 유일한 연결점입니다.

## 진행 체크리스트 (2026-10-12 기준)

### 0단계 — Neon 준비
- [x] Neon 프로젝트 생성, `.env`에 `NEON_DATABASE_URL` 저장
- [x] 스키마 적용 (Neon에 `companies` / `job_postings` / `scrape_runs` 3개 테이블 존재 확인)
- [ ] `ANTHROPIC_API_KEY`를 `.env`에 입력 (현재 비어 있음)
- [ ] `.env.example`의 실제 비밀번호를 placeholder로 교체 + Neon 비밀번호 재발급

### 1단계 — DB 스키마 (`db/schema.sql`)
- [x] 3개 테이블, 유니크 제약, 인덱스, CHECK 제약, COMMENT 작성
- [x] `description`을 `responsibilities` / `required_qualifications` / `preferred_qualifications` 3개로 분리 (PLAN 초안에서 변경)
- [ ] `CREATE EXTENSION vector` 실제 적용 여부 확인

### 2단계 — MCP 서버 (`mcp_server/server.py`)
- [x] `upsert_company`, `upsert_job_posting`, `record_scrape_run`, `search_jobs`, `list_companies`
- [x] asyncpg 풀 재사용, `search_jobs` 파라미터 바인딩, limit 1~100 클램프
- [x] `description` 컬럼 참조를 3개 컬럼(하는 일/필수/우대)으로 교체 (INSERT SQL은 트랜잭션 롤백으로 검증, MCP 5개 tool 로딩·조회 확인)

### 3단계 — 스크래핑 대상 (`scrapers/companies.py`)
- [x] 23개 회사 메타데이터 (`COMPANIES`, `COMPANY_BY_NAME`)
- [ ] 일부 URL이 `...`로 잘려 있음 (현대자동차, BCG) — 실제 URL로 보완

### 4단계 — 적재 에이전트 (`agents/ingest_graph.py`)
- [x] LangGraph: `fetch_page` → `extract_postings` → `save_postings` → `finish`
- [x] `static_html`/`server_error`/`api_error`는 httpx+BeautifulSoup, 재시도 1회
- [x] `robots_blocked`/`js_rendered`는 "unverifiable"로 기록 후 종료
- [x] `agents/mcp_client.py` (`MultiServerMCPClient` stdio 연결)
- [x] `agents/main_ingest.py` (회사명 인자 또는 전체 실행)
- [x] `JobPostingDraft`를 새 컬럼 구조로 수정 (`posted_at` 포함)
- [ ] **HTML에서 링크(href)가 버려짐** — `get_text()`만 써서 공고 URL이 항상 null → NULL은 UNIQUE에서 서로 다른 값 취급이라 재실행 시 중복 적재됨. 링크 보존 또는 URL 없는 공고의 dedup 키 필요
- [x] AI Engineer 관련 공고만 적재 (목록 1차 + JD 본문 2차 필터) — 2026-10-12, 코드만 완료/실행 미검증
- [x] 공고 링크 방문 → JD 본문 파싱 후 적재 — 위와 동일 (상세는 CHANGELOG.md #6~#8)
- [x] `companies.industry` 컬럼 추가 (DB·코드 반영, CHANGELOG.md #5)
- [ ] `js_rendered` 회사 처리 (Claude in Chrome 또는 잡포털 미러) — 현재는 건너뜀

### 5단계 — 질의 에이전트 (`agents/query_graph.py`)
- [x] LangGraph: `plan_search` → `run_search` → `synthesize_answer`
- [x] 결과 없으면 "공고 없음" 답변, 지어내지 않도록 프롬프트 명시
- [x] `agents/main_query.py` CLI
- [x] `search_jobs` keyword 검색을 새 컬럼으로 수정

### 6단계 — 실행/테스트
- [x] 의존성 설치 (`uv sync`)
- [x] `pyproject.toml` 작성 (`requirements.txt`는 아직 남아 있음 — 삭제 여부 결정 필요)
- [ ] 현대오토에버 1곳 단독 적재 테스트
- [ ] 23개 전체 적재
- [ ] 질의 에이전트 CLI 수동 테스트
- [ ] `scrape_runs` 조회로 실패/확인불가 회사 점검 (현재 DB 3개 테이블 모두 0행 = 한 번도 실행 안 됨)

### 7단계 — 이후 확장
- [ ] 스케줄링
- [ ] Dedup/diff (`is_active=false` 처리 배치)
- [ ] 임베딩 검색 (pgvector)
- [ ] 신규 공고 알림

---

## 확정된 기술 스택

- 언어: **Python**
- 에이전트 오케스트레이션: **LangGraph**
- DB: **Neon Postgres**
- DB 접근: **커스텀 MCP 서버를 직접 작성** (Neon 공식 MCP 서버를 쓰지 않고, 역할이 명확한 tool만 노출)
- 스크래핑 1차 대상: **기존에 조사했던 23개 회사 포털 재사용** (아래 "회사별 접근성 메모" 참고)
- 스크래핑 도구: WebSearch / Claude in Chrome (JS 렌더링·로그인이 필요한 사이트용)

## 프로젝트 구조

```
job-agent/
  PLAN.md                  # 이 파일
  .env.example
  requirements.txt
  db/
    schema.sql             # Neon에 적용할 스키마
  mcp_server/
    server.py              # 커스텀 MCP 서버 (FastMCP 기반)
  scrapers/
    companies.py           # 23개 회사 메타데이터 (URL, 접근방식, 필터 조건)
  agents/
    state.py               # LangGraph 상태 스키마 (TypedDict)
    ingest_graph.py         # 적재 에이전트 그래프
    query_graph.py           # 질의 에이전트 그래프
    main_ingest.py          # 적재 에이전트 실행 엔트리포인트
    main_query.py            # 질의 에이전트 실행 엔트리포인트 (CLI)
```

## 단계별 작업 순서

### 0단계 — Neon 프로젝트 준비 (사람이 직접 할 일)

1. https://neon.tech 에서 프로젝트 생성
2. Connection string(`postgresql://...`)을 복사해 `.env`의 `NEON_DATABASE_URL`에 저장
3. `psql "$NEON_DATABASE_URL" -f db/schema.sql` 로 스키마 적용

### 1단계 — DB 스키마 작성 (`db/schema.sql`)

아래 스키마를 그대로 사용하거나 다듬어서 적용합니다. 핵심 설계 포인트:

- `companies`와 `job_postings`를 분리하고, `job_postings`는 `(company_id, title, url)` 유니크 제약으로 **중복 적재 방지**
- 재실행 시 이미 있는 공고는 `last_seen_at`만 갱신하고 사라진 공고는 `is_active=false`로 내릴 수 있게 설계 (dedup/diff 로직은 3단계에서)
- `scrape_runs`는 "자동 확인 불가"였던 회사·실행을 추적하기 위한 로그 테이블
- `pgvector` extension은 지금 당장 안 써도 되지만, 나중에 "비슷한 공고 추천"을 하고 싶을 때를 위해 미리 켜둠

```sql
CREATE EXTENSION IF NOT EXISTS vector;

CREATE TABLE IF NOT EXISTS companies (
    id            SERIAL PRIMARY KEY,
    name          TEXT NOT NULL UNIQUE,
    portal_url    TEXT NOT NULL,
    access_method TEXT NOT NULL DEFAULT 'unknown',
        -- 'static_html' | 'js_rendered' | 'robots_blocked' | 'server_error' | 'api_error' | 'unknown'
    notes         TEXT,
        -- 예: '서울/경기 지역만', '롯데이노베이트 계열사만'
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS job_postings (
    id              SERIAL PRIMARY KEY,
    company_id      INTEGER NOT NULL REFERENCES companies(id) ON DELETE CASCADE,
    title           TEXT NOT NULL,
    url             TEXT,
    location        TEXT,
    employment_type TEXT,
    min_years       INTEGER,
    category        TEXT,           -- AI Engineer / ML Engineer / LLM Engineer / MLOps / Backend 등
    description     TEXT,
    posted_at       DATE,
    deadline        DATE,
    source_snapshot TEXT,
    is_active       BOOLEAN NOT NULL DEFAULT true,
    first_seen_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    last_seen_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (company_id, title, url)
);

CREATE INDEX IF NOT EXISTS idx_job_postings_category ON job_postings(category);
CREATE INDEX IF NOT EXISTS idx_job_postings_active   ON job_postings(is_active);
CREATE INDEX IF NOT EXISTS idx_job_postings_company  ON job_postings(company_id);

CREATE TABLE IF NOT EXISTS scrape_runs (
    id          SERIAL PRIMARY KEY,
    company_id  INTEGER NOT NULL REFERENCES companies(id) ON DELETE CASCADE,
    status      TEXT NOT NULL,   -- 'ok' | 'failed' | 'unverifiable'
    detail      TEXT,
    run_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_scrape_runs_company ON scrape_runs(company_id);
```

### 2단계 — 커스텀 MCP 서버 (`mcp_server/server.py`)

Python MCP SDK의 `FastMCP`로 작성. Neon Postgres에는 `asyncpg`로 연결. 노출할 tool은 **딱 필요한 만큼만** — 질의 에이전트가 임의 SQL을 실행하지 못하게 막는 게 핵심입니다.

| Tool | 용도 | 호출 주체 |
|---|---|---|
| `upsert_company(name, portal_url, access_method, notes)` | 회사/포털 메타데이터 등록·갱신 | 적재 에이전트 |
| `upsert_job_posting(company_name, title, url, location, employment_type, min_years, category, description, posted_at, deadline, source_snapshot)` | 공고 1건 저장 (있으면 `last_seen_at` 갱신) | 적재 에이전트 |
| `record_scrape_run(company_name, status, detail)` | 이번 실행에서 그 회사가 성공/실패/확인불가였는지 로그 | 적재 에이전트 |
| `search_jobs(keyword?, company_name?, category?, location?, only_active=true, limit=20)` | 조건에 맞는 공고 검색 (파라미터화된 SQL만 실행) | 질의 에이전트 |
| `list_companies()` | 등록된 회사·접근성 상태 목록 | 둘 다 |

구현 시 주의할 점:

- `upsert_job_posting`의 충돌 키는 `(company_id, title, url)` — `ON CONFLICT ... DO UPDATE SET last_seen_at = now(), is_active = true, description = EXCLUDED.description, deadline = EXCLUDED.deadline`
- `search_jobs`는 동적으로 `WHERE` 절을 조립하되, 모든 값은 파라미터 바인딩(`$1, $2, ...`)으로 넘기고 `limit`만 `int()` 캐스팅 후 직접 삽입 (SQL 인젝션 방지)
- `keyword`는 제목/설명에 대한 `ILIKE '%...%'` 부분일치로 시작 (임베딩 기반 유사도 검색은 나중 확장 과제)
- 커넥션은 `asyncpg.create_pool`로 한 번 만들어 재사용

### 3단계 — 스크래핑 대상 정의 (`scrapers/companies.py`)

아래는 직전에 23개 회사를 실제로 스크래핑 시도해서 얻은 접근성 정보입니다. 이 데이터를 `access_method`/`notes`로 그대로 시딩하면, 적재 에이전트가 "이 사이트는 JS 렌더링이라 브라우저 도구가 필요하다"는 걸 미리 알고 전략을 바꿀 수 있습니다.

| 회사 | URL | access_method | notes |
|---|---|---|---|
| 삼성 | https://www.samsungcareers.com/hr/ | api_error | 정적 fetch 시 JSON 500 에러 반환, 하위 검색 경로 필요 |
| LG(CNS) | https://careers.lg.com/apply?c=CNS | js_rendered | SPA, 정적 HTML에 공고 없음 |
| SK | https://www.skcareers.com/Recruit | js_rendered | 템플릿 플레이스홀더만 반환 |
| 현대오토에버 | https://career.hyundai-autoever.com/ko/apply | static_html | 정상 작동, 개별 공고 URL 패턴: `/ko/o/{id}` |
| 현대(현대자동차) | https://talent.hyundai.com/apply/applyList.hc?... | js_rendered | AJAX 렌더링 |
| 하나은행 | https://hanabankhr.career.co.kr/jobs/ | robots_blocked | robots.txt 차단 — 브라우저 도구로도 우회 전 정책 확인 필요 |
| KB | https://careers.kbfg.com/apply/apply | js_rendered | SPA |
| 신한 | https://shinhandigital.recruiter.co.kr/career/jobs | js_rendered | SPA |
| IBK기업은행 | https://ibk.incruit.com/index_main_2025.asp | server_error | 인크루트 플랫폼, 재시도 후에도 서버 오류 |
| 농협 | https://with.nonghyup.com/jbnf/jbnfLst.do | robots_blocked | **서울/경기 지역만 필터링** |
| 우리은행 | https://jrs.jobkorea.co.kr/wooribank | static_html | 신입 공채 섹션 단위, 개별 공고 URL 없음 |
| 포스코 | https://recruit.posco.com/h22a01-front/H22A1000.html?codeNum=30 | robots_blocked | |
| 한화 | https://www.hanwhain.com/portal/apply/recruit | robots_blocked | |
| LIG | https://ligdna.recruiter.co.kr/career/home | js_rendered | SPA |
| PwC | https://www.pwcconsulting.co.kr/ko/career/consultant.html | static_html | 정상 작동 |
| Bain | https://www.bain.com/ko/careers/find-a-role/?filters=offices(277)\| | robots_blocked | **서울 오피스 공고 위주** |
| BCG | https://careers.bcg.com/global/en/search-results?... | js_rendered | 검색 결과가 JS로 채워짐 |
| 롯데 | https://recruit.lotte.co.kr/apply/announcement | static_html | **"롯데이노베이트" 계열사 공고만 필터링** |
| CJ | https://recruit.cj.net/recruit/ko/recruit/recruit/list.fo | static_html | 정상 작동 |
| 두산 | https://career.doosan.com/dsp/sa/RecList.jsp | static_html | 목록은 정적으로 보이나 개별 공고는 JS 모달 (상세 URL 없음) |
| 현대모비스 | https://careers.mobis.com/jobs | static_html | 정상 작동, 상세 URL 패턴: `/jobs-view?seq={id}` |
| LG AI연구원 | https://www.lgresearch.ai/careers | js_rendered | SPA — catch.co.kr 같은 잡포털 미러로 보완 가능 |
| KT | https://recruit.kt.com/careers/ | js_rendered | SPA — catch.co.kr 같은 잡포털 미러로 보완 가능 |

`access_method`에 따라 적재 에이전트가 쓸 전략을 다르게 분기하세요:

- `static_html` → 단순 fetch/파싱으로 충분
- `js_rendered` → Claude in Chrome(또는 다른 브라우저 자동화)로 렌더링 후 텍스트 추출 필요. 또는 `catch.co.kr` 같은 잡포털 미러 페이지를 보조 소스로 활용 (LG AI연구원/KT에서 효과적이었음)
- `robots_blocked` → 자동 크롤링이 정책적으로 막혀 있음. 브라우저 자동화로도 우회가 적절한지는 사람이 먼저 판단할 사항 — 일단은 "자동 확인 불가"로 남기고 `scrape_runs`에 기록하는 것을 권장
- `server_error` / `api_error` → 재시도 1회 후 실패하면 "확인 불가"로 기록

### 4단계 — 적재 에이전트 (`agents/ingest_graph.py`)

LangGraph 노드 구성 (회사 1개당 1회 실행):

1. **`fetch_page`**: `access_method`에 따라 WebSearch 또는 Claude in Chrome으로 raw 텍스트 확보. 실패하면 바로 `record_scrape_run(status='failed'|'unverifiable')` 호출 후 종료.
2. **`extract_postings`**: raw 텍스트를 LLM에 넘겨 `JobPostingDraft` 리스트로 구조화 추출 (제목/URL/지역/경력/직군분류/마감일). `notes`에 적힌 필터 조건(서울·경기만, 롯데이노베이트만 등)을 프롬프트에 반영. **실제로 존재하지 않는 공고를 지어내지 않도록** "확인된 것만 추출" 지시를 명시.
3. **`save_postings`**: `upsert_company` → 각 공고에 대해 `upsert_job_posting` → `record_scrape_run(status='ok')` 순서로 MCP tool 호출.

MCP 서버와의 연결은 `langchain-mcp-adapters`의 `MultiServerMCPClient`로 tool을 LangChain tool 형태로 불러와 LangGraph 노드 안에서 호출하는 방식을 권장합니다.

### 5단계 — 질의 에이전트 (`agents/query_graph.py`)

LangGraph 노드 구성:

1. **`plan_search`**: 사용자 질문을 LLM에 넘겨 `search_jobs` 호출 파라미터(`keyword`, `category`, `company_name`, `location`)로 변환.
2. **`run_search`**: MCP tool `search_jobs` 호출.
3. **`synthesize_answer`**: 검색 결과를 바탕으로 사용자 프로필(이력서 요약)과 매칭해 추천 이유를 설명. 결과가 없으면 "현재 조건에 맞는 공고 없음"을 명확히 답변 (지어내지 않음).

처음엔 구조화 필터(SQL WHERE)만으로 충분합니다. "비슷한 공고 추천"처럼 느슨한 유사도가 필요해지면 그때 `pgvector` + description embedding을 추가하세요 (스키마에는 이미 extension을 켜둠).

### 6단계 — 실행/테스트

- 적재 에이전트: 회사 1곳(예: 현대오토에버 — `static_html`이라 가장 쉬움)부터 단독 실행해 DB에 잘 들어가는지 확인 → 이후 23개 전체로 확장
- 질의 에이전트: `python agents/main_query.py "LLM 에이전트 경험 살릴 수 있는 공고 있어?"` 같은 CLI로 수동 테스트
- `scrape_runs` 테이블을 조회해 이번 실행에서 어떤 회사가 실패/확인불가였는지 한눈에 확인

### 7단계 — 이후 확장 (지금 당장은 불필요)

- 스케줄링: cron 또는 기존에 쓰던 Claude 스케줄 작업으로 적재 에이전트를 주기 실행
- Dedup/diff: 이번 실행에서 안 보인 공고를 `is_active=false`로 내리는 배치 로직
- 임베딩 검색: `description`에 대한 벡터 생성 → `search_jobs`에 유사도 검색 옵션 추가
- 알림: "새 공고 발견 시에만" 알려주는 로직 (기존 PushNotification 루틴과 유사한 패턴)

## 참고 — 설치 패키지 (`requirements.txt` 초안)

```
langgraph>=0.2
langchain-anthropic
langchain-mcp-adapters
mcp
asyncpg
python-dotenv
```

## 참고 — 환경변수 (`.env.example` 초안)

```
ANTHROPIC_API_KEY=
NEON_DATABASE_URL=postgresql://<user>:<password>@<host>/<db>?sslmode=require
```
