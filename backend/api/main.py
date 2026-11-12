"""개인용 웹 API. uv run uvicorn api.main:app --host 127.0.0.1 --port 8000"""
import asyncio
import logging
import tempfile
import time
from contextlib import asynccontextmanager
from pathlib import Path
from uuid import uuid4

from fastapi import FastAPI, File, Form, HTTPException, Query, UploadFile
from fastapi.encoders import jsonable_encoder
from langgraph.checkpoint.memory import InMemorySaver
from pydantic import BaseModel, Field

from common.observability import configure_logging, request_id
from agents.graphs.supervisor import make_graph, turn_input
from agents.mcp_client import open_tools
from mcp_server import db
from scripts.ingest_profile import extract_text, ingest, summarize

log = logging.getLogger(__name__)
sessions: dict[str, asyncio.Lock] = {}
profile_lock = asyncio.Lock()


@asynccontextmanager
async def lifespan(app):
    configure_logging()
    log.info("startup: MCP 도구 연결 시작")
    async with open_tools() as tools:
        app.state.graph = make_graph(tools, checkpointer=InMemorySaver())
        log.info("startup: MCP 연결 완료 tools=%s", sorted(tools))
        try:
            await db.pool()
            log.info("startup: DB 연결 완료")
            yield
        finally:
            if db._pool is not None:
                await db._pool.close()
                db._pool = None


app = FastAPI(title="Career Workspace", lifespan=lifespan)


@app.middleware("http")
async def log_requests(request, call_next):
    rid = uuid4().hex[:12]
    token = request_id.set(rid)
    started = time.perf_counter()
    log.info("http.start method=%s path=%s", request.method, request.url.path)
    try:
        response = await call_next(request)
        response.headers["X-Request-ID"] = rid
        log.info("http.end status=%s elapsed=%.2fs", response.status_code, time.perf_counter() - started)
        return response
    except Exception:
        log.exception("http.failed elapsed=%.2fs", time.perf_counter() - started)
        raise
    finally:
        request_id.reset(token)



@app.exception_handler(Exception)
async def unexpected_error(request, exc):
    from fastapi.responses import JSONResponse
    log.exception("API 처리 실패", exc_info=exc)
    return JSONResponse(status_code=500, content={"detail": "처리에 실패했습니다. 서버 로그와 API 키·DB 연결을 확인해 주세요."})


def config(sid):
    if sid not in sessions:
        raise HTTPException(404, "대화가 만료되었습니다. 새 대화를 시작해 주세요.")
    return {"configurable": {"thread_id": sid}}


async def snapshot(sid):
    state = (await app.state.graph.aget_state(config(sid))).values
    posting = state.get("selected_posting")
    same = posting and state.get("research_posting_id") == posting["id"]
    return jsonable_encoder({
        "session_id": sid, "candidates": state.get("candidates", []),
        "selected_posting": posting,
        "research_report": state.get("research_report", "") if same else "",
        "gap_report": state.get("gap_report", "") if posting and state.get("gap_posting_id") == posting["id"] else "",
        "messages": [{"role": "user" if m.type == "human" else "assistant", "content": m.content}
                     for m in state.get("messages", []) if m.type in ("human", "ai")],
    })


@app.post("/sessions")
async def create_session():
    sid = str(uuid4())
    sessions[sid] = asyncio.Lock()
    return await snapshot(sid)


@app.get("/sessions/{sid}")
async def get_session(sid: str):
    return await snapshot(sid)


class Message(BaseModel):
    text: str = Field(min_length=1, max_length=12000)


@app.post("/sessions/{sid}/messages")
async def chat(sid: str, body: Message):
    cfg = config(sid)
    started = time.perf_counter()
    log.info("chat.queued session=%s input_chars=%d", sid, len(body.text))
    
    async with sessions[sid]:
        log.info("chat.start session=%s lock_wait=%.2fs", sid, time.perf_counter() - started)
        try:
            await app.state.graph.ainvoke(turn_input(body.text), cfg)
            result = await snapshot(sid)
            log.info("chat.done session=%s candidates=%d messages=%d selected=%s elapsed=%.2fs",
                     sid, len(result["candidates"]), len(result["messages"]),
                     (result["selected_posting"] or {}).get("id"), time.perf_counter() - started)
            return result
        except asyncio.CancelledError:
            log.warning("chat.cancelled session=%s elapsed=%.2fs", sid, time.perf_counter() - started)
            raise
        except Exception:
            log.exception("chat.failed session=%s elapsed=%.2fs", sid, time.perf_counter() - started)
            raise


