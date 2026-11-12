"""本机演示接口：uv run uvicorn src.api:app --host 127.0.0.1 --port 8001"""
import logging
import json
import ipaddress
import hmac
import re
import threading
from typing import Literal
from pathlib import Path
from uuid import uuid4
from urllib.parse import urlsplit

from fastapi import Depends, FastAPI, HTTPException, Request, Response
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from langsmith import tracing_context
from pydantic import BaseModel, Field

from src.schema.main_graph import is_customer_verified, resume, run_query
from src.schema.exceptions import MySQLError
from src.utils.ticket_utils import list_customer_tickets, list_tickets, update_ticket
from src.utils.mysql_utils import query_one
from src.utils import agent_auth

app = FastAPI(title="狮城银行客服演示")
logger = logging.getLogger(__name__)
WEB_DIR = Path(__file__).parent / "web"
app.mount("/static", StaticFiles(directory=WEB_DIR), name="static")


@app.middleware("http")
async def revalidate_demo_assets(request: Request, call_next):
    """演示代码更新后让浏览器重新验证页面与脚本，避免加载旧版本。"""
    response = await call_next(request)
    if request.url.path in ("/", "/agent") or request.url.path.startswith("/static/"):
        response.headers["Cache-Control"] = "no-cache"
    return response


# 演示会话只在当前进程中保存；客户 ID 由服务端绑定，不接受对话中切换
sessions = {}
sessions_lock = threading.Lock()
DEMO_CUSTOMERS = {"C001", "C002", "C003"}


class NewSession(BaseModel):
    customer_id: str


class Message(BaseModel):
    text: str = Field(min_length=1, max_length=2000)


class DemoOtp(BaseModel):
    code: str = Field(pattern=r"^\d{6}$")


class TicketUpdate(BaseModel):
    expected_status: Literal["open", "replied", "closed"]
    status: Literal["replied", "closed"]
    reply: str | None = Field(default=None, max_length=2000)


class AgentCredentials(BaseModel):
    username: str = Field(min_length=3, max_length=32, pattern=r"^[A-Za-z0-9_.-]+$")
    password: str = Field(min_length=1, max_length=128)


def check_origin(request: Request):
    origin = request.headers.get("origin")
    if origin:
        value = urlsplit(origin)
        if value.scheme != request.url.scheme or value.netloc.lower() != request.url.netloc.lower():
            raise HTTPException(status_code=403, detail="请求来源不允许。")


def require_local(request: Request):
    host = request.client.host if request.client else ""
    try:
        local_peer = ipaddress.ip_address(host).is_loopback
    except ValueError:
        local_peer = host == "testclient"  # FastAPI 测试客户端没有真实网络地址
    allowed_hosts = {"localhost", "127.0.0.1", "::1"}
    if host == "testclient":
        allowed_hosts.add("testserver")
    if not local_peer or request.url.hostname not in allowed_hosts:
        raise HTTPException(status_code=403, detail="演示数据仅允许本机访问。")


def require_agent(request: Request):
    require_local(request)
    if not agent_auth.is_authenticated(request.cookies.get("agent_session")):
        raise HTTPException(status_code=401, detail="请先登录坐席账号。")
    return True


def _set_agent_cookie(response: Response, token: str, request: Request):
    response.set_cookie("agent_session", token, max_age=agent_auth.SESSION_SECONDS, httponly=True,
                        samesite="strict", secure=request.url.scheme == "https", path="/api/agent")


@app.get("/")
def index():
    return FileResponse(WEB_DIR / "index.html")


@app.get("/agent")
def agent_page():
    return FileResponse(WEB_DIR / "agent.html")


@app.get("/api/agent/auth/status")
def agent_auth_status(request: Request, response: Response):
    require_local(request)
    response.headers["Cache-Control"] = "no-store"
    return {"configured": agent_auth.is_configured(),
            "authenticated": agent_auth.is_authenticated(request.cookies.get("agent_session"))}


@app.post("/api/agent/auth/setup")
def agent_setup(credentials: AgentCredentials, request: Request, response: Response):
    require_local(request)
    check_origin(request)
    if len(credentials.password) < 10:
        raise HTTPException(status_code=400, detail="密码至少需要 10 个字符。")
    try:
        agent_auth.setup(credentials.username, credentials.password)
    except FileExistsError:
        raise HTTPException(status_code=409, detail="坐席账号已经设置，请直接登录。")
    except OSError:
        logger.exception("创建坐席账号失败")
        raise HTTPException(status_code=503, detail="无法保存账号，请检查本地存储。")
    token, error = agent_auth.login(credentials.username, credentials.password, request.client.host)
    if error:
        raise HTTPException(status_code=503, detail="账号已创建，请重新登录。")
    _set_agent_cookie(response, token, request)
    return {"ok": True}


