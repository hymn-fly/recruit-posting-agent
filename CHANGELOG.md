# 개선 로그

개선사항을 하나씩 기록한다. 새 항목은 **맨 아래에 번호를 이어서** 추가한다.
각 항목: 무엇을 / 왜 / 변경 파일 / 검증 여부.

---

## 2026-10-12

### #1 `description` 컬럼 불일치 수정
- **왜**: DB 스키마는 `description`을 `responsibilities` / `required_qualifications` / `preferred_qualifications` 3개로 나눴는데, 코드는 `description`을 그대로 써서 `upsert_job_posting`과 `search_jobs`가 SQL 에러로 실패했다.
- **변경**: `mcp_server/server.py`(INSERT/ON CONFLICT/검색 조건/SELECT), `agents/state.py`
- **검증**: INSERT SQL을 트랜잭션 롤백으로 확인, MCP tool 5개 로딩 확인.

### #2 `pyproject.toml` 도입 + 의존성 설치
- **왜**: CLAUDE.md 규칙(uv + pyproject.toml). 기존엔 `requirements.txt`만 있고 `.venv`에 패키지가 없었다.
- **변경**: `pyproject.toml` 신규, `uv sync` 실행. (`requirements.txt`는 이후 삭제되어 의존성은 `pyproject.toml`/`uv.lock`으로만 관리)

### #3 `temperature` 인자 제거
- **왜**: `claude-sonnet-5-5`가 `temperature`를 받지 않아 400 에러(`temperature is deprecated for this model`).
- **변경**: `agents/ingest_graph.py`, `agents/query_graph.py`

### #4 적재용 LLM `max_tokens` 상향 + 적재 모델 Haiku 5.5로 변경
- **왜**: 기본 `max_tokens`에서 출력이 잘려 `ExtractionResult` 파싱 실패. 적재는 단순 구조화 추출이라 비용을 낮추려고 Haiku 5.5 사용.
- **변경**: `agents/ingest_graph.py` (`MODEL = "claude-haiku-5-5"`, `max_tokens=16_000`)
- **검증**: 현대오토에버 첫 적재 성공(42건). 이때 `scrape_runs`에 실패 2건 + 성공 1건이 남았다.

### #5 `companies.industry` (산업) 컬럼 추가
- **왜**: 회사별 소속 산업을 질의/추천에 활용하려고.
- **변경**
  - Neon DB: `ALTER TABLE companies ADD COLUMN IF NOT EXISTS industry TEXT` + 컬럼 COMMENT 적용, 기존 현대오토에버 행은 `IT서비스(자동차 SW)`로 갱신
  - `db/schema.sql` (컬럼, COMMENT, 기존 DB용 ALTER 안내)
  - `scrapers/companies.py` (`Company.industry`, 23개 회사 모두 값 부여)
  - `mcp_server/server.py` (`upsert_company(industry=...)`, `list_companies`/`search_jobs` 결과에 `industry` 포함)
  - `agents/ingest_graph.py` (`upsert_company` 호출 시 industry 전달)
- **주의**: 23개 회사의 산업 값은 임의로 분류한 초안이다(예: 한화 `방산/에너지/제조`, SK `통신/에너지/반도체`). 틀린 곳은 `scrapers/companies.py`에서 수정. `upsert_company`는 industry가 null로 오면 기존 값을 유지한다(`COALESCE`).
- **검증**: Neon `\d companies`로 컬럼 확인, MCP `list_companies`에 industry 노출 확인.

### #6 목록 텍스트에 링크(URL) 보존
- **왜**: `get_text()`가 `href`를 버려서 공고 URL이 42건 전부 NULL이었다. NULL URL은 UNIQUE 제약에서 서로 다른 값으로 취급되어 재실행 시 중복 적재된다.
- **변경**: `agents/ingest_graph.py`에 `page_to_text()` 추가 — 링크마다 `[절대URL]`을 앵커 텍스트 뒤에 붙인다(`javascript:`/`#`/`mailto:` 제외, `script`/`style` 제거).
- **검증**: 현대오토에버 목록에서 공고 URL 42개 전부 텍스트에 포함됨(LLM 호출 없이 확인). 텍스트는 5.7k → 8.9k자로 증가.

