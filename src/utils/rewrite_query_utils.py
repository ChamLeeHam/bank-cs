# 问题重写：结合前面的对话，把"那 FAST 呢？"这种问题补全
import json
import logging

from src.utils.llm_util import extract_json_from_markdown, get_llm_client

logger = logging.getLogger(__name__)

MAX_HISTORY = 4  # 只看最近 2 轮对话

REPHRASE_PROMPT = """你是银行客服系统的问题重写助手。根据历史对话，把客户的当前问题改写成一个完整、可以单独理解的问题，用于检索知识库。
历史对话：
{history_text}

当前问题：{query}

只输出 JSON：{{"rewritten_query": "重写后的问题"}}
要求：
1. 问题本来就完整的话，原样返回
2. 用历史对话补全"那个"、"它"这类指代和省略
3. 保持客户用的语言（中文、英文或中英混杂）
4. 不要回答问题，也不要加历史对话里没有的内容"""


def process(query, history):
    """history: [(角色, 文本), ...]"""
    history = history[-MAX_HISTORY:]

    # 第一轮没有历史，直接返回原问题
    if not history:
        return {"rewritten_query": query, "history": history}

    try:
        # 1. 组装提示词
        history_text = "\n".join(f"{role}：{content}" for role, content in history)
        prompt = REPHRASE_PROMPT.format(history_text=history_text, query=query)

        # 2. 调用 LLM
        client = get_llm_client(response_format=True)
        content = client.invoke(prompt).content.strip()

        # 3. 解析 JSON
        result = json.loads(extract_json_from_markdown(content))
        rewritten_query = str(result.get("rewritten_query") or "").strip() or query
    except Exception as e:
        logger.warning(f"问题重写失败，用原问题：{e}")
        rewritten_query = query

    return {"rewritten_query": rewritten_query, "history": history}


if __name__ == "__main__":
    history = [("客户", "海外电汇手续费多少？"), ("客服", "每笔海外电汇（TT）收取 SGD 10 手续费。")]
    print(process("那 FAST 呢？", history)["rewritten_query"])
    print(process("那 FAST 呢？", [])["rewritten_query"])
