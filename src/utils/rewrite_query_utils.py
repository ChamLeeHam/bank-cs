# 根据多轮对话的历史记录实现问题重写（参考课程 03-rag/rewritten_query.py）
# 例：上一轮问 "海外电汇手续费多少？"，这一轮问 "那 FAST 呢？" -> "FAST 转账的手续费是多少？"
import json
import logging

from src.utils.llm_util import extract_json_from_markdown, get_llm_client

logger = logging.getLogger(__name__)

MAX_HISTORY = 4  # 最近 2 轮（客户 + 客服）

REPHRASE_PROMPT = """你是银行客服系统的问题重写助手。根据历史对话，把客户的当前问题改写成一个独立、完整、指代明确的问题，用于检索知识库。
历史对话：
{history_text}

当前问题：{query}

只输出一个 JSON 对象：{{"rewritten_query": "重写后的问题"}}
要求：
1. 当前问题已经完整时，原样返回
2. 用历史对话补全代词和省略（"那个"、"它"、"那 FAST 呢"）
3. 保持客户使用的语言（中文 / 英文 / 中英混杂）
4. 不要回答问题，不要添加历史中没有的信息"""


def process(query: str, history: list[tuple[str, str]]):
    """history: [(角色, 脱敏文本), ...]，角色为 客户 / 客服（见 history_utils.history_of）"""
    # 1. 对历史记录进行裁剪：只取最近几条
    history = history[-MAX_HISTORY:]

    # 2. 没有历史时直接返回原问题（第一轮不调用模型，省一次请求）
    if not history:
        return {"rewritten_query": query, "history": history}

    try:
        # 3. 组装提示词
        history_text = "\n".join(f"{role}：{content}" for role, content in history)
        prompt = REPHRASE_PROMPT.format(history_text=history_text, query=query)

        # 4. 调用 LLM（要求返回 JSON）
        client = get_llm_client(response_format=True)
        content = client.invoke(prompt).content.strip()

        # 5. 解析 JSON
        result = json.loads(extract_json_from_markdown(content))
        rewritten_query = str(result.get("rewritten_query") or "").strip() or query
        logger.info("3. 问题重写完成")
    except Exception as e:
        logger.warning(f"问题重写失败，使用原问题：{e}")
        rewritten_query = query

    # 6. 返回重写后的问题
    return {"rewritten_query": rewritten_query, "history": history}


if __name__ == "__main__":
    history = [("客户", "海外电汇手续费多少？"), ("客服", "每笔海外电汇（TT）收取 SGD 10 手续费。")]
    print(process("那 FAST 呢？", history)["rewritten_query"])
    print(process("那 FAST 呢？", [])["rewritten_query"])  # 没有历史：原样返回
