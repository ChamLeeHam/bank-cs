"""本机演示接口：uv run uvicorn src.api:app --host 127.0.0.1 --port 8001"""
import logging
import json
import re
import threading
from pathlib import Path
from uuid import uuid4

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from langsmith import tracing_context
from pydantic import BaseModel, Field

from src.schema.main_graph import resume, run_query

app = FastAPI(title="狮城银行客服演示")
logger = logging.getLogger(__name__)
WEB_DIR = Path(__file__).parent / "web"
app.mount("/static", StaticFiles(directory=WEB_DIR), name="static")

# 演示会话只在当前进程中保存；客户 ID 由服务端绑定，不接受对话中切换
sessions = {}
sessions_lock = threading.Lock()
DEMO_CUSTOMERS = {"C001", "C002", "C003"}


class NewSession(BaseModel):
    customer_id: str


class Message(BaseModel):
    text: str = Field(min_length=1, max_length=2000)


@app.get("/")
def index():
    return FileResponse(WEB_DIR / "index.html")


@app.post("/api/sessions")
def new_session(request: NewSession):
    if request.customer_id not in DEMO_CUSTOMERS:
        raise HTTPException(status_code=400, detail="请选择演示客户。")
    session_id = str(uuid4())
    with sessions_lock:
        sessions[session_id] = {"customer_id": request.customer_id, "pending": None, "lock": threading.Lock()}
    return {"session_id": session_id}


@app.post("/api/sessions/{session_id}/messages")
def send_message(session_id: str, request: Message):
    with sessions_lock:
        session = sessions.get(session_id)
    if session is None:
        raise HTTPException(status_code=404, detail="会话已失效，请重新开始。")
    message = request.text.strip()
    if not message:
        raise HTTPException(status_code=400, detail="请输入内容。")
    if not session["lock"].acquire(blocking=False):
        raise HTTPException(status_code=409, detail="上一条消息还在处理，请稍候。")
    try:
        # 演示会话含账户信息，禁止通过外部链路追踪上传会话数据。
        with tracing_context(enabled=False):
            # 等待选择交易时，明显的新问题先结束当前选择，再作为新一轮处理。
            new_question = (session["pending"] in ("select_transfer", "transfer_scope") and
                            re.search(r"[?？]|帮我|我想|我要|查(询|一下)|\b(check|what|how|can i)\b", message, re.I))
            if new_question:
                resume(session_id, "取消", verbose=False)
                session["pending"] = None
                result = run_query(session_id, session["customer_id"], message, verbose=False)
            elif session["pending"]:
                result = resume(session_id, message, verbose=False)
            else:
                result = run_query(session_id, session["customer_id"], message, verbose=False)
        pending = result.get("pending") or {}
        session["pending"] = pending.get("type")
        return {"answer": result.get("answer", ""), "pending": session["pending"]}
    except Exception:
        logger.exception("演示会话处理失败")
        raise HTTPException(status_code=500, detail="处理失败，请检查本地服务后重试。")
    finally:
        session["lock"].release()


@app.post("/api/sessions/{session_id}/messages/stream")
def stream_message(session_id: str, request: Message):
    """先完成回答及检查，再分段发送，避免提前显示未经检查的内容。"""
    def event(value):
        return json.dumps(value, ensure_ascii=False) + "\n"

    def generate():
        yield event({"type": "status", "text": "正在处理…"})
        try:
            result = send_message(session_id, request)
        except HTTPException as exc:
            yield event({"type": "error", "message": exc.detail})
            return
        answer = result["answer"] or "暂时没有收到回复，请重试。"
        for start in range(0, len(answer), 12):
            yield event({"type": "delta", "text": answer[start:start + 12]})
        yield event({"type": "done", "pending": result["pending"]})

    return StreamingResponse(generate(), media_type="application/x-ndjson",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})