### #7 AI Engineer 관련 공고만 적재 (2단계 필터)
- **왜**: 보이는 공고를 전부 적재하고 있었다(현대오토에버 42건 중 AI 관련은 소수, 34건이 `Other`).
- **변경**
  - 1차(목록): `extract_postings` 프롬프트가 AI/ML/LLM/MLOps/데이터 직무만 후보로 추출. 제목·직군만으로 애매하면 포함.
  - 2차(JD 본문): `fetch_details`가 본문 기준으로 `is_ai_related`를 다시 판정해 아니면 저장하지 않음.
  - `category`는 `AI Engineer / ML Engineer / LLM Engineer / MLOps / Data`로 제한(`Literal`). `Backend`/`Other`는 더 이상 저장하지 않는다.
  - 파일: `agents/ingest_graph.py`, `agents/state.py`
- **알려진 한계**: 1차에서 빠진 공고는 2차에서 복구되지 않는다(재현율 손실 가능). 필터 기준은 프롬프트 의존이라 실제 실행 후 점검 필요.

### #8 공고 링크 방문 → JD 본문 파싱 → DB 적재
- **왜**: 목록 페이지에는 하는 일/필수/우대가 없어 `responsibilities`, `required_qualifications`, `preferred_qualifications`가 전부 NULL이었다.
- **변경**: `fetch_details` 노드 신설. 각 공고 URL을 GET(동시 4개, 상세 텍스트 20,000자 상한) → LLM(`JDDetail` 구조화)으로 3개 컬럼, 경력, 마감일을 파싱 → `source_snapshot`에 상세 텍스트 앞 2,000자 저장.
  - 상세 요청/파싱 실패 시: 그 공고만 목록 정보로 저장하고 건수를 `scrape_runs.detail`에 기록.
  - URL이 없는 공고: 목록 정보만 저장.
  - 그래프: `fetch_page → extract_postings → fetch_details → save_postings → finish`
  - 파일: `agents/ingest_graph.py`, `agents/state.py`
- **검증**: 현대오토에버 상세 페이지가 정적으로 받아지고 JD 본문이 포함됨을 확인(약 4.4k자). 그래프 컴파일 OK. **LLM을 포함한 실제 end-to-end 실행은 아직 안 함.**

### #9 기존 적재 데이터 삭제 후 새 파이프라인으로 현대오토에버 재적재
- **왜**: 이전 42건은 URL이 NULL이고 비AI 공고가 섞여 있어, 새 로직으로 적재하면 중복이 쌓인다.
- **작업**: `job_postings` 42건 삭제(`scrape_runs`는 유지) → `main_ingest.py 현대오토에버` 실행.
- **결과**: 목록 AI 후보 6건 → JD 확인 후 4건 저장(비AI 제외 2건, 상세 실패 0). 4건 모두 URL과 하는 일/필수/우대가 채워졌고 category는 `AI Engineer` 2, `LLM Engineer` 2.
- **검증**: DB에서 컬럼 값 직접 조회. 42건 → 4건으로 줄어든 것은 의도한 필터링이며, 1차에서 빠진 공고(재현율)는 아직 검증하지 않았다.

### #10 저장소 정리 및 GitHub 연동
- **왜**: 코드를 `hymn-fly/recruit-posting-agent`에 버전 관리하기 위해.
- **변경**: `.env.example`에 들어 있던 실제 DB 비밀번호와 Neon 설정 안내문을 placeholder로 교체, `CLAUDE.md`에 커밋/푸시 규칙 추가, git 초기화 후 원격 `main`(README 1개 커밋) 위에 커밋.
- **주의**: `.env.example`에 있던 비밀번호는 이 대화에서 노출됐으므로 Neon 콘솔에서 재발급(rotate)하고 `.env`를 갱신하는 것을 권장한다.

### #11 LLM을 Claude에서 Gemini API 경유 Gemma 4 31B로 교체
- **왜**: Claude 대신 Gemini API(Gemma 4 31B)를 쓰기로 결정.
- **변경**
  - `langchain-anthropic` 제거, `langchain-google-genai` 추가 (`pyproject.toml`, `uv.lock`)
  - `agents/ingest_graph.py`, `agents/query_graph.py`: `ChatAnthropic` → `ChatGoogleGenerativeAI`, 모델 `gemma-4-31b-it`, 출력 상한 인자 `max_tokens` → `max_output_tokens`
  - `.env.example`: `ANTHROPIC_API_KEY` → `GOOGLE_API_KEY` (`GEMINI_API_KEY`도 인식됨)
