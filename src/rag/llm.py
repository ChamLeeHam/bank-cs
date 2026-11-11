"""
DeepSeek 客户端（OpenAI 兼容接口）：RAG 的问题重写、HyDE、回答生成共用；与 Day 2 生成数据使用同一组配置
发送给 DeepSeek 的只有脱敏后的问题 / 对话和公开的 FAQ 片段，不包含客户 ID 和账户数据
"""
from functools import lru_cache

from src.schema.config import get_config
from src.schema.exceptions import LLMError


@lru_cache(maxsize=4)
def get_deepseek(temperature: float = 0.2, json_mode: bool = False):
    from langchain_openai import ChatOpenAI

    config = get_config()
    if not config.deepseek_api_key:
        raise LLMError("缺少 DEEPSEEK_API_KEY", "rag")
    kwargs = {"response_format": {"type": "json_object"}} if json_mode else {}
    return ChatOpenAI(model=config.deepseek_model, api_key=config.deepseek_api_key, base_url=config.deepseek_base_url,
                      temperature=temperature, timeout=30, max_retries=1, model_kwargs=kwargs)


class DeepSeekGenerator:
    """与本地 IntentModel.chat() 相同的接口：chat(messages) -> str"""

    def __init__(self):
        self.llm = get_deepseek(0.2)

    def chat(self, messages: list[dict]) -> str:
        return str(self.llm.invoke(messages).content).strip()
