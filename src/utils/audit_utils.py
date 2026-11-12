# 每轮结束后记录运行路径；不保存验证码、工具参数或账户查询结果
import json
import time

from src.utils.mysql_utils import execute


def write_turn_log(state):
    started = state.get("turn_started_at")
    latency_ms = max(0, int((time.time() - started) * 1000)) if started is not None else None
    # 工具参数可能包含客户信息，只记工具名
    calls = [{"tool": c.get("tool", "")} for c in state.get("tool_calls") or []]
    answer = "[账户结果已隐藏]" if state.get("route") == "account" else state.get("answer", "")
    sql = """
        insert into turn_log (session_id, customer_id, question_masked, lang, intent, sub_intent,
                              intent_score, router_backend, decision, route, trace, tool_calls, answer, latency_ms)
        values (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
    """
    question = "[私人业务问题已隐藏]" if state.get("route") in ("account", "handoff") or state.get("private_input") else state.get("masked_question", "")
    return execute(sql, (state.get("session_id"), state.get("customer_id"), question,
                         state.get("lang") or "en", state.get("intent") or None, state.get("sub_intent") or None,
                         state.get("intent_score"), state.get("router_backend") or None,
                         state.get("decision"), state.get("route"), json.dumps(state.get("trace") or []),
                         json.dumps(calls), answer, latency_ms))
