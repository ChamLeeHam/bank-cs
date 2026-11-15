"""本机演示接口：uv run uvicorn src.api:app --host 127.0.0.1 --port 8001"""
import os
import asyncio
import logging
import json
import ipaddress
import hmac
import re
import threading
from contextlib import asynccontextmanager
from typing import Literal
from pathlib import Path
from uuid import uuid4
from urllib.parse import urlsplit

from fastapi import Depends, FastAPI, HTTPException, Request, Response
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from langchain_core.messages import HumanMessage
from langsmith import tracing_context
from pydantic import BaseModel, Field

from src.schema.main_graph import is_customer_verified, resume, run_query
from src.schema.exceptions import MySQLError
from src.utils.ticket_utils import (
    claim_ticket_notification,
    create_ticket,
    find_active_related_ticket,
    get_customer_ticket,
    list_customer_tickets,
    list_tickets,
    update_ticket,
)
from src.utils.mysql_utils import query_one
from src.utils.account_utils import get_recent_statement, get_statement_transaction, get_statement_transaction_detail
from src.utils.receipt_pdf import build_transaction_receipt
from src.intents import is_human_request
from src.utils.fraud_context import fraud_context
from src.utils import agent_auth

@asynccontextmanager
async def lifespan(_app):
    from src.schema.config import get_config

    if get_config().startup_warmup:
        try:
            from src.utils.startup_warmup import warmup_runtime

            await asyncio.to_thread(warmup_runtime)
        except Exception:
            logger.exception("服务启动预热异常，继续启动服务")
    yield


app = FastAPI(title="狮城银行客服演示", lifespan=lifespan)
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
TICKET_STATUS_QUESTION = re.compile(
    r"(?:工单.{0,10}(?:状态|进度|处理到哪|怎么样)|(?:查|查看|查询|看看|了解).{0,12}工单)|"
    r"(?:my\s+)?tickets?.{0,20}(?:status|progress|update)|(?:check|view).{0,12}(?:my\s+)?tickets?",
    re.IGNORECASE,
)


class NewSession(BaseModel):
    customer_id: str


class Message(BaseModel):
    text: str = Field(min_length=1, max_length=2000)


class DemoOtp(BaseModel):
    code: str = Field(pattern=r"^\d{6}$")


class TicketUpdate(BaseModel):
    expected_status: Literal["open", "in_progress", "replied", "closed"]
    status: Literal["in_progress", "replied", "closed"]
    reply: str | None = Field(default=None, max_length=2000)


class AgentCredentials(BaseModel):
    username: str = Field(min_length=3, max_length=32, pattern=r"^[A-Za-z0-9_.-]+$")
    password: str = Field(min_length=1, max_length=128)


def check_origin(request: Request):
    origin = request.headers.get("origin")
    if origin:
        value = urlsplit(origin)
        same_origin = value.scheme == request.url.scheme and value.netloc.lower() == request.url.netloc.lower()
        local_frontend = (value.scheme == request.url.scheme == "http" and
                          value.hostname in {"localhost", "127.0.0.1"} and value.port == 5173)
        if not (same_origin or local_frontend):
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

@app.post("/api/internal/tickets/{ticket_id}/claim-notification")
def claim_notification(ticket_id: int, request: Request):
    """供本机 n8n 认领待通知工单。"""
    require_local(request)

    token = os.getenv("N8N_INTERNAL_TOKEN", "")
    supplied = request.headers.get("X-Internal-Token", "")

    if not token:
        raise HTTPException(status_code=503, detail="内部接口令牌未配置。")

    if not hmac.compare_digest(supplied, token):
        raise HTTPException(status_code=401, detail="内部接口认证失败。")

    if ticket_id <= 0:
        raise HTTPException(status_code=400, detail="工单编号无效。")

    try:
        claimed = claim_ticket_notification(ticket_id)
    except MySQLError:
        logger.exception("认领通知工单失败")
        raise HTTPException(status_code=503, detail="暂时无法认领工单。")

    return {"ticket_id": ticket_id, "claimed": claimed}


