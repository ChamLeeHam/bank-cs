# 人工工单：只保存已处理的客户消息和系统判断的验证状态
from langchain_core.messages import HumanMessage

from src.utils.mysql_utils import execute, query


def create_ticket(state, priority):
    messages = [str(m.content) for m in state.get("messages") or [] if isinstance(m, HumanMessage)]
    questions = messages[-3:] or [state.get("masked_question", "")]
    verified = bool(state.get("otp_verified") and state.get("otp_customer_id") == state.get("customer_id"))
    summary = "\n".join(f"客户：{q}" for q in questions if q)
    summary += f"\n身份验证：{'已通过' if verified else '未通过'}"
    sql = """
        insert into tickets (session_id, customer_id, intent, sub_intent, priority, summary)
        values (%s, %s, %s, %s, %s, %s)
    """
    result = execute(sql, (state.get("session_id"), state.get("customer_id"), state.get("intent") or "unknown",
                           state.get("sub_intent") or None, priority, summary))
    if result["rowcount"] != 1 or not result["lastrowid"]:
        raise ValueError("工单没有创建成功")
    return result["lastrowid"]


def list_tickets(status=None, priority=None, limit=100):
    """坐席演示：紧急工单优先，最多返回最近 100 条。"""
    sql = """
        select ticket_id, customer_id, intent, sub_intent, priority, status, summary,
               agent_reply, created_at, updated_at
        from tickets where 1 = 1
    """
    params = []
    if status:
        sql += " and status = %s"
        params.append(status)
    if priority:
        sql += " and priority = %s"
        params.append(priority)
    sql += " order by case when priority = 'urgent' then 0 else 1 end, created_at desc, ticket_id desc limit %s"
    params.append(min(max(limit, 1), 100))
    return query(sql, params)


def list_customer_tickets(customer_id, limit=50):
    """只返回当前客户的工单和对客户可见的回复。"""
    sql = """
        select ticket_id, status,
               case when status in ('replied', 'closed') then agent_reply else null end as agent_reply,
               created_at, updated_at
        from tickets where customer_id = %s
        order by created_at desc, ticket_id desc limit %s
    """
    return query(sql, (customer_id, min(max(limit, 1), 50)))


def update_ticket(ticket_id, expected_status, status, reply=None):
    """仅在状态未变化时更新，防止旧页面覆盖其他操作。"""
    sql = """
        update tickets set status = %s, agent_reply = coalesce(%s, agent_reply)
        where ticket_id = %s and status = %s
    """
    return execute(sql, (status, reply, ticket_id, expected_status))["rowcount"] == 1
