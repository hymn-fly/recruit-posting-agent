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

---

## 미해결 / 다음 후보
- Gemma가 계속 10초를 넘기면 매 호출 10초 낭비 → 일정 시간 Gemma 건너뛰기(서킷 브레이커) 또는 기본 모델 교체 검토
- id 48 `RX 기술 리더(팀장)`의 AI 관련성 점검 및 1차 필터의 재현율 점검(현대오토에버 목록에서 빠진 AI 공고가 없는지)
- URL 없는 공고의 중복 방지(`UNIQUE ... NULLS NOT DISTINCT` 또는 별도 키)
- `js_rendered` 회사 처리(브라우저 자동화 또는 잡포털 미러)
- 현대자동차/BCG의 잘린 URL(`...`) 보완
