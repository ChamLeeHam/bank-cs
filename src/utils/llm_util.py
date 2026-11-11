# 大模型客户端，这里用的是 DeepSeek（兼容 OpenAI 接口）
import logging
import re

from langchain_openai import ChatOpenAI

from src.schema.config import get_config

logger = logging.getLogger(__name__)

cache_llm_client = {}


def get_llm_client(model_name=None, temperature=0.0, response_format=False):
    """获取 LLM 客户端，相同的参数只创建一次"""
    config = get_config()

    # 1. 模型名，默认用 .env 里的 DEEPSEEK_MODEL_NAME
    model_name = model_name or config.deepseek_model
    cache_key = (model_name, temperature, response_format)

    # 2. 已经创建过就直接返回
    if cache_key in cache_llm_client:
        return cache_llm_client[cache_key]

    # 3. 需要 JSON 格式时加上 response_format
    model_kwargs = {}
    if response_format:
        model_kwargs["response_format"] = {"type": "json_object"}

    try:
        # 4. 创建客户端
        client = ChatOpenAI(
            model=model_name,
            api_key=config.deepseek_api_key,
            base_url=config.deepseek_base_url,
            temperature=temperature,
            timeout=30,
            max_retries=1,
            model_kwargs=model_kwargs,
        )
        cache_llm_client[cache_key] = client
        return client
    except Exception as e:
        logger.error(f"LLM客户端创建失败：{e}")
        return None


def chat(messages, temperature=0.2):
    """发送对话，返回回复的文本"""
    client = get_llm_client(temperature=temperature)
    if client is None:
        raise RuntimeError("LLM客户端不可用")
    return str(client.invoke(messages).content).strip()


def extract_json_from_markdown(content):
    """去掉 ```json 这类标记，只留下 JSON 部分"""
    match = re.search(r"\{.*\}", content, re.DOTALL)
    if match:
        return match.group(0)
    return re.sub(r"^```(?:json)?\s*|\s*```$", "", content.strip(), flags=re.MULTILINE)


if __name__ == "__main__":
    import json

    llm_client = get_llm_client(response_format=True)
    ai_message = llm_client.invoke('用 JSON 返回 PayNow 的中文名称：{"name": "..."}')
    print(json.loads(extract_json_from_markdown(ai_message.content)))
