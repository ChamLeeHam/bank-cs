"""本机演示接口：uv run uvicorn src.api:app --host 127.0.0.1 --port 8001"""
import os
import asyncio
import logging
import json
import ipaddress
import hmac
import re
import threading
import urllib.error
import urllib.request
from contextlib import asynccontextmanager
from typing import Literal
from datetime import date
from pathlib import Path
from uuid import uuid4
from urllib.parse import urlsplit

from fastapi import Depends, FastAPI, HTTPException, Request, Response
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from langchain_core.messages import HumanMessage
from langsmith import tracing_context
from pydantic import BaseModel, Field

from src.nodes.loan_calculator import LoanComparison, compare_loan_plans
from src.nodes.stub_nodes import detect_lang, is_unsafe_customer_action, unsafe_action_reply
from src.schema.main_graph import get_conversation_language, is_customer_verified, resume, run_query
from src.schema.exceptions import MySQLError
from src.utils.ticket_utils import (
    claim_ticket_notification,
    fail_ticket_notification,
    mark_notification_recipient_sent,
    requeue_failed_notification,
    create_ticket,
    find_active_related_ticket,
    is_active_dispute_conflict,
    get_customer_ticket,
    list_customer_tickets,
    list_tickets,
    list_ticket_assignment_history,
    supervisor_assign_ticket,
    ticket_analytics,
    update_ticket,
)
from src.utils.ticket_assignment_schema import ensure_ticket_assignment_schema, ensure_ticket_dispute_constraint
from src.utils.ticket_notification_schema import ensure_ticket_notification_schema
from src.utils.ticket_sla import SLA_ALERT_RANK, SLA_LABELS, sla_level
from src.utils.ticket_sla_schema import ensure_ticket_sla_schema
from src.utils.service_feedback import parse_service_feedback, record_service_feedback, service_feedback_analytics
from src.utils.service_feedback_schema import ensure_service_feedback_schema
from src.utils.mysql_utils import execute, query, query_one
from src.schema.config import get_config
from src.utils.account_utils import get_recent_statement, get_statement_transaction, get_statement_transaction_detail
from src.utils.receipt_pdf import build_transaction_receipt
from src.intents import is_human_request
from src.utils.fraud_context import fraud_context
from src.utils import agent_auth

@asynccontextmanager
async def lifespan(_app):
    from src.schema.config import get_config

    await asyncio.to_thread(ensure_ticket_assignment_schema)
    await asyncio.to_thread(ensure_ticket_dispute_constraint)
    await asyncio.to_thread(ensure_ticket_notification_schema)
    await asyncio.to_thread(ensure_ticket_sla_schema)
    await asyncio.to_thread(ensure_service_feedback_schema)

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
automation_run_lock = threading.Lock()
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


