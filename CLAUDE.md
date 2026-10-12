## Conventions
- 파이썬 패키지 관리용으로는 uv를 사용해. 가상환경도 uv 를 통해 만들고, 의존성 관리 pyproject.toml 로 관리해
- 코드를 변경하는 작업지시를 받으면 `CHANGELOG.md`에 항목(무엇을/왜/변경 파일/검증)을 추가하고, 변경 내용을 목적별로 정리한 커밋 메시지(제목 + 본문)로 commit 후 `origin`(https://github.com/hymn-fly/recruit-posting-agent.git)에 push 해. `.env` 등 비밀 값은 절대 커밋하지 마
