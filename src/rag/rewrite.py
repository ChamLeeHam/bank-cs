"""
问题重写（参考课程 03-rag/13-rewritten_query.py）：根据对话历史把当前问题改写成独立、指代明确的问题

例：上一轮问 "海外电汇手续费多少？"，这一轮问 "那 FAST 呢？" -> "FAST 转账的手续费是多少？"
- 没有历史时直接返回原问题（第一轮不调用 LLM，省一次请求）
- 只使用最近几条脱敏后的对话（state["messages"] 中只保存脱敏文本）
- 失败时返回原问题，不影响后续检索
"""
import json
import logging

from src.rag.llm import get_deepseek

logger = logging.getLogger("bank.rag.rewrite")
MAX_HISTORY = 4  # 最近 2 轮（客户 + 客服）

REWRITE_PROMPT = """你是银行客服系统的问题重写助手。根据历史对话，把客户的当前问题改写成一个独立、完整、指代明确的问题，用于检索知识库。
历史对话：
{history}

当前问题：{query}

只输出一个 JSON 对象：{{"rewritten_query": "重写后的问题"}}
要求：
1. 当前问题已经完整时，原样返回
2. 用历史对话补全代词和省略（"那个"、"它"、"那 FAST 呢"）
3. 保持客户使用的语言（中文 / 英文 / 中英混杂）
4. 不要回答问题，不要添加历史中没有的信息"""


def rewrite_query(query: str, history: list[tuple[str, str]]) -> str:
    """history: [(角色, 文本), ...]，角色为 客户 或 客服"""
    history = history[-MAX_HISTORY:]
    if not history:
        return query
    try:
        text = "\n".join(f"{role}：{content}" for role, content in history)
        reply = get_deepseek(0.0, json_mode=True).invoke(REWRITE_PROMPT.format(history=text, query=query)).content
        rewritten = str(json.loads(reply).get("rewritten_query") or "").strip()
        return rewritten or query
    except Exception as e:
        logger.warning("问题重写失败，使用原问题：%s", e)
        return query