class AgentUserCreate(AgentCredentials):
    email: str | None = Field(default=None, max_length=254,
                              pattern=r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


class AgentUserEmail(BaseModel):
    email: str = Field(min_length=3, max_length=254,
                       pattern=r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


class TicketAssignment(BaseModel):
    username: str = Field(min_length=3, max_length=32, pattern=r"^[A-Za-z0-9_.-]+$")


class AutomationRun(BaseModel):
    action: Literal["notify"]
    confirm: bool = False


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
    identity = agent_auth.identity(request.cookies.get("agent_session"))
    if not identity:
        raise HTTPException(status_code=401, detail="请先登录坐席账号。")
    return identity


def require_supervisor(identity=Depends(require_agent)):
    if identity.get("role") != "supervisor":
        raise HTTPException(status_code=403, detail="只有主管账号可以管理坐席和改派工单。")
    return identity

@app.post("/api/internal/tickets/{ticket_id}/claim-notification")
def claim_notification(ticket_id: int, request: Request):
    """供本机 n8n 认领待通知工单。"""
    _require_n8n_internal_token(request)

    if ticket_id <= 0:
        raise HTTPException(status_code=400, detail="工单编号无效。")

    try:
        ticket = query_one("""
            select ticket_id, assigned_agent, intent, priority,
                   notification_agent_sent_at, notification_supervisor_sent_at
            from tickets where ticket_id = %s
        """, (ticket_id,))
        if not ticket:
            raise HTTPException(status_code=404, detail="工单不存在。")
        emails = agent_auth.notification_emails(ticket.get("assigned_agent"))
        if not emails:
            raise HTTPException(status_code=503, detail="请先为主管账号配置通知邮箱。")
        claimed = claim_ticket_notification(ticket_id)
    except MySQLError:
        logger.exception("认领通知工单失败")
        raise HTTPException(status_code=503, detail="暂时无法认领工单。")

    return {"ticket_id": ticket_id, "claimed": claimed,
            "notify_agent": ticket.get("notification_agent_sent_at") is None,
            "notify_supervisor": ticket.get("notification_supervisor_sent_at") is None,
            "assigned_agent": ticket.get("assigned_agent"),
            "intent": ticket.get("intent"), "priority": ticket.get("priority"), **emails}


@app.post("/api/internal/tickets/{ticket_id}/complete-notification/{recipient}")
def complete_notification_recipient(ticket_id: int, recipient: Literal["agent", "supervisor"], request: Request):
    """Record one confirmed successful email; the ticket is sent after both recipients are recorded."""
    _require_n8n_internal_token(request)
    if ticket_id <= 0:
        raise HTTPException(status_code=400, detail="工单编号无效。")
    try:
        completed = mark_notification_recipient_sent(ticket_id, recipient)
        if not completed:
            raise HTTPException(status_code=409, detail="工单状态已变化，无法记录通知结果。")
        row = query_one("SELECT notification_status FROM tickets WHERE ticket_id = %s", (ticket_id,))
    except MySQLError:
        logger.exception("记录工单通知收件人送达失败")
        raise HTTPException(status_code=503, detail="暂时无法记录通知结果。")
    return {"ticket_id": ticket_id, "recipient": recipient,
            "notification_status": row["notification_status"] if row else None}


@app.post("/api/internal/tickets/{ticket_id}/fail-notification")
def fail_notification(ticket_id: int, request: Request):
    """Record a definite email-node failure while preserving any successful recipient."""
    _require_n8n_internal_token(request)
    if ticket_id <= 0:
        raise HTTPException(status_code=400, detail="工单编号无效。")
    try:
        failed = fail_ticket_notification(ticket_id)
    except MySQLError:
        logger.exception("记录工单通知失败状态失败")
        raise HTTPException(status_code=503, detail="暂时无法记录通知失败。")
    if not failed:
        raise HTTPException(status_code=409, detail="工单状态已变化，无法标记通知失败。")
    return {"ticket_id": ticket_id, "notification_status": "failed"}


@app.get("/api/internal/tickets/{ticket_id}/notification-recipients")
def notification_recipients(ticket_id: int, request: Request):
    """供 n8n 超时提醒读取工单对应的坐席与主管邮箱；只读，不改变工单状态。"""
    _require_n8n_internal_token(request)

    if ticket_id <= 0:
        raise HTTPException(status_code=400, detail="工单编号无效。")

    try:
        ticket = query_one("""
            select ticket_id, assigned_agent, intent, priority
            from tickets where ticket_id = %s
        """, (ticket_id,))
    except MySQLError:
        logger.exception("读取超时提醒收件人失败")
        raise HTTPException(status_code=503, detail="暂时无法读取工单通知收件人。")
    if not ticket:
        raise HTTPException(status_code=404, detail="工单不存在。")

    emails = agent_auth.notification_emails(ticket.get("assigned_agent"))
    if not emails:
        raise HTTPException(status_code=503, detail="请先为主管账号配置通知邮箱。")
    return {"ticket_id": ticket_id, "assigned_agent": ticket.get("assigned_agent"),
            "intent": ticket.get("intent"), "priority": ticket.get("priority"), **emails}


@app.post("/api/internal/tickets/{ticket_id}/claim-sla-alert/{level}")
def claim_sla_alert(ticket_id: int, level: Literal["pre_timeout", "timeout", "severe_timeout"], request: Request):
    """Atomically claim one SLA escalation so overlapping n8n polls do not duplicate alerts."""
    _require_n8n_internal_token(request)
    if ticket_id <= 0:
        raise HTTPException(status_code=400, detail="工单编号无效。")

    try:
        ticket = query_one("""
            select ticket_id, assigned_agent, intent, priority, status, created_at, sla_tracking_enabled,
                   sla_agent_notified_level, sla_supervisor_notified_level,
                   timestampdiff(SECOND, created_at, NOW()) as elapsed_seconds
            from tickets where ticket_id = %s
        """, (ticket_id,))
        if not ticket:
            raise HTTPException(status_code=404, detail="工单不存在。")
        current_level = sla_level(ticket.get("priority"), ticket.get("status"), ticket.get("elapsed_seconds"))
        if not ticket.get("sla_tracking_enabled") or current_level != level:
            return {"claimed": False, "ticket_id": ticket_id, "current_sla_level": current_level}

        target_rank = SLA_ALERT_RANK[level]
        notify_agent = SLA_ALERT_RANK.get(ticket.get("sla_agent_notified_level"), 0) < target_rank
        notify_supervisor = SLA_ALERT_RANK.get(ticket.get("sla_supervisor_notified_level"), 0) < target_rank
        if not notify_agent and not notify_supervisor:
            return {"claimed": False, "ticket_id": ticket_id, "current_sla_level": current_level}

        emails = agent_auth.notification_emails(ticket.get("assigned_agent"))
        if not emails:
            raise HTTPException(status_code=503, detail="请先为主管账号配置通知邮箱。")
        result = execute("""
            update tickets
            set sla_claimed_level = %s, sla_claimed_at = NOW()
            where ticket_id = %s and status = 'open' and sla_tracking_enabled = 1
              and priority = %s and created_at = %s
              and (
                coalesce(field(sla_agent_notified_level, 'pre_timeout', 'timeout', 'severe_timeout'), 0) < %s
                or coalesce(field(sla_supervisor_notified_level, 'pre_timeout', 'timeout', 'severe_timeout'), 0) < %s
              )
              and (sla_claimed_at is null or sla_claimed_at < NOW() - INTERVAL 10 MINUTE)
        """, (level, ticket_id, ticket.get("priority"), ticket.get("created_at"), target_rank, target_rank))
    except MySQLError:
        logger.exception("认领工单 SLA 提醒失败")
        raise HTTPException(status_code=503, detail="暂时无法认领工单超时提醒。")

    claimed = result["rowcount"] == 1
    return {"claimed": claimed, "ticket_id": ticket_id, "sla_level": level,
            "sla_label": SLA_LABELS[level], "elapsed_seconds": int(ticket.get("elapsed_seconds") or 0),
            "notify_agent": notify_agent, "notify_supervisor": notify_supervisor,
            "assigned_agent": ticket.get("assigned_agent"), "intent": ticket.get("intent"),
            "priority": ticket.get("priority"), **(emails if claimed else {})}


@app.post("/api/internal/tickets/{ticket_id}/complete-sla-alert/{level}/{recipient}")
def complete_sla_recipient_alert(
    ticket_id: int,
    level: Literal["pre_timeout", "timeout", "severe_timeout"],
    recipient: Literal["agent", "supervisor"],
    request: Request,
):
    """Persist one successful recipient so retries skip already-sent mail."""
    _require_n8n_internal_token(request)
    if ticket_id <= 0:
        raise HTTPException(status_code=400, detail="工单编号无效。")
    column = "sla_agent_notified_level" if recipient == "agent" else "sla_supervisor_notified_level"
    target_rank = SLA_ALERT_RANK[level]
    try:
        ticket = query_one(
            "SELECT sla_claimed_level, sla_agent_notified_level, sla_supervisor_notified_level "
            "FROM tickets WHERE ticket_id = %s", (ticket_id,)
        )
        if not ticket or ticket.get("sla_claimed_level") != level:
            raise HTTPException(status_code=409, detail="提醒认领已失效，请刷新工作流状态。")

        if SLA_ALERT_RANK.get(ticket.get(column), 0) < target_rank:
            execute(
                f"UPDATE tickets SET {column} = %s WHERE ticket_id = %s AND sla_claimed_level = %s",
                (level, ticket_id, level),
            )
        latest = query_one(
            "SELECT sla_agent_notified_level, sla_supervisor_notified_level "
            "FROM tickets WHERE ticket_id = %s", (ticket_id,)
        )
        all_sent = (
            SLA_ALERT_RANK.get(latest.get("sla_agent_notified_level"), 0) >= target_rank
            and SLA_ALERT_RANK.get(latest.get("sla_supervisor_notified_level"), 0) >= target_rank
        )
        if all_sent:
            execute("""
                UPDATE tickets
                SET sla_notified_level = %s, sla_claimed_level = NULL, sla_claimed_at = NULL
                WHERE ticket_id = %s AND sla_claimed_level = %s
            """, (level, ticket_id, level))
    except MySQLError:
        logger.exception("记录工单单方 SLA 提醒失败")
        raise HTTPException(status_code=503, detail="暂时无法记录工单超时提醒。")
    return {"completed": True, "ticket_id": ticket_id, "sla_level": level,
            "recipient": recipient, "all_recipients_completed": all_sent}


@app.post("/api/internal/tickets/{ticket_id}/complete-sla-alert/{level}")
def complete_sla_alert(ticket_id: int, level: Literal["pre_timeout", "timeout", "severe_timeout"], request: Request):
    """Backward-compatible completion endpoint for older workflow exports."""
    _require_n8n_internal_token(request)
    if ticket_id <= 0:
        raise HTTPException(status_code=400, detail="工单编号无效。")
    try:
        result = execute("""
            update tickets
            set sla_notified_level = %s, sla_agent_notified_level = %s,
                sla_supervisor_notified_level = %s, sla_claimed_level = null, sla_claimed_at = null
            where ticket_id = %s and sla_claimed_level = %s
        """, (level, level, level, ticket_id, level))
    except MySQLError:
        logger.exception("完成工单 SLA 提醒记录失败")
        raise HTTPException(status_code=503, detail="暂时无法记录工单超时提醒。")
    if result["rowcount"] != 1:
        raise HTTPException(status_code=409, detail="提醒认领已失效，请刷新工作流状态。")
    return {"completed": True, "ticket_id": ticket_id, "sla_level": level}


def _require_n8n_internal_token(request: Request):
    require_local(request)
    token = os.getenv("N8N_INTERNAL_TOKEN", "")
    supplied = request.headers.get("X-Internal-Token", "")
    if not token:
        raise HTTPException(status_code=503, detail="内部接口令牌未配置。")
    if not hmac.compare_digest(supplied, token):
        raise HTTPException(status_code=401, detail="内部接口认证失败。")


def _set_agent_cookie(response: Response, token: str, request: Request):
    response.set_cookie("agent_session", token, max_age=agent_auth.SESSION_SECONDS, httponly=True,
                        samesite="strict", secure=request.url.scheme == "https", path="/api/agent")


@app.get("/")
def index():
    react_index = WEB_DIR / "react" / "index.html"
    return FileResponse(react_index if react_index.is_file() else WEB_DIR / "index.html")


@app.post("/api/loan-plans/compare")
def compare_loan_plan_options(comparison: LoanComparison):
    """Estimate fixed-rate plans from supplied terms and rank them by total cost under the monthly budget."""
    return compare_loan_plans(comparison)


@app.get("/agent")
def agent_page():
    react_agent = WEB_DIR / "react" / "agent.html"
    return FileResponse(react_agent if react_agent.is_file() else WEB_DIR / "agent.html")


@app.get("/api/agent/auth/status")
def agent_auth_status(request: Request, response: Response):
    require_local(request)
    response.headers["Cache-Control"] = "no-store"
    identity = agent_auth.identity(request.cookies.get("agent_session"))
    return {"configured": agent_auth.is_configured(), "authenticated": bool(identity), "identity": identity}


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
    return {"ok": True, "identity": agent_auth.identity(token)}


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
    return {"ok": True, "identity": agent_auth.identity(token)}


@app.get("/api/agent/teams")
def agent_teams(authenticated: bool = Depends(require_agent)):
    return {"teams": {"customer_service": "公共坐席池"}}


@app.get("/api/agent/users")
def agent_users(authenticated=Depends(require_supervisor)):
    try:
        return {"users": agent_auth.list_users()}
    except (OSError, ValueError):
        logger.exception("坐席列表读取失败")
        raise HTTPException(status_code=503, detail="坐席列表暂时无法读取。")


@app.post("/api/agent/users")
def agent_user_create(payload: AgentUserCreate, request: Request,
                      authenticated=Depends(require_supervisor)):
    check_origin(request)
    if len(payload.password) < 8:
        raise HTTPException(status_code=400, detail="密码至少需要 8 个字符。")
    try:
        user = agent_auth.add_user(payload.username, payload.password, None,
                                   payload.email.strip().lower() if payload.email else None)
        return {"user": user}
    except FileExistsError:
        raise HTTPException(status_code=409, detail="这个坐席账号已存在。")
    except OSError:
        logger.exception("坐席账号创建失败")
        raise HTTPException(status_code=503, detail="无法保存坐席账号。")


@app.patch("/api/agent/users/{username}/email")
def agent_user_email_update(username: str, payload: AgentUserEmail, request: Request,
                            authenticated=Depends(require_supervisor)):
    check_origin(request)
    if not re.fullmatch(r"[A-Za-z0-9_.-]{3,32}", username):
        raise HTTPException(status_code=400, detail="坐席账号无效。")
    try:
        user = agent_auth.set_email(username, payload.email.strip().lower())
        if not user:
            raise HTTPException(status_code=404, detail="坐席账号不存在。")
        return {"user": user}
    except OSError:
        logger.exception("坐席邮箱保存失败")
        raise HTTPException(status_code=503, detail="无法保存通知邮箱。")


def _n8n_webhook_is_configured():
    config = get_config()
    value = config.n8n_automation_webhook_url
    if not value or not config.n8n_internal_token:
        return False
    try:
        parsed = urlsplit(value)
        port = parsed.port
    except ValueError:
        return False
    return (parsed.scheme == "http" and parsed.hostname in {"localhost", "127.0.0.1", "::1"}
            and port is not None and not parsed.username and not parsed.password
            and not parsed.query and not parsed.fragment)


@app.get("/api/agent/automation/preview")
def automation_preview(authenticated=Depends(require_supervisor)):
    """Read-only snapshot for supervisors; never triggers an n8n workflow."""
    try:
        counts = query("""
            select notification_status as status, count(*) as total
            from tickets
            where notification_status in ('pending', 'processing', 'sent', 'failed')
            group by notification_status
        """)
        pending = query("""
            select ticket_id, priority, intent, created_at, notification_status
            from tickets
            where notification_status = 'pending'
            order by created_at asc, ticket_id asc
            limit 25
        """)
        retryable_failed = query("""
            select ticket_id, priority, intent, notification_attempts,
                   notification_claimed_at, notification_agent_sent_at,
                   notification_supervisor_sent_at
            from tickets
            where notification_status = 'failed'
              and notification_retry_safe = 1
              and notification_attempts < 3
              and notification_claimed_at <= NOW() - INTERVAL 10 MINUTE
              and (notification_agent_sent_at is null or notification_supervisor_sent_at is null)
            order by notification_claimed_at, ticket_id
            limit 25
        """)
    except MySQLError:
        logger.exception("自动化预览读取失败")
        raise HTTPException(status_code=503, detail="暂时无法读取待通知工单。")
    totals = {str(row["status"]): int(row["total"]) for row in counts}
    return {"counts": totals, "pending_tickets": pending,
            "retryable_failed_tickets": retryable_failed,
            "n8n_configured": _n8n_webhook_is_configured()}


@app.post("/api/agent/tickets/{ticket_id}/retry-notification")
def retry_ticket_notification(ticket_id: int, request: Request,
                              authenticated=Depends(require_supervisor)):
    """Put a known failed, retry-safe notification back in the queue after supervisor confirmation."""
    check_origin(request)
    if ticket_id <= 0:
        raise HTTPException(status_code=400, detail="工单编号无效。")
    try:
        requeued = requeue_failed_notification(ticket_id)
    except MySQLError:
        logger.exception("重新排队工单通知失败")
        raise HTTPException(status_code=503, detail="暂时无法重新排队通知。")
    if not requeued:
        raise HTTPException(status_code=409,
                            detail="此工单暂不可重试：可能已送达、仍在冷却期、超过尝试上限，或属于旧版未跟踪记录。")
    return {"ticket_id": ticket_id, "notification_status": "pending",
            "message": "已重新加入待通知队列；已成功送达的收件人不会重复收到邮件。"}


@app.post("/api/agent/automation/run")
def automation_run(payload: AutomationRun, request: Request,
                   authenticated=Depends(require_supervisor)):
    check_origin(request)
    if not payload.confirm:
        raise HTTPException(status_code=400, detail="请先确认将由 n8n 发送待通知工单邮件。")
    if not _n8n_webhook_is_configured():
        raise HTTPException(status_code=503, detail="n8n 通知尚未配置；请在本机设置 Webhook 地址和内部令牌。")
    if not automation_run_lock.acquire(blocking=False):
        raise HTTPException(status_code=409, detail="通知流程正在触发，请稍候再查看执行结果。")
    try:
        config = get_config()
        req = urllib.request.Request(
            config.n8n_automation_webhook_url,
            data=json.dumps({"task": payload.action}, ensure_ascii=False).encode("utf-8"),
            headers={"Content-Type": "application/json", "X-Internal-Token": config.n8n_internal_token},
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=8) as response:
                if not 200 <= response.status < 300:
                    raise HTTPException(status_code=502, detail="n8n 未接受通知任务。")
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            logger.warning("触发 n8n 通知工作流失败 (%s)", type(exc).__name__)
            raise HTTPException(status_code=502, detail="无法连接 n8n；请检查服务状态后再试。")
        return {"accepted": True, "message": "通知流程已触发。邮件发送结果请在 n8n 执行记录中查看。"}
    finally:
        automation_run_lock.release()


@app.post("/api/agent/auth/logout")
def agent_logout(request: Request, response: Response):
    require_local(request)
    check_origin(request)
    agent_auth.logout(request.cookies.get("agent_session"))
    response.delete_cookie("agent_session", path="/api/agent", samesite="strict")
    return {"ok": True}


@app.get("/api/agent/tickets")
def agent_tickets(response: Response, status: Literal["open", "in_progress", "replied", "closed"] | None = None,
                  authenticated=Depends(require_agent),
                  priority: Literal["urgent", "normal"] | None = None,
                  start_date: date | None = None, end_date: date | None = None,
                  intent: str | None = None,
                  scope: Literal["queue", "mine", "team", "all"] | None = None,
                  assigned_agent: str | None = None):
    response.headers["Cache-Control"] = "no-store"
    if start_date and end_date and start_date > end_date:
        raise HTTPException(status_code=400, detail="开始日期不能晚于结束日期。")
    if intent and not re.fullmatch(r"[a-zA-Z0-9_]{1,60}", intent):
        raise HTTPException(status_code=400, detail="工单分类无效。")
    if assigned_agent and authenticated.get("role") != "supervisor":
        raise HTTPException(status_code=403, detail="只有主管可以筛选其他坐席的工单。")
    scope = scope or ("all" if authenticated.get("role") == "supervisor" else "queue")
    assigned_team = assigned_agent_name = None
    unassigned_only = False
    if authenticated.get("role") != "supervisor":
        # A seat can only read tickets allocated to its own account, regardless of client filters.
        assigned_agent_name = authenticated["username"]
    elif scope != "all":
        if scope == "queue":
            unassigned_only = True
        elif scope == "mine":
            assigned_agent_name = authenticated["username"]
        elif scope == "team":
            assigned_team = authenticated.get("team")
    try:
        return {"tickets": list_tickets(status=status, priority=priority, limit=500,
                                         start_date=start_date, end_date=end_date, intent=intent,
                                         assigned_team=assigned_team, assigned_agent=assigned_agent_name,
                                         unassigned_only=unassigned_only)}
    except MySQLError:
        logger.exception("读取工单失败")
        raise HTTPException(status_code=503, detail="工单暂时无法读取，请稍后重试。")


@app.get("/api/agent/reports")
def agent_reports(start_date: date, end_date: date, response: Response,
                  authenticated=Depends(require_agent)):
    response.headers["Cache-Control"] = "no-store"
    if start_date > end_date:
        raise HTTPException(status_code=400, detail="开始日期不能晚于结束日期。")
    if (end_date - start_date).days > 365:
        raise HTTPException(status_code=400, detail="报表日期范围最多为 366 天。")
    try:
        report = ticket_analytics(start_date, end_date,
                                  agent=None if authenticated.get("role") == "supervisor" else authenticated["username"])
        if authenticated.get("role") == "supervisor":
            report["feedback"] = service_feedback_analytics(start_date, end_date)
        return report
    except MySQLError:
        logger.exception("读取工单报表失败")
        raise HTTPException(status_code=503, detail="运营报表暂时无法读取，请稍后重试。")


@app.patch("/api/agent/tickets/{ticket_id}")
def agent_update_ticket(ticket_id: int, payload: TicketUpdate, request: Request,
                        authenticated=Depends(require_agent)):
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
        if not update_ticket(ticket_id, payload.expected_status, payload.status, reply or None, authenticated):
            raise HTTPException(status_code=409, detail="工单不存在或状态已变化，请刷新后重试。")
        return {"ok": True}
    except MySQLError:
        logger.exception("更新工单失败")
        raise HTTPException(status_code=503, detail="工单暂时无法更新，请稍后重试。")


@app.patch("/api/agent/tickets/{ticket_id}/assignment")
def agent_assign_ticket(ticket_id: int, payload: TicketAssignment, request: Request,
                        authenticated=Depends(require_supervisor)):
    check_origin(request)
    if payload.username:
        user = agent_auth.get_user(payload.username)
        if not user or user.get("role") != "agent":
            raise HTTPException(status_code=400, detail="所选账号不是坐席账号。")
    try:
        if not supervisor_assign_ticket(ticket_id, payload.username, authenticated["username"]):
            raise HTTPException(status_code=409, detail="工单不存在或已关闭，无法改派。")
        return {"ok": True}
    except MySQLError:
        logger.exception("改派工单失败")
        raise HTTPException(status_code=503, detail="暂时无法改派工单。")


@app.get("/api/agent/tickets/{ticket_id}/assignment-history")
def agent_ticket_assignment_history(ticket_id: int, response: Response,
                                    authenticated=Depends(require_agent)):
    response.headers["Cache-Control"] = "no-store"
    try:
        events = list_ticket_assignment_history(ticket_id, authenticated)
        if events is None:
            raise HTTPException(status_code=404, detail="没有找到这张工单。")
        return {"events": events}
    except MySQLError:
        logger.exception("读取工单派单记录失败")
        raise HTTPException(status_code=503, detail="派单记录暂时无法读取，请稍后重试。")


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


def _existing_dispute(ticket):
    ticket_id = ticket["ticket_id"]
    return {"ticket_id": ticket_id, "created": False,
            "answer": f"这笔交易已有核查工单 #{ticket_id}，请在「我的工单」查看处理进度。"}


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
            existing = find_active_related_ticket(session["customer_id"], transaction_id)
        except MySQLError:
            logger.exception("创建账单争议工单前读取记录失败")
            raise HTTPException(status_code=503, detail="账单暂时无法读取，请稍后重试。")

        response.headers["Cache-Control"] = "no-store"
        if existing:
            return _existing_dispute(existing)

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
        except MySQLError as exc:
            if is_active_dispute_conflict(exc):
                try:
                    existing = find_active_related_ticket(session["customer_id"], transaction_id)
                except MySQLError:
                    logger.exception("读取并发创建的争议工单失败")
                else:
                    if existing:
                        return _existing_dispute(existing)
            logger.exception("创建账单关联工单失败")
            raise HTTPException(status_code=503, detail="暂时无法创建核查工单，请稍后重试。")
        except ValueError:
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
        # 恢复挂起流程和直接查询工单都会跳过图的输入护栏。
        if (session["pending"] or TICKET_STATUS_QUESTION.search(message)) and is_unsafe_customer_action(message):
            lang = get_conversation_language(session_id, session["customer_id"]) or detect_lang(message)
            return {"answer": unsafe_action_reply(lang),
                    "pending": session["pending"], "sources": []}

        if session.get("pending") == "service_rating":
            parsed = parse_service_feedback(message)
            if parsed:
                feedback_status, rating = parsed
                try:
                    record_service_feedback(session_id, session["customer_id"], feedback_status, rating)
                except MySQLError:
                    logger.exception("保存会话服务评价失败")
                    raise HTTPException(status_code=503, detail="暂时无法保存评价，请稍后重试。")
                session["pending"] = None
                answer = ("感谢您的评价，祝您一切顺利！" if feedback_status == "rated"
                          else "好的，已结束本次会话。感谢使用狮城银行客服。")
                return {"answer": answer, "pending": None, "sources": []}
            # A new banking question takes priority; the rating invitation is optional.
            session["pending"] = None

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
            labels = {"open": "待处理", "in_progress": "处理中", "replied": "已有回复", "closed": "已关闭"}
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
        pending_type = pending.get("type")
        answer = result.get("answer", "")
        should_invite_rating = (result.get("intent") == "greeting"
                                and result.get("sub_intent") in ("thanks", "goodbye")
                                and not result.get("fraud_confirmation_pending"))
        if should_invite_rating:
            lang = result.get("lang", "zh")
            rating_prompt = ("Before you go, please rate this service from 1 to 5 (5 = very satisfied). You can also skip."
                             if lang == "en" else "结束前请为本次服务打分：1–5 分（5 分表示非常满意），也可以选择跳过。")
            answer = f"{answer}\n\n{rating_prompt}"
            pending_type = "service_rating"
        session["pending"] = pending_type
        return {"answer": answer, "pending": session["pending"],
                "sources": result.get("sources") or []}
    except HTTPException:
        raise
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
