# 人工工单：只保存已处理的客户消息和系统判断的验证状态
from langchain_core.messages import HumanMessage

from src.utils.mysql_utils import execute


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