def _set_agent_cookie(response: Response, token: str, request: Request):
    response.set_cookie("agent_session", token, max_age=agent_auth.SESSION_SECONDS, httponly=True,
                        samesite="strict", secure=request.url.scheme == "https", path="/api/agent")


@app.get("/")
def index():
    react_index = WEB_DIR / "react" / "index.html"
    return FileResponse(react_index if react_index.is_file() else WEB_DIR / "index.html")


@app.get("/agent")
def agent_page():
    react_agent = WEB_DIR / "react" / "agent.html"
    return FileResponse(react_agent if react_agent.is_file() else WEB_DIR / "agent.html")


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
def agent_tickets(response: Response, status: Literal["open", "in_progress", "replied", "closed"] | None = None,
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
    transitions = {
        "open": {"in_progress", "replied", "closed"},
        "in_progress": {"replied", "closed"},
        "replied": {"closed"},
        "closed": set(),
    }
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


@app.get("/api/sessions/{session_id}/tickets/{ticket_id}")
def customer_ticket_detail(session_id: str, ticket_id: int, request: Request, response: Response):
    """Read one ticket only after identity verification and customer ownership check."""
    require_local(request)
    session = get_demo_session(session_id)
    if not session["lock"].acquire(blocking=False):
        raise HTTPException(status_code=409, detail="上一项操作还在处理，请稍候。")
    try:
        if not session["ticket_verified"] and not is_customer_verified(session_id, session["customer_id"]):
            raise HTTPException(status_code=403, detail="请先完成演示身份验证。")
        response.headers["Cache-Control"] = "no-store"
        try:
            ticket = get_customer_ticket(session["customer_id"], ticket_id)
        except MySQLError:
            logger.exception("客户读取工单详情失败")
            raise HTTPException(status_code=503, detail="工单详情暂时无法读取，请稍后重试。")
        if not ticket:
            raise HTTPException(status_code=404, detail="没有找到这张工单。")
        return {"ticket": {
            "ticket_id": ticket["ticket_id"],
            "status": ticket["status"],
            "priority": ticket["priority"],
            "agent_reply": ticket["agent_reply"],
            "related_transaction": ticket["related_transaction"],
            "created_at": ticket["created_at"],
            "updated_at": ticket["updated_at"],
            "customer_view": ticket["customer_view"],
            "events": ticket["events"],
        }}
    finally:
        session["lock"].release()


@app.get("/api/sessions/{session_id}/statement")
def customer_statement(session_id: str, request: Request, response: Response):
    """返回当前演示客户的近期卡片交易和转账；必须先通过演示身份验证。"""
    require_local(request)
    session = get_demo_session(session_id)
    if not session["lock"].acquire(blocking=False):
        raise HTTPException(status_code=409, detail="上一项操作还在处理，请稍候。")
    try:
        if not session["ticket_verified"] and not is_customer_verified(session_id, session["customer_id"]):
            raise HTTPException(status_code=403, detail="请先完成演示身份验证。")
        response.headers["Cache-Control"] = "no-store"
        try:
            return {"transactions": get_recent_statement(session["customer_id"])}
        except MySQLError:
            logger.exception("读取演示账单失败")
            raise HTTPException(status_code=503, detail="账单暂时无法读取，请稍后重试。")
    finally:
        session["lock"].release()


@app.get("/api/sessions/{session_id}/statement/{transaction_id}/explanation")
def explain_statement_transaction(session_id: str, transaction_id: str, request: Request, response: Response):
    """从本地已验证的账单记录解释具体费用，避免把账户交易详情发送给外部模型。"""
    require_local(request)
    session = get_demo_session(session_id)
    if not session["lock"].acquire(blocking=False):
        raise HTTPException(status_code=409, detail="上一项操作还在处理，请稍候。")
    try:
        if not session["ticket_verified"] and not is_customer_verified(session_id, session["customer_id"]):
            raise HTTPException(status_code=403, detail="请先完成演示身份验证。")
        if not re.fullmatch(r"(?:CT|TT|PN|FT|GR)\d{8,12}", transaction_id):
            raise HTTPException(status_code=404, detail="没有找到这笔账单交易。")
        try:
            txn = get_statement_transaction(session["customer_id"], transaction_id)
        except MySQLError:
            logger.exception("读取单笔账单交易失败")
            raise HTTPException(status_code=503, detail="账单暂时无法读取，请稍后重试。")
        if not txn:
            raise HTTPException(status_code=404, detail="没有找到这笔账单交易。")

        state_text = txn.get("status_note_zh") or {
            "processing": "银行仍在处理这笔转账。", "completed": "这笔转账已完成。",
            "pending_review": "这笔转账正在审核中。", "failed": "这笔转账未成功。",
            "returned": "这笔转账已退回。", "posted": "这笔消费已入账。",
            "pending": "这笔消费仍在处理中。", "reversed": "这笔消费已撤销。",
        }.get(txn["status"], f"当前状态：{txn['status']}。")
        if txn["kind"] == "transfer" and txn["description"] == "Overseas TT":
            fee = txn.get("transfer_fee")
            if fee is not None and float(fee) == 10:
                answer = ("这笔账单记录的 SGD 10.00 是本行的海外电汇手续费；收费标准是每笔 SGD 10，"
                          f"因此记录金额与标准相符。{state_text}转账状态与这笔本行手续费是两项信息，"
                          "‘处理中’不表示又产生了一笔费用。中转行或收款行可能另收费用，但这条记录没有显示它们已扣款。")
            else:
                answer = (f"账单为这笔海外电汇记录了 SGD {fee or 0} 的手续费。"
                          f"{state_text}目前无法仅凭这条记录确认该金额为何产生；如需核对具体收费，可申请人工复核。")
            source = "来源：《费用与汇率》海外电汇（TT）手续费"
        elif txn["kind"] == "transfer":
            fee = float(txn.get("transfer_fee") or 0)
            if fee == 0:
                answer = f"这笔{txn['description']}转账记录没有本行手续费。{state_text}"
            else:
                answer = (f"这笔{txn['description']}转账记录了 SGD {fee:.2f} 的手续费。"
                          f"{state_text}如需核对该费用是否适用于您的办理渠道，可以申请人工复核。")
            source = "来源：本地演示账单记录"
        else:
            answer = (f"这条记录显示您在 {txn['description']} 有一笔 {txn['currency']} {txn['amount']} 的银行卡消费，"
                      f"状态为{state_text}账单数据没有为这笔消费单独列出手续费；若您指的是另一条费用记录，请选中那条记录。")
            source = "来源：本地演示账单记录"
        response.headers["Cache-Control"] = "no-store"
        return {"answer": f"{answer}\n\n{source}"}
    finally:
        session["lock"].release()


@app.get("/api/sessions/{session_id}/statement/{transaction_id}")
def statement_transaction_detail(session_id: str, transaction_id: str, request: Request, response: Response):
    """Load expanded details for a transaction in the verified customer's own statement."""
    require_local(request)
    session = get_demo_session(session_id)
    if not session["lock"].acquire(blocking=False):
        raise HTTPException(status_code=409, detail="上一项操作还在处理，请稍候。")
    try:
        if not session["ticket_verified"] and not is_customer_verified(session_id, session["customer_id"]):
            raise HTTPException(status_code=403, detail="请先完成演示身份验证。")
        if not re.fullmatch(r"(?:CT|TT|PN|FT|GR)\d{8,12}", transaction_id):
            raise HTTPException(status_code=404, detail="没有找到这笔账单交易。")
        try:
            transaction = get_statement_transaction_detail(session["customer_id"], transaction_id)
        except MySQLError:
            logger.exception("读取账单交易详情失败")
            raise HTTPException(status_code=503, detail="交易详情暂时无法读取，请稍后重试。")
        if not transaction:
            raise HTTPException(status_code=404, detail="没有找到这笔账单交易。")
        response.headers["Cache-Control"] = "no-store"
        return {"transaction": transaction}
    finally:
        session["lock"].release()


@app.get("/api/sessions/{session_id}/statement/{transaction_id}/receipt.pdf")
def download_statement_receipt(session_id: str, transaction_id: str, request: Request):
    """Generate and download a masked PDF receipt for a verified customer's transaction."""
    require_local(request)
    session = get_demo_session(session_id)
    if not session["lock"].acquire(blocking=False):
        raise HTTPException(status_code=409, detail="上一项操作还在处理，请稍候。")
    try:
        if not session["ticket_verified"] and not is_customer_verified(session_id, session["customer_id"]):
            raise HTTPException(status_code=403, detail="请先完成演示身份验证。")
        if not re.fullmatch(r"(?:CT|TT|PN|FT|GR)\d{8,12}", transaction_id):
            raise HTTPException(status_code=404, detail="没有找到这笔账单交易。")
        try:
            transaction = get_statement_transaction_detail(session["customer_id"], transaction_id)
            customer = query_one(
                "select customer_id, name_zh, name_en from customers where customer_id = %s",
                (session["customer_id"],),
            )
        except MySQLError:
            logger.exception("生成账单 PDF 前读取交易或客户信息失败")
            raise HTTPException(status_code=503, detail="交易回单暂时无法生成，请稍后重试。")
        if not transaction or not customer:
            raise HTTPException(status_code=404, detail="没有找到这笔账单交易。")
        try:
            pdf = build_transaction_receipt(transaction, customer)
        except Exception:
            logger.exception("生成演示交易回单 PDF 失败")
            raise HTTPException(status_code=503, detail="交易回单暂时无法生成，请稍后重试。")
        filename = f"LCB-Receipt-{transaction_id}.pdf"
        return Response(content=pdf, media_type="application/pdf", headers={
            "Content-Disposition": f'attachment; filename="{filename}"',
            "Cache-Control": "no-store",
            "X-Content-Type-Options": "nosniff",
        })
    finally:
        session["lock"].release()


@app.post("/api/sessions/{session_id}/statement/{transaction_id}/dispute")
def create_statement_dispute(session_id: str, transaction_id: str, request: Request, response: Response):
    """Create a ticket with a structured transaction link; never pass transaction details to the chat model."""
    require_local(request)
    check_origin(request)
    session = get_demo_session(session_id)
    if not session["lock"].acquire(blocking=False):
        raise HTTPException(status_code=409, detail="上一项操作还在处理，请稍候。")
    try:
        if not session["ticket_verified"] and not is_customer_verified(session_id, session["customer_id"]):
            raise HTTPException(status_code=403, detail="请先完成演示身份验证。")
        if not re.fullmatch(r"(?:CT|TT|PN|FT|GR)\d{8,12}", transaction_id):
            raise HTTPException(status_code=404, detail="没有找到这笔账单交易。")
        try:
            txn = get_statement_transaction(session["customer_id"], transaction_id)
            if not txn:
                raise HTTPException(status_code=404, detail="没有找到这笔账单交易。")
            existing = find_active_related_ticket(session_id, transaction_id)
        except MySQLError:
            logger.exception("创建账单争议工单前读取记录失败")
            raise HTTPException(status_code=503, detail="账单暂时无法读取，请稍后重试。")

        response.headers["Cache-Control"] = "no-store"
        if existing:
            return {"ticket_id": existing["ticket_id"], "created": False,
                    "answer": f"这笔交易已有核查工单 #{existing['ticket_id']}，请在「我的工单」查看处理进度。"}

        occurred_at = txn["occurred_at"]
        if hasattr(occurred_at, "isoformat"):
            occurred_at = occurred_at.isoformat(sep=" ", timespec="minutes")
        related_transaction = {
            "transaction_id": txn["transaction_id"],
            "kind": txn["kind"],
            "description": txn["description"],
            "amount": str(txn["amount"]),
            "currency": txn["currency"],
            "status": txn["status"],
            "occurred_at": occurred_at,
        }
        if txn.get("transfer_fee") is not None:
            related_transaction["fee_amount"] = str(txn["transfer_fee"])
            related_transaction["fee_currency"] = "SGD"

        request_text = "客户选择账单中的一笔交易申请人工核查，具体争议原因尚待坐席确认。"
        state = {
            "session_id": session_id,
            "customer_id": session["customer_id"],
            "intent": "dispute_refund",
            "sub_intent": "transaction_dispute",
            "lang": "zh",
            "masked_question": request_text,
            "messages": [HumanMessage(request_text)],
            "otp_verified": True,
            "otp_customer_id": session["customer_id"],
        }
        try:
            ticket_id = create_ticket(state, "normal", related_transaction=related_transaction)
        except (MySQLError, ValueError):
            logger.exception("创建账单关联工单失败")
            raise HTTPException(status_code=503, detail="暂时无法创建核查工单，请稍后重试。")
        return {
            "ticket_id": ticket_id,
            "created": True,
            "answer": (f"已为账单交易 {transaction_id} 创建核查工单 #{ticket_id}。"
                       "坐席可以直接查看关联交易；处理进度和回复可在「我的工单」查看。"),
        }
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
        # 查已有工单是只读账户查询，不应被误判为转人工并再次创建工单。
        if TICKET_STATUS_QUESTION.search(message):
            if not session["ticket_verified"] and not is_customer_verified(session_id, session["customer_id"]):
                return {"answer": "为保护账户，查看工单前需要验证身份。请点页面右上方「我的工单」，完成演示验证码验证后查看状态。",
                        "pending": None}
            try:
                tickets = list_customer_tickets(session["customer_id"])
            except MySQLError:
                logger.exception("客户查询工单状态失败")
                raise HTTPException(status_code=503, detail="工单状态暂时无法查询，请稍后重试。")
            if not tickets:
                return {"answer": "查询完成，您目前没有已创建的工单。", "pending": None}
            labels = {"open": "待处理", "replied": "已有回复", "closed": "已关闭"}
            latest = tickets[0]
            answer = (f"您最近一张工单是 #{latest['ticket_id']}，状态："
                      f"{labels.get(latest['status'], latest['status'])}。您共有 {len(tickets)} 张工单；"
                      "如需查看全部状态和坐席回复，请打开页面右上方「我的工单」。")
            return {"answer": answer, "pending": None}
        # 演示会话含账户信息，禁止通过外部链路追踪上传会话数据。
        with tracing_context(enabled=False):
            # 等待选择交易时，明显的新问题先结束当前选择，再作为新一轮处理。
            new_question = (session["pending"] in ("select_transfer", "transfer_scope") and
                            re.search(r"[?？]|帮我|我想|我要|查(询|一下)|\b(check|what|how|can i)\b", message, re.I))
            risk_interrupt = session["pending"] and (
                fraud_context(message)[0] == "reported" or is_human_request(message))
            if new_question or risk_interrupt:
                cancelled = resume(session_id, "取消", verbose=False)
                if cancelled.get("pending"):
                    raise RuntimeError("未能结束等待输入的操作")
                session["pending"] = None
                result = run_query(session_id, session["customer_id"], message, verbose=False)
            elif session["pending"]:
                result = resume(session_id, message, verbose=False)
            else:
                result = run_query(session_id, session["customer_id"], message, verbose=False)
        pending = result.get("pending") or {}
        session["pending"] = pending.get("type")
        return {"answer": result.get("answer", ""), "pending": session["pending"],
                "sources": result.get("sources") or []}
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
        yield event({"type": "done", "pending": result["pending"], "sources": result.get("sources") or []})

    return StreamingResponse(generate(), media_type="application/x-ndjson",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})