- **근거**: 모델 ID `gemma-4-31b-it`는 Google 공식 문서(Gemma on Gemini API)에 명시. 함수 호출과 시스템 지시는 지원, 컨텍스트 256K.
- **검증**: import 및 그래프 컴파일만 확인(더미 키, 네트워크 호출 없음). **실제 호출은 `GOOGLE_API_KEY`가 비어 있어 못 했다.**
- **위험**: Gemma의 구조화 출력(`with_structured_output`) 지원 여부는 공식 문서에 API 수준 근거가 없다. 실행 시 파싱 오류가 나면 JSON 모드/프롬프트 기반 파싱으로 바꿔야 한다. 출력 상한과 rate limit도 문서에 명시가 없다.

### #12 Gemma 실호출 검증 + Gemma/OpenAI 전환 인터페이스(10초 초과 시 gpt-6-luna)
- **왜**: Gemma 4 31B 실호출 결과 매우 느렸다. 작은 호출도 30~110초, `thinking_level="minimal"`로 줄여도 약 29초였고, 구조화 출력 호출은 간헐적으로 `500 INTERNAL`이 났다(구조화 출력 자체는 동작함). 그래서 Gemma가 10초 안에 응답하지 못하면 OpenAI로 넘기도록 했다.
- **변경**
  - `agents/llm.py` 신규:
    - `make_chat(model, max_output_tokens)` — 모델명 접두사로 제공자 선택(`gemma*`/`gemini*` → Google, `gpt*`/`o1`·`o3`·`o4` → OpenAI). 모델명만 바꿔 쓰는 단일 인터페이스.
    - `LLM(max_output_tokens, schema)` — `ainvoke(prompt)` 하나로 사용. 기본 `gemma-4-31b-it`가 10초 초과하거나 에러가 나면 `gpt-6-luna`로 전환(전환 시 재시도 3회, 경고 로그 출력). 모델명/타임아웃은 `llm.py` 상단 상수.
  - `agents/ingest_graph.py`, `agents/query_graph.py`: `LLM` 래퍼 사용. 질의 응답은 `msg.text` 사용(Gemma 응답 content가 블록 리스트라서).
  - `pyproject.toml`, `uv.lock`: `langchain-openai` 추가. `.env.example`: `OPENAI_API_KEY` 추가.
- **검증**
  - 가짜 모델로 3경우(느림/에러/정상)와 미지원 모델명 에러 확인.
  - `gpt-6-luna` 실호출 OK(일반 4.1초, 구조화 2.6초).
  - 현대오토에버 실적재 OK: 호출 7번 모두 Gemma 10초 초과로 OpenAI 전환, 전체 약 73초. 기존 4건은 URL 중복 판정으로 갱신, 1건 추가(총 5건). 목록 후보 6건 중 JD 확인 후 5건.
- **관찰**
  - 이번 실행에서는 Gemma가 한 번도 10초 안에 응답하지 못해 사실상 전부 OpenAI가 처리했다. 매 호출이 10초 대기를 먼저 소비한다.
  - 모델이 바뀌면서 분류가 달라졌다. `[Tech] RX 기술 리더(팀장)`(id 48, 하는 일 50자)이 새로 AI Engineer로 들어왔다. 이전 Gemma+Haiku 실행에서는 제외된 공고라 AI 관련성 점검이 필요하다.

### #13 Claude Desktop 예약 작업용 적재 구조: MCP 도구 추가, 회사 시딩, 프롬프트
- **왜**: 적재용 LLM(토큰 사용량이 큼)을 API 대신 Claude Desktop 예약 작업에서 Claude가 직접 수행하고, 로컬 MCP 서버(`job-db`)로 DB에 적재하는 방식으로 전환하기 위해.
- **변경**
  - `mcp_server/server.py`
    - `list_known_urls(company_name)` 신규 — 활성 공고 URL 목록. 이미 저장된 공고는 JD 재파싱을 건너뛰기 위함(토큰 절약).
    - `touch_postings(company_name, urls)` 신규 — 건너뛴 기존 공고의 `last_seen_at` 갱신.
    - `upsert_job_posting`에 `category` 검증 추가(`AI Engineer`/`ML Engineer`/`LLM Engineer`/`MLOps`/`Data` 외 거부) — Claude가 직접 쓸 때 잘못된 분류가 DB에 들어가는 것을 막는다.
  - `scripts/seed_companies.py` 신규 — `scrapers/companies.py`의 23개 회사를 DB에 시딩(LLM 호출 없음). **실행 완료: companies 23행.**
  - `prompts/daily_ingest.md` 신규 — 예약 작업 프롬프트(회사별 `access_method` 처리, 신규 공고만 JD 파싱, `record_scrape_run` 기록 규칙)와 Claude Desktop MCP 등록 방법.
