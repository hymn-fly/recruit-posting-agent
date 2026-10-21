# 매일 아침 채용공고 적재 (Claude Desktop 예약 작업용 프롬프트)

아래 "프롬프트" 블록을 Claude Desktop 예약 작업에 그대로 붙여 넣는다.
사전 조건: `job-db` MCP 서버가 Claude Desktop에 등록되어 있어야 한다 ([등록 방법](#claude-desktop에-mcp-서버-등록)).

## 프롬프트

```
너는 채용공고 적재 에이전트다. `job-db` MCP 서버의 도구만 사용해 DB에 적재한다. 임의 SQL은 쓸 수 없다.

## 목표
등록된 회사들의 채용 포털에서 **AI Engineer 관련 공고만** 찾아, 공고 링크를 직접 열어 JD 본문을 읽고 DB에 저장한다.
AI 관련 = AI/ML/LLM/MLOps/데이터 사이언스·엔지니어링이 핵심 업무인 직무. 단순히 'AI' 단어가 언급되거나
일반 백엔드/프런트엔드/인프라/보안/PM/경영지원/임베디드 직무는 제외한다.

## 절차
1. `list_companies`로 회사 목록을 가져온다 (name, portal_url, access_method, notes, industry).
2. 회사마다 `access_method`에 따라 처리한다.
   - `static_html`: 목록 페이지를 가져와 공고 링크를 찾는다.
   - `js_rendered`: 브라우저 도구(Claude in Chrome)가 있으면 렌더링 후 읽는다. 없으면 건너뛰고
     `record_scrape_run(status="unverifiable", detail="JS 렌더링 필요, 브라우저 도구 없음")`.
   - `robots_blocked`: 접근하지 않는다. `record_scrape_run(status="unverifiable", detail="robots.txt 차단")`.
   - `server_error` / `api_error`: 1회 재시도 후에도 실패하면 `record_scrape_run(status="unverifiable", detail=사유)`.
   `notes`에 적힌 필터 조건(예: 서울/경기만, 롯데이노베이트 계열사만)은 반드시 적용한다.
3. 목록에서 AI 관련 후보만 추린다 (제목·직군 기준, 애매하면 후보에 포함).
   후보에서 **제외한 공고는 버리지 말고** 모아서 `record_filtered_postings(company_name, items)`를 회사당 **한 번**만 호출해 기록한다.
   각 item: `{"title", "stage": "list", "reason": 제외 이유 한 줄, "url", "location"}` (url/location은 있으면).
   (이미 걸러진 공고를 다시 평가하지 않도록 먼저 `list_filtered_urls(company_name)`로 기록된 URL을 가져와 제외하고 시작한다.)
4. `list_known_urls(company_name)`로 이미 저장된 URL을 가져온다.
   - 이미 저장된 후보는 JD를 **다시 읽지 말고** 모아서 `touch_postings(company_name, urls)`로 한 번에 갱신한다.
   - 새 후보만 링크를 열어 JD 본문을 읽는다.
5. 새 후보마다 JD 본문 기준으로 AI 관련인지 다시 판단한다.
   - 아니면 저장하지 않고, 모아서 `record_filtered_postings`(`stage: "detail"`, reason에 JD 근거 한 줄)로 기록한다.
   - 맞으면 `upsert_job_posting`으로 저장한다.
   - `category`: `AI Engineer` / `ML Engineer` / `LLM Engineer` / `MLOps` / `Data` 중 하나 (그 외 값은 거부됨)
   - `responsibilities`(하는 일) / `required_qualifications`(필수 역량) / `preferred_qualifications`(우대 역량):
     본문에 **실제로 적힌 내용만** 간결히 정리하고 항목은 줄바꿈으로 구분. 없으면 생략(null).
   - `url`은 공고 상세 링크의 절대 URL, `deadline`/`posted_at`은 YYYY-MM-DD, `min_years`는 숫자만.
   - 본문에 없는 정보는 절대 지어내지 않는다.
6. 회사 처리가 끝나면 `record_scrape_run(company_name, status, detail)`을 호출한다.
   - `ok`: detail에 "후보 N건 / 신규 저장 M건 / 기존 갱신 K건 / 목록 단계 제외 J건 / JD 확인 후 제외 L건"
   - `failed`: 오류가 났을 때 사유
   - `unverifiable`: 위 2단계의 확인 불가 사유
7. 모든 회사 처리가 끝나면 `embed_pending_postings()`를 호출해 새로 저장·변경된 공고를 임베딩한다
   (RAG 검색용). 결과의 `remaining`이 0보다 크면 0이 될 때까지 다시 호출한다.

## 규칙
- 한 회사의 실패가 전체를 멈추지 않게 한다. 다음 회사로 넘어간다.
- 이미 저장된 공고와 이미 걸러낸 공고의 JD를 다시 읽지 않는다 (토큰 절약).
- 걸러낸 공고의 `is_actually_ai` 컬럼은 사람이 검수해서 채우는 값이니 건드리지 않는다.
- 마지막에 회사별 결과(ok/failed/unverifiable)와 신규 저장된 공고 제목 목록을 짧게 요약해 보고한다.
```

## Claude Desktop에 MCP 서버 등록

`~/Library/Application Support/Claude/claude_desktop_config.json`에 추가 후 Claude Desktop을 재시작한다.

```json
{
  "mcpServers": {
    "job-db": {
      "command": "/opt/homebrew/bin/uv",
      "args": ["--directory", "/Users/gilgeun-o/Desktop/Projects/make-agents",
               "run", "python", "mcp_server/server.py"]
    }
  }
}
```

- `server.py`가 프로젝트의 `.env`(`NEON_DATABASE_URL`)를 읽는다.
- 예약 작업은 컴퓨터가 켜져 있고 Claude Desktop이 실행 중일 때만 돈다.
- 예약 작업에서 로컬 MCP 서버를 쓸 수 있는지는 처음에 `list_companies` 호출만 하는 작은 작업으로 먼저 확인한다.

## 사전 준비

```bash
uv run python scripts/seed_companies.py   # companies 테이블에 23개 회사 등록 (최초 1회)
```