@app.post("/sessions/{sid}/select/{posting_id}")
async def select_posting(sid: str, posting_id: int):
    cfg = config(sid)
    async with sessions[sid]:
        connection_pool = await db.pool()
        row = await connection_pool.fetchrow("SELECT j.id,j.title,j.url,j.location,j.deadline,j.min_years,j.responsibilities,j.required_qualifications,j.preferred_qualifications,c.name AS company,c.industry FROM job_postings j JOIN companies c ON c.id=j.company_id WHERE j.id=$1", posting_id)
        if not row:
            raise HTTPException(404, "공고를 찾지 못했습니다.")
        state = (await app.state.graph.aget_state(cfg)).values
        old = state.get("selected_posting") or {}
        update = {"selected_posting": jsonable_encoder(dict(row))}
        if old.get("id") != posting_id:
            update.update(research_report="", research_posting_id=None, gap_report="")
            cached = await connection_pool.fetchval("SELECT report_md FROM company_research WHERE posting_id=$1 ORDER BY created_at DESC LIMIT 1", posting_id)
            if cached:
                update.update(research_report=cached, research_posting_id=posting_id)
        await app.state.graph.aupdate_state(cfg, update)
        return await snapshot(sid)


@app.get("/jobs")
async def jobs(search_text: str = Query("", alias="q"), active: bool = True, offset: int = 0):   # URL은 ?q= 그대로
    connection_pool = await db.pool()
    condition = "($1='' OR j.title ILIKE '%'||$1||'%' OR c.name ILIKE '%'||$1||'%') AND (NOT $2 OR (j.is_active AND (j.deadline IS NULL OR j.deadline >= (now() AT TIME ZONE 'Asia/Seoul')::date)))"
    total = await connection_pool.fetchval(f"SELECT count(*) FROM job_postings j JOIN companies c ON c.id=j.company_id WHERE {condition}", search_text, active)
    rows = await connection_pool.fetch(f"SELECT j.id,j.title,j.location,j.category,j.deadline,j.min_years,c.name AS company,c.industry FROM job_postings j JOIN companies c ON c.id=j.company_id WHERE {condition} ORDER BY j.first_seen_at DESC,j.id DESC LIMIT 30 OFFSET $3", search_text, active, max(offset, 0))
    return jsonable_encoder({"items": [dict(r) for r in rows], "total": total})


@app.get("/collection")
async def collection():
    connection_pool = await db.pool()
    rows = await connection_pool.fetch("SELECT source,status,detail,run_at FROM scrape_runs ORDER BY run_at DESC LIMIT 50")
    return jsonable_encoder([dict(r) for r in rows])


@app.get("/documents")
async def documents():
    connection_pool = await db.pool()
    rows = await connection_pool.fetch("SELECT id,kind,filename,updated_at FROM profile_documents ORDER BY updated_at DESC")
    return jsonable_encoder([dict(r) for r in rows])


@app.post("/documents")
async def upload(file: UploadFile = File(...), kind: str = Form(...)):
    if kind not in ("resume", "portfolio", "career", "other"):
        raise HTTPException(422, "문서 종류를 확인해 주세요.")
    name = Path((file.filename or "").replace("\\", "/")).name
    if Path(name).suffix.lower() not in (".pdf", ".docx", ".md", ".txt"):
        raise HTTPException(422, "PDF, DOCX, MD, TXT 파일을 올려 주세요.")
    content = await file.read(20 * 1024 * 1024 + 1)
    await file.close()
    if len(content) > 20 * 1024 * 1024:
        raise HTTPException(413, "파일은 20MB 이하로 올려 주세요.")
    async with profile_lock:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / name
            path.write_bytes(content)
            try:
                text = await asyncio.to_thread(extract_text, path)
            except Exception as exc:
                raise HTTPException(422, "문서를 읽지 못했습니다. 파일 형식과 내용을 확인해 주세요.") from exc
            if not text:
                raise HTTPException(422, "추출할 텍스트가 없습니다. 스캔 PDF는 텍스트 문서로 변환해 주세요.")
            result = await ingest(path, kind)
            try:
                await summarize()
            except Exception as exc:
                raise HTTPException(503, "문서는 저장됐지만 프로필 요약에 실패했습니다. 같은 파일을 다시 업로드해 주세요.") from exc
    # 문서 변경 후 기존 역량 결과를 다시 보여주지 않는다.
    for sid, lock in list(sessions.items()):
        async with lock:
            await app.state.graph.aupdate_state(config(sid), {"gap_report": ""})
    return {"message": result}
