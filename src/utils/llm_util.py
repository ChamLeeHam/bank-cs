"""
LLM 客户端（参考课程 03-rag/utils/llm_util.py）：DeepSeek（OpenAI 兼容接口）

RAG 的问题重写、HyDE、回答生成共用；与 Day 2 生成数据使用同一组配置（DEEPSEEK_*）。
发送给 DeepSeek 的只有脱敏后的问题 / 对话和公开的 FAQ 片段，不包含客户 ID 和账户数据。
"""
from functools import lru_cache

from src.schema.config import get_config
from src.schema.exceptions import LLMError


@lru_cache(maxsize=4)
def get_llm_client(temperature: float = 0.2, response_format: bool = False):
    """
    获取 LLM 客户端（相同参数只创建一次）
    :param temperature: 温度
    :param response_format: True 时要求模型输出 JSON 对象
    """
    from langchain_openai import ChatOpenAI

    config = get_config()
    if not config.deepseek_api_key:
        raise LLMError("缺少 DEEPSEEK_API_KEY", "llm_util")
    kwargs = {"response_format": {"type": "json_object"}} if response_format else {}
    return ChatOpenAI(model=config.deepseek_model, api_key=config.deepseek_api_key, base_url=config.deepseek_base_url,
                      temperature=temperature, timeout=30, max_retries=1, model_kwargs=kwargs)


class DeepSeekGenerator:
    """与本地 IntentModel.chat() 相同的接口：chat(messages) -> str，RAG 回答节点可以在两者之间切换"""

    def __init__(self):
        self.llm = get_llm_client(0.2)

    def chat(self, messages: list[dict]) -> str:
        return str(self.llm.invoke(messages).content).strip()
