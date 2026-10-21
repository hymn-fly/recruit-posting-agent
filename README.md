# recruit-posting-agent
이직준비를 위한 agent 구성

## 내 프로필 자료 넣기
```bash
mkdir -p data/profile   # gitignore됨. 원본 파일은 여기 둔다
uv run python scripts/ingest_profile.py data/profile/resume.pdf --kind resume
uv run python scripts/ingest_profile.py data/profile/career.docx --kind career
uv run python scripts/ingest_profile.py data/profile/portfolio.md --kind portfolio
```
- 지원 형식: `.pdf` `.docx` `.md` `.txt` (스캔 이미지 PDF는 텍스트가 없어 건너뜀)
- **개인정보 주의**: 추출한 텍스트는 Neon DB(`profile_*` 테이블)에 저장되고, 임베딩·요약을 위해 Gemini/OpenAI API로 전송된다.
  주민번호·연락처 등 불필요한 정보는 지우고 넣는 것을 권장한다.
- 개인 자료는 `profile-db` MCP 서버로만 조회된다(예약 작업용 `job-db`에는 노출되지 않음).

## Multi-Agent 대화 (채용공고 조회 · 기업분석 · 내 프로필)
```bash
uv run python agents/main_chat.py
> LLM 에이전트 개발하는 공고 찾아줘          # 채용공고 조회 Agent (임베딩 RAG)
> 2번 공고 회사 분석해줘                     # 기업분석 Agent (Tavily 웹 리서치, 출처 포함)
> 이 공고 기준으로 내가 부족한 점 알려줘      # 내 프로필 Agent (이력서 근거 gap 분석)
> 3번 공고 기업분석하고 내 부족한 점도        # 여러 Agent를 순서대로 실행
```
- 명령: `/select N`(추천 N번 선택), `/id ID`(공고 id로 선택), `/reset`, `/quit`
- 필요 키: `GOOGLE_API_KEY`(LLM·임베딩), `OPENAI_API_KEY`(폴백), `TAVILY_API_KEY`(기업분석), `NEON_DATABASE_URL`
- 새로 적재된 공고는 `embed_pending_postings`(예약 작업 마지막 단계) 또는 `uv run python scripts/embed_postings.py`로 임베딩해야 검색된다.
