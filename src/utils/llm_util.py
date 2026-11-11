# 大模型客户端（参考课程 03-rag/utils/llm_util.py）
# 本项目使用 DeepSeek（OpenAI 兼容接口），与 Day 2 生成数据使用同一组配置（DEEPSEEK_*）
# 发送给 DeepSeek 的只有脱敏后的问题 / 对话和公开的 FAQ 片段，不包含客户 ID 和账户数据
import logging
import re

from langchain_openai import ChatOpenAI

from src.schema.config import get_config

logger = logging.getLogger(__name__)

cache_llm_client = {}


def get_llm_client(model_name: str = None, temperature: float = 0.0, response_format: bool = False):
    """
    返回 LLM 客户端对象
    缓存的对象是：client
    缓存的 key：模型名 + 温度 + 响应格式（不同的步骤可能用不同的温度和响应格式）
    """
    config = get_config()

    # 1. 获取模型的名字
    model_name = model_name or config.deepseek_model
    cache_key = (model_name, temperature, response_format)

    # 2. 缓存命中，直接返回
    if cache_key in cache_llm_client:
        return cache_llm_client[cache_key]

    # 3. 返回的内容格式
    model_kwargs = {}
    if response_format:
        model_kwargs["response_format"] = {"type": "json_object"}
    try:
        # 4. 定义模型实例
        client = ChatOpenAI(
            model=model_name,
            api_key=config.deepseek_api_key,
            base_url=config.deepseek_base_url,
            temperature=temperature,
            timeout=30,
            max_retries=1,
            model_kwargs=model_kwargs,
        )

        # 5. 同步数据
        cache_llm_client[cache_key] = client

        # 6. 返回
        return client
    except Exception as e:
        logger.error(f"LLM客户端创建失败，原因：{str(e)}")
        return None


def chat(messages: list[dict], temperature: float = 0.2) -> str:
    """发送对话，返回回复文本；与本地 IntentModel.chat() 的用法相同，回答节点可以在两者之间切换"""
    client = get_llm_client(temperature=temperature)
    if client is None:
        raise RuntimeError("LLM客户端不可用")
    return str(client.invoke(messages).content).strip()


def extract_json_from_markdown(content: str) -> str:
    """从可能包含 Markdown 代码块标记的字符串中提取纯 JSON 内容"""
    # 匹配第一个 '{' 到最后一个 '}' 之间的所有内容
    match = re.search(r"\{.*\}", content, re.DOTALL)
    if match:
        return match.group(0)

    # 如果没有找到 {}，尝试清理 markdown 标记
    return re.sub(r"^```(?:json)?\s*|\s*```$", "", content.strip(), flags=re.MULTILINE)


if __name__ == "__main__":
    import json

    llm_client = get_llm_client(response_format=True)
    ai_message = llm_client.invoke('用 JSON 返回 PayNow 的中文名称：{"name": "..."}')
    print(json.loads(extract_json_from_markdown(ai_message.content)))
