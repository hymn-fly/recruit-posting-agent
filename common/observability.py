"""애플리케이션 로그: 터미널(stderr) + 용량 제한 파일. 프롬프트·프로필 본문은 기록하지 않는다."""
import logging
import os
from contextvars import ContextVar
from logging.handlers import RotatingFileHandler
from pathlib import Path

request_id = ContextVar("request_id", default="-")


class RequestContext(logging.Filter):
    def filter(self, record):
        record.request_id = request_id.get()
        return True


def configure_logging(filename="app.log"):
    level = os.environ.get("APP_LOG_LEVEL", "INFO").upper()
    directory = Path(os.environ.get("APP_LOG_DIR", str(Path(__file__).resolve().parent.parent / "logs")))
    directory.mkdir(parents=True, exist_ok=True)
    formatter = logging.Formatter("%(asctime)s %(levelname)-7s [%(name)s] request=%(request_id)s %(message)s")
    console = logging.StreamHandler()  # MCP stdout는 JSON-RPC 전용이므로 stderr 사용
    file = RotatingFileHandler(directory / filename, maxBytes=5_000_000, backupCount=3, encoding="utf-8")
    for handler in (console, file):
        handler.setFormatter(formatter)
        handler.addFilter(RequestContext())
    
    for name in ("api", "agents", "mcp_server"):
        logger = logging.getLogger(name)
        for old in logger.handlers:
            old.close()
        logger.handlers = [console, file]
        logger.setLevel(level)
        logger.propagate = False