- **검증**: MCP로 7개 tool 로딩, `list_companies` 23건, `list_known_urls`(현대오토에버 5건), `touch_postings`(1건 갱신), 잘못된 category 거부(DB에 미삽입) 확인.
- **미검증**: Claude Desktop에 실제 등록했을 때 예약 작업이 로컬 MCP 서버를 쓸 수 있는지, 그리고 예약 작업의 웹 접근/JS 렌더링 능력. 처음엔 `list_companies`만 호출하는 작은 작업으로 확인 필요.
- **참고**: 기존 LangGraph 적재 코드(`agents/ingest_graph.py` 등)는 그대로 둔다(API 방식 대안). 현대자동차/BCG의 URL은 여전히 `...`로 잘려 있다.

### #14 걸러낸 공고 기록 테이블(`filtered_postings`) + MCP 도구
- **왜**: LLM 필터가 얼마나 잘 거르는지(AI 공고를 놓치거나 잘못 거르는지) 확인하려면, 걸러진 공고와 그 이유가 남아 있어야 한다.
- **변경**
  - `db/schema.sql` + **Neon DB 적용 완료**: `filtered_postings` 테이블 신규(회사, 제목, URL, 근무지, `stage`=list|detail, `reason`, `is_actually_ai`(사람 검수용, NULL=미검수), 최초/최근 시각). UNIQUE (company_id, title, url)를 `NULLS NOT DISTINCT`로 걸어 URL이 없는 공고도 중복되지 않는다. 모든 컬럼에 COMMENT.
  - `mcp_server/server.py`
    - `record_filtered_postings(company_name, items)` 신규 — 여러 건을 한 번에 기록(목록 단계 제외가 회사당 수십 건이라 토큰 절약을 위해 배치 전용). 재기록 시 `stage/reason/last_seen_at`만 갱신하고 사람이 단 `is_actually_ai`는 보존.
    - `list_filtered_urls(company_name)` 신규 — 이미 걸러낸 URL을 매번 다시 읽지 않기 위함.
  - `prompts/daily_ingest.md`: 제외한 공고를 `record_filtered_postings`로 기록하는 단계(목록 단계/JD 단계)와 `list_filtered_urls` 사용 규칙 추가.
- **검증**: MCP 도구 9개 로딩, 같은 공고를 세 번 기록해도 행이 늘지 않음(URL 없는 행 포함), `detail`로 stage 갱신, 사람이 단 `is_actually_ai=true` 유지, 잘못된 stage 거부. 테스트 행은 삭제(현재 0행).
- **미적용**: 기존 LangGraph 적재 경로(`agents/ingest_graph.py`)는 이 테이블에 기록하지 않는다. 목록 단계에서 LLM이 제외 공고를 아예 출력하지 않는 구조라 별도 수정이 필요하다.
- **검수 방법 예시**: `select stage, count(*), count(*) filter (where is_actually_ai) as missed from filtered_postings where is_actually_ai is not null group by stage;`

## 2026-10-21

### #15 채용공고 조회 Agent RAG화 (Multi-Agent P0+P1) + LLM 서킷 브레이커
- **왜**: Multi-Agent(채용공고 조회 / 기업분석 / 내 프로필) 구성의 첫 단계. 키워드(ILIKE) 검색은 "LLM 에이전트 서비스 경험"처럼 서술형 요청을 공고의 하는 일·자격요건과 의미로 맞추지 못한다. 또 Gemma가 매번 타임아웃이라 호출마다 10~30초를 버리고 있었다.
- **변경**
  - `db/schema.sql` + **Neon 적용 완료**: `job_postings.embedding vector(768)`, `embedding_hash`(원문 sha256, 바뀌면 재임베딩), HNSW cosine 인덱스.
  - `mcp_server/embeddings.py` 신규: `gemini-embedding-001`(768차원, 문서/질의 task_type 구분). pgvector에는 텍스트(`'[..]'::vector`)로 넘겨 numpy 의존을 없앴다.
  - `mcp_server/server.py`: `embed_pending_postings`, `semantic_search_jobs`(임베딩 유사도 + 회사/직군/지역/최대 경력 필터, `similarity` 반환), `get_job_posting(id)` 신규. 어디서 실행해도 프로젝트 `.env`를 읽도록 경로를 고정했다.
  - `scripts/embed_postings.py` 신규: 기존 행 백필.
  - `agents/job_search_graph.py` 신규: `plan`(검색문 재작성 + 명시된 필터만) → `retrieve`(top-20) → `rerank_explain`(LLM이 최대 5건 선택·근거 작성, 결과에 없는 id는 버림. 답변 본문은 DB 값으로 코드가 조립). `agents/state.py`에 `JobSearchPlan`/`Ranking`/`JobSearchState` 추가.
  - `agents/llm.py`: 서킷 브레이커. 기본 모델이 실패하면 `PRIMARY_COOLDOWN`(300초) 동안 건너뛰고 바로 폴백한다(적재 그래프에도 적용됨).
  - `prompts/daily_ingest.md`: 마지막에 `embed_pending_postings` 호출 단계 추가.
  - `pyproject.toml`: `langchain-tavily`, `pypdf`, `python-docx` 추가(P2/P3용). `.env.example`: `TAVILY_API_KEY`.
