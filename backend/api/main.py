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
from agents.graphs.supervisor import make_graph, input_to_agent_state
from agents.mcp_client import open_tools
from mcp_server import db
from scripts.ingest_profile import extract_text, ingest, summarize

log = logging.getLogger(__name__)
sessions: dict[str, asyncio.Lock] = {}
profile_lock = asyncio.Lock()


@asynccontextmanager
async def lifespan(app):
    configure_logging()
    log.info("event=startup_mcp_connecting")
    async with open_tools() as tools:
        app.state.graph = make_graph(tools, checkpointer=InMemorySaver())
        log.info("event=startup_mcp_connected tools=%s", sorted(tools))
        try:
            await db.pool()
            log.info("event=startup_db_connected")
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
    log.info("event=http_start method=%s path=%s", request.method, request.url.path)
    try:
        response = await call_next(request)
        response.headers["X-Request-ID"] = rid
        log.info("event=http_end status=%s elapsed_seconds=%.2f", response.status_code, time.perf_counter() - started)
        return response
    except Exception:
        log.exception("event=http_failed elapsed_seconds=%.2f", time.perf_counter() - started)
        raise
    finally:
        request_id.reset(token)



@app.exception_handler(Exception)
async def unexpected_error(request, exc):
    from fastapi.responses import JSONResponse
    log.exception("event=api_unhandled_error", exc_info=exc)
    return JSONResponse(status_code=500, content={"detail": "처리에 실패했습니다. 서버 로그와 API 키·DB 연결을 확인해 주세요."})


def config(sid):
    if sid not in sessions:
        raise HTTPException(404, "대화가 만료되었습니다. 새 대화를 시작해 주세요.")
    return {"configurable": {"thread_id": sid}}


