# Career Workspace

Next.js + React + HeroUI v3 프론트와 기존 Python 에이전트를 연결한 개인용 웹앱입니다.

## 실행

프로젝트 루트에서:

```bash
uv sync
PYTHONPATH=backend:. uv run uvicorn api.main:app --host 127.0.0.1 --port 8000
```

다른 터미널에서:

```bash
cd frontend
npm ci
npm run build
npm start -- --hostname 127.0.0.1
```

브라우저: http://127.0.0.1:3000

개발 모드는 `npm run dev -- --hostname 127.0.0.1`입니다. FastAPI 주소가 다르면 `frontend/.env.local`의 `API_BASE_URL`을 지정한 뒤 프론트를 다시 빌드/시작하세요. 기본값은 `http://127.0.0.1:8000`입니다.

루트 `.env`는 기존 에이전트와 같은 DB·모델·임베딩 설정을 사용합니다. 산업·기업 조사는 `TAVILY_API_KEY`가 필요합니다. `mcp_server/sql/schema.sql` 및 기존 마이그레이션이 적용된 DB를 사용하세요. 앱은 자동으로 DB 스키마를 변경하지 않습니다.

## 사용 순서

1. 공고 목록에서 제목·회사명으로 검색하거나, 에이전트에게 자연어로 공고 추천을 요청합니다.
2. 공고 목록 또는 추천 카드의 **공고 확인**을 누릅니다.
3. 오른쪽 **산업·기업 조사** 탭에서 리포트를 생성합니다. 해당 공고의 기존 조사 결과가 있으면 먼저 표시합니다.
4. **내 문서**에서 이력서·포트폴리오를 업로드합니다(PDF/DOCX/MD/TXT, 최대 20MB).
5. 공고의 **내 역량 분석** 탭에서 등록 문서와 공고 요구사항을 비교합니다.
6. **수집 현황**에서 최근 50건의 사이트별 실행 기록을 확인합니다.

## 저장 범위와 운영 조건

- 기존 DB는 한 사람의 프로필을 공유합니다. 로그인이나 사용자별 데이터 분리가 없는 개인용 로컬 앱입니다.
- 문서는 텍스트·임베딩·요약으로 기존 프로필 테이블에 저장됩니다. 원본 파일 자체는 보관하지 않습니다. 같은 파일명은 교체되고, 업로드된 모든 문서가 분석 대상입니다.
- 스캔 PDF는 OCR을 제공하지 않습니다. 추출 텍스트가 없으면 업로드를 거부합니다.
- 문서 요약에 실패하면 문서가 저장된 상태일 수 있습니다. 같은 파일을 재업로드하면 요약을 다시 시도합니다.
- 대화와 역량 리포트는 서버 메모리에 유지됩니다. FastAPI 재시작 시 대화가 초기화됩니다. 산업·기업 리포트와 문서는 기존 DB에 저장됩니다.
- 분석은 요청이 끝나면 전체 결과를 표시합니다. 현재 토큰 스트리밍과 백그라운드 작업 재연결은 제공하지 않습니다.
- API와 프론트는 로컬 주소로 실행합니다. 외부 공개 전에는 인증·사용자별 프로필 분리·접근 제어가 필요합니다.

## UI 출처

`motor-fault-predictor`의 nextjs-axhub 기반 UI 구성(Shell, Providers, ax 래퍼, CSS 진입점)을 재사용했습니다. 원본 README는 템플릿을 MIT로 안내합니다. UI 패키지의 라이선스는 각 npm 패키지를 따릅니다. axhub SDK·로그인·Gateway·플랫폼 배포 코드와 모터 예측 모델은 포함하지 않았습니다.

## 처리 로그

FastAPI를 재시작하면 터미널과 `logs/app.log`에 요청별 로그가 남습니다. 각 HTTP 요청은 `request=<ID>`로 묶이며, 응답의 `X-Request-ID` 헤더에도 같은 ID가 포함됩니다. MCP 검색 서버의 DB 필터·임베딩·검색 결과 건수는 `logs/job-query.log`에 남습니다. 로그는 파일당 약 5MB, 이전 파일 3개까지 보관합니다.

프로젝트 루트의 별도 터미널에서:

```bash
tail -f logs/app.log logs/job-query.log
```

`chat.queued` → `chat.start` → `supervisor.start`/`route` → `plan.done` → `retrieve.start`/`retrieve.tool_done` → `rerank.start` → `chat.done` 순서로 확인하세요. `retrieve.empty`는 도구의 빈 응답 또는 오류 내용을, `search.empty`는 DB 필터를 통과한 공고가 없음을 뜻합니다. 실패는 스택 추적과 함께 기록합니다. LLM 호출은 모델·스키마·소요 시간·폴백 재시도를 기록하며 프롬프트와 이력서 원문은 기록하지 않습니다. 검색 조건·공고 식별 정보와 예외 메시지는 로그에 포함됩니다.

`APP_LOG_LEVEL`(기본 `INFO`), `APP_LOG_DIR`(기본 프로젝트 루트의 `logs/`)로 설정할 수 있습니다.