- **검증**: 활성 공고 77건 임베딩, 재실행 시 0건(멱등). MCP 도구 12개 로딩. 질의 2개 — "LLM 에이전트 + RAG 파이프라인" → SK AI Agent 계열 4건 + CJ 1건(유사도 0.73~0.78), "MLOps 3년차 모델 서빙" → LG AI연구원 MLOps Engineer. 응답 시간은 서킷 브레이커 적용 전 55초에서 적용 후 12~26초.
- **미검증**: Claude Desktop 예약 작업에서 `embed_pending_postings` 호출(실행 환경에 `GOOGLE_API_KEY` 필요. `.env`에서 읽는다).

### #16 내 프로필 Agent (Multi-Agent P2)
- **왜**: 이력서·경력기술서·포트폴리오를 기억해 두고, 공고 대비 부족한 점과 보완 방법을 근거와 함께 알려주는 Agent가 필요하다.
- **변경**
  - `db/schema.sql` + **Neon 적용 완료**: `profile_documents`(파일 1개=1행, 파일명 유니크, 내용 hash), `profile_chunks`(약 800자/겹침 100자, 임베딩, HNSW), `profile_summary`(LLM 구조화 요약, 단일 행).
  - `scripts/ingest_profile.py` 신규: PDF(pypdf)/DOCX(python-docx, 표 포함)/MD/TXT 추출 → 문단 우선 청킹 → 임베딩 → 저장. 내용이 같으면 건너뛰고 바뀌면 조각까지 교체. 변경이 있으면 `profile_summary` 재생성. `--summary-only` 지원.
  - `mcp_server/profile_server.py` 신규(`profile-db`): `get_profile_summary`, `search_profile`, `list_profile_documents`(읽기 전용). `job-db`와 분리해 예약 작업에는 개인 자료가 노출되지 않게 했다. `agents/mcp_client.py`에 두 번째 서버로 등록.
  - `agents/profile_gap_graph.py` 신규: `load`(요약 없으면 적재 안내) → `decompose`(필수/우대 역량을 최대 12개 요구사항으로) → `match`(요구사항별 `search_profile` 병렬 + LLM 1회로 충족/부분/미충족 판정·원문 인용) → `synthesize`(강점/부족한 점/보완 방법 상위 4개/면접 질문 3개, 2500자 이내). 기업분석 리포트가 있으면 보완 방향에 반영.
  - `agents/state.py`: `ProfileSummary`, `Requirement(s)`, `Assessment(s)`, `ProfileGapState`. `.gitignore`: `data/`. `README.md`: 적재 방법·개인정보 주의.