async def snapshot(sid):
    state = (await app.state.graph.aget_state(config(sid))).values
    posting = state.get("selected_posting")
    same = posting and state.get("research_posting_id") == posting["id"]
    gap_report = ""
    if posting:
        gap_report = (state.get("gap_report", "") if state.get("gap_posting_id") == posting["id"]
                      else (state.get("gap_reports") or {}).get(str(posting["id"]), ""))
    return jsonable_encoder({
        "session_id": sid, "candidates": state.get("candidates", []),
        "selected_posting": posting,
        "research_report": state.get("research_report", "") if same else "",
        "gap_report": gap_report,
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
    log.info("event=chat_queued session=%s input_chars=%d", sid, len(body.text))
    
    async with sessions[sid]:
        log.info("event=chat_start session=%s lock_wait_seconds=%.2f", sid, time.perf_counter() - started)
        try:
            await app.state.graph.ainvoke(input_to_agent_state(body.text), cfg)
            await save_gap_report_of_session(sid)
            result = await snapshot(sid)
            log.info("event=chat_done session=%s candidates=%d messages=%d selected_posting_id=%s elapsed_seconds=%.2f",
                     sid, len(result["candidates"]), len(result["messages"]),
                     (result["selected_posting"] or {}).get("id"), time.perf_counter() - started)
            return result
        except asyncio.CancelledError:
            log.warning("event=chat_cancelled session=%s elapsed_seconds=%.2f", sid, time.perf_counter() - started)
            raise
        except Exception:
            log.exception("event=chat_failed session=%s elapsed_seconds=%.2f", sid, time.perf_counter() - started)
            raise


async def save_gap_report_of_session(sid: str) -> None:
    """세션에 방금 만들어진 역량 분석을 공고 id 기준으로 DB에 저장(공고당 최신 1건)."""
    state = (await app.state.graph.aget_state(config(sid))).values
    posting_id, report = state.get("gap_posting_id"), state.get("gap_report")
    if posting_id is None or not report:
        return

    connection_pool = await db.pool()
    await connection_pool.execute(
        "INSERT INTO posting_gap_reports (posting_id, report_md) VALUES ($1, $2) "
        "ON CONFLICT (posting_id) DO UPDATE SET report_md=EXCLUDED.report_md, updated_at=now() "
        "WHERE posting_gap_reports.report_md IS DISTINCT FROM EXCLUDED.report_md",
        posting_id, report)


@app.post("/sessions/{sid}/select/{posting_id}")
async def select_posting(sid: str, posting_id: int):
    cfg = config(sid)
    async with sessions[sid]:
        pool = await db.pool()
        row = await pool.fetchrow("SELECT j.id,j.title,j.url,j.location,j.deadline,j.min_years,j.responsibilities,j.required_qualifications,j.preferred_qualifications,c.name AS company,c.industry FROM job_postings j JOIN companies c ON c.id=j.company_id WHERE j.id=$1", posting_id)
        if not row:
            raise HTTPException(404, "공고를 찾지 못했습니다.")
        state = (await app.state.graph.aget_state(cfg)).values
        old = state.get("selected_posting") or {}
        update = {"selected_posting": jsonable_encoder(dict(row))}
        if old.get("id") != posting_id:
            update.update(research_report="", research_posting_id=None)
            cached = await pool.fetchval("SELECT report_md FROM company_research WHERE posting_id=$1 ORDER BY created_at DESC LIMIT 1", posting_id)
            if cached:
                update.update(research_report=cached, research_posting_id=posting_id)
        # 세션 메모리에 없는 공고의 역량 분석은 DB에 저장된 것을 불러온다
        gap_reports = dict(state.get("gap_reports") or {})
        in_memory = state.get("gap_posting_id") == posting_id or str(posting_id) in gap_reports
        if not in_memory:
            saved_gap_report = await pool.fetchval("SELECT report_md FROM posting_gap_reports WHERE posting_id=$1", posting_id)
            if saved_gap_report:
                update["gap_reports"] = {**gap_reports, str(posting_id): saved_gap_report}
        await app.state.graph.aupdate_state(cfg, update)
        return await snapshot(sid)


@app.get("/jobs")
async def jobs(search_text: str = Query("", alias="q"), active: bool = True,
               offset: int = Query(0, ge=0), limit: int = Query(10, ge=1, le=100)):
    pool = await db.pool()
    condition = "($1='' OR j.title ILIKE '%'||$1||'%' OR c.name ILIKE '%'||$1||'%') AND (NOT $2 OR (j.is_active AND (j.deadline IS NULL OR j.deadline >= (now() AT TIME ZONE 'Asia/Seoul')::date)))"
    total = await pool.fetchval(f"SELECT count(*) FROM job_postings j JOIN companies c ON c.id=j.company_id WHERE {condition}", search_text, active)
    rows = await pool.fetch(f"SELECT j.id,j.title,j.location,j.category,j.deadline,j.min_years,c.name AS company,c.industry FROM job_postings j JOIN companies c ON c.id=j.company_id WHERE {condition} ORDER BY j.first_seen_at DESC,j.id DESC LIMIT $3 OFFSET $4", search_text, active, limit, offset)
    return jsonable_encoder({"items": [dict(r) for r in rows], "total": total})


@app.get("/favorites")
async def favorites():
    connection_pool = await db.pool()
    rows = await connection_pool.fetch(
        "SELECT j.id,j.title,j.url,j.location,j.deadline,j.min_years,j.is_active,"
        "c.name AS company,c.industry,f.created_at AS saved_at "
        "FROM favorite_postings f "
        "JOIN job_postings j ON j.id=f.posting_id "
        "JOIN companies c ON c.id=j.company_id "
        "ORDER BY f.created_at DESC,j.id DESC"
    )
    return jsonable_encoder([dict(row) for row in rows])


@app.put("/favorites/{posting_id}")
async def add_favorite(posting_id: int):
    connection_pool = await db.pool()
    row = await connection_pool.fetchrow(
        "INSERT INTO favorite_postings (posting_id) "
        "SELECT id FROM job_postings WHERE id=$1 "
        "ON CONFLICT (posting_id) DO UPDATE SET posting_id=EXCLUDED.posting_id "
        "RETURNING posting_id", posting_id
    )
    if not row:
        raise HTTPException(404, "공고를 찾지 못했습니다.")
    return {"posting_id": row["posting_id"]}


@app.delete("/favorites/{posting_id}")
async def remove_favorite(posting_id: int):
    connection_pool = await db.pool()
    await connection_pool.execute("DELETE FROM favorite_postings WHERE posting_id=$1", posting_id)
    return {"posting_id": posting_id}


@app.get("/analyses")
async def list_analyses():
    """산업·기업조사나 역량 분석이 저장된 공고 목록 (최근 분석 순)."""
    connection_pool = await db.pool()
    rows = await connection_pool.fetch(
        "SELECT j.id,j.title,c.name AS company,c.industry,"
        "r.created_at AS research_saved_at,g.updated_at AS gap_saved_at "
        "FROM job_postings j JOIN companies c ON c.id=j.company_id "
        "LEFT JOIN LATERAL (SELECT created_at FROM company_research WHERE posting_id=j.id "
        "                   ORDER BY created_at DESC LIMIT 1) r ON true "
        "LEFT JOIN posting_gap_reports g ON g.posting_id=j.id "
        "WHERE r.created_at IS NOT NULL OR g.posting_id IS NOT NULL "
        "ORDER BY GREATEST(r.created_at, g.updated_at) DESC, j.id DESC"
    )
    return jsonable_encoder([dict(row) for row in rows])


@app.get("/postings/{posting_id}/analysis")
async def get_posting_analysis(posting_id: int):
    """한 공고의 저장된 산업·기업조사와 역량 분석. 없는 쪽은 빈 문자열."""
    connection_pool = await db.pool()
    research = await connection_pool.fetchrow(
        "SELECT report_md,created_at FROM company_research WHERE posting_id=$1 ORDER BY created_at DESC LIMIT 1", posting_id)
    gap = await connection_pool.fetchrow(
        "SELECT report_md,updated_at FROM posting_gap_reports WHERE posting_id=$1", posting_id)
    if not research and not gap:
        raise HTTPException(404, "저장된 분석이 없습니다.")
    return jsonable_encoder({
        "posting_id": posting_id,
        "research_report": research["report_md"] if research else "",
        "research_saved_at": research["created_at"] if research else None,
        "gap_report": gap["report_md"] if gap else "",
        "gap_saved_at": gap["updated_at"] if gap else None,
    })


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
    # 문서 변경 후 기존 역량 결과를 다시 보여주지 않는다. 저장된 분석도 낡았으므로 함께 지운다.
    connection_pool = await db.pool()
    await connection_pool.execute("DELETE FROM posting_gap_reports")
    for sid, lock in list(sessions.items()):
        async with lock:
            await app.state.graph.aupdate_state(config(sid), {"gap_report": "", "gap_posting_id": None, "gap_reports": {}})
    return {"message": result}
