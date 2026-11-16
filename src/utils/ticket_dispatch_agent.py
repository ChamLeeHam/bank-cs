"""Automatic, workload-balanced ticket assignment across a shared seat pool."""
import json
import logging

from src.utils.llm_util import extract_json_from_markdown, get_llm_client

logger = logging.getLogger(__name__)


def dispatch_ticket(candidates, summary=""):
    """Pick a least-loaded seat; the model may break ties, never route by ticket category."""
    if not candidates:
        return {"username": None, "reason": "没有可用坐席，等待主管分配。", "source": "rule"}

    counts = {item["username"]: int(item.get("active_tickets", 0)) for item in candidates}
    minimum = min(counts.values())
    eligible = sorted(username for username, count in counts.items() if count == minimum)
    # Deterministic fallback also spreads initial assignments by increasing active load.
    fallback = eligible[0]
    if len(eligible) == 1:
        return {"username": fallback, "reason": f"按当前未结工单量自动分配给 {fallback}。", "source": "rule"}

    try:
        client = get_llm_client(response_format=True, timeout=8, max_retries=0)
        if client is None:
            raise RuntimeError("dispatcher model is unavailable")
        eligible_json = json.dumps(
            [{"username": name, "active_tickets": counts[name]} for name in eligible], ensure_ascii=False)
        prompt = (
            "你是坐席自动分配智能体。所有坐席处理所有类型的工单，严禁根据工单类别或内容选择员工。"
            "只能在未结工单量最少的候选坐席中选择一个，目标是均衡工作量。"
            "只输出 JSON：{\"username\":\"候选账号\"}。不得选候选列表以外的账号。\n"
            f"候选及未结工单量：{eligible_json}\n"
            f"工单摘要（仅供了解，不可作为按类别分人的依据）：{summary[:300]}"
        )
        raw = client.invoke(prompt).content
        result = json.loads(extract_json_from_markdown(str(raw)))
        username = result.get("username")
        if username not in eligible:
            raise ValueError("dispatcher selected a non-eligible seat")
        return {"username": username, "reason": "智能体依据坐席当前未结工单量均衡分配。", "source": "ai"}
    except Exception as exc:
        logger.warning("AI坐席分配失败，使用负载均衡规则兜底：%s", type(exc).__name__)
        return {"username": fallback, "reason": f"智能体不可用，按未结工单量规则分配给 {fallback}。", "source": "rule"}
