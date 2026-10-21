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