@app.post("/api/agent/auth/login")
def agent_login(credentials: AgentCredentials, request: Request, response: Response):
    require_local(request)
    check_origin(request)
    token, error = agent_auth.login(credentials.username, credentials.password, request.client.host)
    if error == "locked":
        raise HTTPException(status_code=429, detail="尝试次数过多，请 5 分钟后再试。")
    if error == "unavailable":
        raise HTTPException(status_code=503, detail="坐席账号不可用，请检查是否已设置。")
    if error:
        raise HTTPException(status_code=401, detail="账号或密码不正确。")
    _set_agent_cookie(response, token, request)
    return {"ok": True}


@app.post("/api/agent/auth/logout")
def agent_logout(request: Request, response: Response):
    require_local(request)
    check_origin(request)
    agent_auth.logout(request.cookies.get("agent_session"))
    response.delete_cookie("agent_session", path="/api/agent", samesite="strict")
    return {"ok": True}


@app.get("/api/agent/tickets")
def agent_tickets(response: Response, status: Literal["open", "replied", "closed"] | None = None,
                  authenticated: bool = Depends(require_agent),
                  priority: Literal["urgent", "normal"] | None = None):
    response.headers["Cache-Control"] = "no-store"
    try:
        return {"tickets": list_tickets(status=status, priority=priority)}
    except MySQLError:
        logger.exception("读取工单失败")
        raise HTTPException(status_code=503, detail="工单暂时无法读取，请稍后重试。")


@app.patch("/api/agent/tickets/{ticket_id}")
def agent_update_ticket(ticket_id: int, payload: TicketUpdate, request: Request,
                        authenticated: bool = Depends(require_agent)):
    check_origin(request)
    transitions = {"open": {"replied", "closed"}, "replied": {"closed"}, "closed": set()}
    if payload.status not in transitions[payload.expected_status]:
        raise HTTPException(status_code=400, detail="该状态不能进行此操作，请刷新工单。")
    reply = (payload.reply or "").strip()
    if payload.status == "replied" and not reply:
        raise HTTPException(status_code=400, detail="请先填写处理回复。")
    try:
        if not update_ticket(ticket_id, payload.expected_status, payload.status, reply or None):
            raise HTTPException(status_code=409, detail="工单不存在或状态已变化，请刷新后重试。")
        return {"ok": True}
    except MySQLError:
        logger.exception("更新工单失败")
        raise HTTPException(status_code=503, detail="工单暂时无法更新，请稍后重试。")


@app.post("/api/sessions")
def new_session(request: NewSession):
    if request.customer_id not in DEMO_CUSTOMERS:
        raise HTTPException(status_code=400, detail="请选择演示客户。")
    session_id = str(uuid4())
    with sessions_lock:
        sessions[session_id] = {"customer_id": request.customer_id, "pending": None, "lock": threading.Lock(),
                                "ticket_verified": False, "ticket_otp_attempts": 0}
    return {"session_id": session_id}


def get_demo_session(session_id):
    with sessions_lock:
        session = sessions.get(session_id)
    if session is None:
        raise HTTPException(status_code=404, detail="会话已失效，请重新开始。")
    return session


@app.post("/api/sessions/{session_id}/tickets/verify")
def verify_tickets(session_id: str, payload: DemoOtp, request: Request):
    require_local(request)
    check_origin(request)
    session = get_demo_session(session_id)
    if not session["lock"].acquire(blocking=False):
        raise HTTPException(status_code=409, detail="上一项操作还在处理，请稍候。")
    try:
        if session["ticket_verified"] or is_customer_verified(session_id, session["customer_id"]):
            return {"ok": True}
        if session["ticket_otp_attempts"] >= 3:
            raise HTTPException(status_code=429, detail="验证次数已用完，请开始新的演示对话。")
        try:
            row = query_one("SELECT demo_otp FROM customers WHERE customer_id = %s", (session["customer_id"],))
        except MySQLError:
            raise HTTPException(status_code=503, detail="验证暂时不可用，请稍后重试。")
        if row is None or not hmac.compare_digest(payload.code, str(row["demo_otp"])):
            session["ticket_otp_attempts"] += 1
            left = 3 - session["ticket_otp_attempts"]
            raise HTTPException(status_code=401 if left else 429,
                                detail=f"验证码不正确，还可尝试 {left} 次。" if left else "验证次数已用完，请开始新的演示对话。")
        session["ticket_verified"] = True
        return {"ok": True}
    finally:
        session["lock"].release()


@app.get("/api/sessions/{session_id}/tickets")
def customer_tickets(session_id: str, request: Request, response: Response):
    require_local(request)
    session = get_demo_session(session_id)
    if not session["lock"].acquire(blocking=False):
        raise HTTPException(status_code=409, detail="上一项操作还在处理，请稍候。")
    try:
        if not session["ticket_verified"] and not is_customer_verified(session_id, session["customer_id"]):
            raise HTTPException(status_code=403, detail="请先完成演示身份验证。")
        response.headers["Cache-Control"] = "no-store"
        try:
            return {"tickets": list_customer_tickets(session["customer_id"])}
        except MySQLError:
            raise HTTPException(status_code=503, detail="工单暂时无法读取，请稍后重试。")
    finally:
        session["lock"].release()


@app.post("/api/sessions/{session_id}/messages")
def send_message(session_id: str, request: Message):
    session = get_demo_session(session_id)
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