- **검증**: 가상 인물 테스트 자료(MD 이력서 + DOCX 포트폴리오)로 적재 → 요약 생성, 같은 파일 재적재 시 "변경 없음" 건너뜀. 긴 텍스트 청킹(12조각, 겹침 일치). MCP 도구 15개 로딩. 공고 id 149(SK AI Agent 개발자)로 분석 → 12개 요구사항 판정, "경력 5년 이상" 필수 미충족(4년)과 Kubernetes 부족을 정확히 짚었고 근거 인용은 원문과 일치. 첫 실행 124초(Gemma 타임아웃 30초 포함), 분량 제한을 추가한 뒤 섹션 구성만 다시 확인했다(소요 시간은 재측정하지 않음).
- **테스트 데이터**: 가상 인물 자료는 P4 통합 테스트 뒤 삭제했다(#17).

### #17 기업분석 Agent + Supervisor 대화 CLI (Multi-Agent P3+P4)
- **왜**: 공고를 고른 뒤 회사가 속한 산업의 현황과 발전 방향, 경쟁사 대비 회사의 행보, 직무로 기여할 수 있는 점을 출처와 함께 조사해야 한다. 또 3개 Agent를 대화 하나에서 이어 쓸 수 있어야 한다(공고 찾기 → 번호로 선택 → 분석).
- **변경**
  - `db/schema.sql` + **Neon 적용 완료**: `company_research`(회사·공고, 토픽별 요약·출처 JSONB, 리포트).
  - `mcp_server/server.py`: `get_cached_research(company_name, max_age_days)`, `save_research` 신규.
  - `agents/company_research_graph.py` 신규: `load_cache` → `plan_queries`(산업·경쟁사 파악, 토픽별 검색어 2개) → `search_topic`(LangGraph `Send`로 토픽별 병렬 Tavily, 토픽마다 [n] 출처 요약) → `synthesize`(출처 번호를 전체 번호로 다시 매긴 뒤 산업 현황/발전 방향/경쟁사 vs 해당 기업/직무 기여 아이디어/한 줄 요약 리포트와 출처 목록 작성, 저장). 회사 단위 4개 토픽은 14일 캐시를 재사용하고, 직무 토픽과 리포트는 매번 새로 만든다. Tavily 클라이언트는 처음 쓸 때 생성한다(키가 없어도 다른 Agent는 동작).
  - `agents/supervisor.py` 신규: 구조화 라우터(`Route`: next/posting_number/posting_id/use_profile/reply/reason) → Agent 노드 → supervisor 복귀. 한 턴에 같은 Agent 1회, 최대 3개까지 실행. "2번 공고"는 추천 목록에서 찾아 `selected_posting`으로 정한다. 공고가 필요한데 선택되지 않았으면 되묻는다. 기업분석 리포트가 같은 공고 기준이면 gap 분석에 넘긴다. Agent마다 필요한 MCP 도구만 넘기고, Agent 실패는 메시지로 바꿔 대화가 끊기지 않게 했다. Route는 checkpointer 직렬화를 위해 dict로 저장한다.
  - `agents/main_chat.py` 신규: REPL(`MemorySaver`, `/select N`, `/id ID`, `/reset`, `/quit`), 라우팅 이유 로그 출력.
  - `agents/mcp_client.py`: `open_tools()` — 서버별 세션을 계속 열어 두는 컨텍스트 매니저(기존 `get_tools()`는 호출마다 서버 프로세스를 띄움).
  - `agents/state.py`: `ResearchPlan`/`TopicQueries`/`ResearchState`, `Route`/`AppState`. `README.md`/`PLAN.md`: 사용법·체크리스트.
- **검증**: 대화 4턴 — 인사 → FINISH 안내, "LLM 에이전트 개발 공고" → job_search 5건 후 FINISH, "2번 공고 기준 부족한 점" → id 149 선택 후 profile_gap, "기업분석" → company_research. `/id 154` 선택 후 기업분석 요청 시 키 안내 메시지. `get_cached_research`/`save_research` 저장·조회 후 테스트 행 삭제.
- **미검증 → #18에서 검증**: 이 시점에는 `TAVILY_API_KEY`가 없어 기업분석 리포트를 실제로 생성하지 못했다.
- **테스트 데이터 정리**: #16의 가상 인물 프로필(`test_resume.md`, `test_portfolio.docx`)과 요약을 삭제(`profile_*` 0행).

### #18 기업분석: 그룹 포털 공고의 실제 계열사 판별 + 인용된 출처만 표시
- **왜**: Tavily 키를 넣고 실제로 돌려보니, SK 포털의 티맵모빌리티 공고(id 154)를 분석하면서 산업을 "SK그룹 통신·에너지·반도체"로 잡았다. 또 캐시 키가 `companies` 행("SK")이라, 다음 SK AX 공고 분석 때 티맵의 산업 분석을 재사용할 상황이었다. SK/LG/현대/롯데/CJ처럼 한 포털에 여러 계열사가 섞인 회사는 모두 해당된다. 출처 목록도 인용 여부와 관계없이 50개를 전부 나열했다.
- **변경**
  - `db/schema.sql` + **Neon 적용 완료**: `company_research.target_company` 추가. 키 없이 저장된 첫 실행 1행은 캐시로 쓸 수 없어 삭제했다.
  - `agents/state.py`: `ResearchPlan.target_company`(실제 채용 법인/계열사).
  - `agents/company_research_graph.py`: 순서를 `plan_queries`(계열사 판별 + 5개 토픽 검색어) → `load_cache`(회사+계열사로 조회) → 캐시에 없는 토픽만 병렬 검색으로 바꿨다. 요약·리포트 프롬프트에 분석 대상 계열사를 명시하고, 리포트 제목을 계열사명으로 했다. 출처 목록에는 본문에 인용된 번호만 남긴다(원래 번호 유지).
  - `mcp_server/server.py`: `get_cached_research`/`save_research`에 `target_company` 인자 추가.
- **검증**(실제 Tavily 호출): id 154 → "티맵모빌리티"(경쟁사 카카오모빌리티·네이버지도 등) 79초. id 151 → "SK AX"(경쟁사 삼성SDS·LG CNS 등), 같은 SK 포털이지만 캐시 미적중(의도대로) 84초. id 154 재실행 → 회사 단위 토픽 4개 캐시 적중, role 토픽만 검색(Tavily 10회→2회). 출처 50개 → 인용된 22~28개.
- **관찰**: 캐시가 적중해도 소요 시간은 85초로 줄지 않았다. 최종 리포트 작성(LLM 1회, 긴 프롬프트)이 대부분이라 검색 절약은 시간보다 Tavily 쿼터·비용 절감 효과다.
- **DB 상태**: 위 검증의 리서치 3행(티맵 2, SK AX 1)은 실제 데이터라 캐시로 남겨 두었다.

### #19 README에 전체 동작 흐름 다이어그램(Mermaid) 추가
- **왜**: 적재(예약 작업)와 대화(Supervisor + 3개 Agent), MCP 서버 2개, 외부 API가 얽혀 있어 코드만으로는 전체 흐름을 파악하기 어렵다.
- **변경**: `README.md`에 "전체 동작 흐름" 섹션 추가 — ① 구성도(flowchart) ② 공고 적재 sequence ③ Supervisor 한 턴의 state 전이 ④ 대표 시나리오(공고 찾기 → 기업분석 → 부족한 점) sequence ⑤ 프로필 적재 sequence.
- **검증**: 다이어그램 내용을 현재 코드의 노드·MCP 도구 이름과 대조했다. mermaid-cli 11.4.2로 5개 모두 SVG 렌더링 성공(문법 오류 없음).

### #20 공고 임베딩을 하는 일 / 필수 / 우대 역량 컬럼별 벡터로 분리
- **왜**: 공고 1건을 벡터 1개(제목+직군+세 컬럼)로만 비교하면, "자격요건 기준으로 잘 맞는 공고"처럼 컬럼별로 따로 보거나 가중치를 줄 수 없다.
- **변경**
  - `db/schema.sql` + **Neon 적용 완료**: `job_postings.embedding_responsibilities` / `embedding_required` / `embedding_preferred`(vector 768) + 각 HNSW 인덱스. 기존 통합 `embedding`은 후보 선정·빈 컬럼 대체용으로 유지.
  - `mcp_server/server.py`
    - `embed_pending`: 공고당 통합 1개 + 컬럼별 최대 3개를 한 번에 임베딩. hash에 `EMBED_VERSION`을 섞어 방식이 바뀌면 전체가 다시 임베딩되게 했다.
    - `semantic_search_jobs`: 통합 벡터 HNSW로 후보 200건(`CANDIDATE_POOL`) → 컬럼별 유사도 가중합(`w_responsibilities=0.5, w_required=0.35, w_preferred=0.15`, 값이 없는 컬럼은 빼고 정규화, 셋 다 없으면 통합 유사도)으로 재정렬. 결과에 `sim_responsibilities`/`sim_required`/`sim_preferred` 추가.
  - `mcp_server/embeddings.py`: 80개 단위로 나눠 보내고, 429(RESOURCE_EXHAUSTED)면 60초 기다렸다가 최대 3회 재시도. Gemini 무료 티어 한도가 분당 임베딩 100건이라 270여 개를 한 번에 보내자 실패했다.
  - `scripts/embed_postings.py`: 한 번에 25건씩 처리(공고당 텍스트 최대 4개 → 약 100건).
- **결정: 컬럼 벡터에는 제목을 붙이지 않는다** — 처음에는 맥락을 주려고 제목을 앞에 붙였는데, 세 컬럼 유사도가 거의 같아졌다(예: 0.752/0.732/0.752). 공고 3건 × 질의 2개로 비교해 보니 본문만 임베딩할 때 컬럼 간 유사도 폭이 2~3배 컸다(id 149: 0.041 → 0.084). 제목은 통합 벡터에 들어 있다.
- **검증**: 활성 77건 — 통합 77, 하는 일 66/66, 필수 66/66, 우대 63/63(원문이 있는 컬럼 수와 일치). 재실행 시 0건(멱등). "Kubernetes 운영·모델 서빙 최적화" 질의에서 기본 가중치는 MLOps Engineer(id 97)가 1위, 필수 역량만(`w_required=1`) 보면 필수 유사도 0.791인 LLM Inference Engineer(id 92)가 1위로 바뀐다. MCP 경유 채용공고 조회 Agent 질의 2개 정상(33초, 13초).
- **참고**: 채용공고 조회 Agent는 아직 기본 가중치만 쓴다. 질문에 따라 가중치를 바꾸게 하려면 `JobSearchPlan`에 가중치 필드를 추가하면 된다.

### #21 질의 에이전트(`query_graph`) 검색을 키워드 일괄 매칭 → 제목 키워드 + 필드별 임베딩 cosine으로 전환
- **왜**: `query_graph`가 질문에서 키워드 1개만 뽑아 제목·하는 일·필수·우대 4개 컬럼에 `ILIKE`로 걸었다. 의미가 같은 표현("LLM 서비스 개발" vs "RAG 파이프라인 구축")을 못 찾고, 직무명 질문과 역량 질문이 구분되지 않았다.
- **변경**
  - `mcp_server/server.py` `search_jobs`: `keyword` 제거 → `title_keywords`(제목 `ILIKE ANY`, 하나라도 일치)와 `q_responsibilities`/`q_required`/`q_preferred`(각각 하는 일/필수/우대 컬럼 임베딩과 cosine 유사도) 추가. 질의가 주어진 컬럼 유사도의 평균(NULL 컬럼 제외)을 `similarity`로 정렬하고 `sim_*`도 반환, 질의 컬럼이 모두 비어 있는 공고는 제외. 질의가 없으면 기존처럼 최근 확인 순. 결과에 `id` 추가.
  - `agents/state.py` `SearchParams`: 위 필드로 교체.
  - `agents/query_graph.py` `plan_search`: 직무명이면 `title_keywords`, 하는 일/필수/우대 질문이면 해당 `q_*` 필드를 채우도록 프롬프트 변경.
- **검증**: 실제 Neon·Gemini로 `search_jobs` 직접 호출(제목만 / 하는 일 / 필수+우대+제목 / 조건 없음)과 MCP 경유 `query_graph` 3개 질의 — "백엔드 개발자" → `title_keywords=[백엔드, Backend]`, "RAG 파이프라인을 구축하는 일" → `q_responsibilities`(유사도 0.77), "PyTorch 3년+ / 쿠버네티스 우대" → `q_required`+`q_preferred`(0.74).
- **참고**: Supervisor의 조회 Agent(`job_search_graph`)는 이미 `semantic_search_jobs`를 쓰므로 변경 없음. 제목 키워드와 임베딩 질의는 AND로 결합된다.

### #22 질의 에이전트 프롬프트 문자열 SyntaxError 수정
- **왜**: #21에서 `plan_search` 프롬프트 예시를 `(예: "PyTorch 실무 경력 3년 이상")`처럼 큰따옴표로 감쌌는데, 바깥 문자열도 큰따옴표라 문자열이 중간에 끝나 `agents/main_query.py` 실행 시 import 단계에서 SyntaxError가 났다.
- **변경**: `agents/query_graph.py` — 예시의 안쪽 따옴표를 작은따옴표로 교체(같은 프롬프트의 `'개발자'` 표기와 통일).
- **검증**: `py_compile`로 agents/mcp_server/scripts 전체 컴파일 통과. `main_query.py "PyTorch 실무 경력 3년 이상 요구하는 ML 엔지니어 공고"` 실행 → SK 제조 AI 이미지 분류 ML 엔지니어 공고 답변(PyTorch 요구 여부가 공고에 없다는 점도 명시).

---

## 미해결 / 다음 후보
- Gemma가 거의 항상 타임아웃 → 서킷 브레이커로 낭비는 줄였지만(#15), 기본 모델 교체 검토는 여전히 필요
- id 48 `RX 기술 리더(팀장)`의 AI 관련성 점검 및 1차 필터의 재현율 점검(현대오토에버 목록에서 빠진 AI 공고가 없는지)
- URL 없는 공고의 중복 방지(`UNIQUE ... NULLS NOT DISTINCT` 또는 별도 키)
- `js_rendered` 회사 처리(브라우저 자동화 또는 잡포털 미러)
- 현대자동차/BCG의 잘린 URL(`...`) 보완
