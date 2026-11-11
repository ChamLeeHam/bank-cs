"""
HyDE（参考课程 03-rag/search_embedding_HyDE.py）：先让 LLM 写一段"假设性答案"，再用"问题 + 假设答案"检索

客户的问法（"钱汇去中国要等多久"）和知识库的写法（"海外电汇一般 1–3 个工作日到账"）用词差别大时，
假设答案的用词更接近知识库，能找回只用问题检索不到的章节。
- 假设答案只用于检索，不会展示给客户，也不会作为回答依据（回答只能基于检索到的真实资料）
- 修正了课程代码的 bug：HYDE_PROMPT_TEMPLATE, format(query) 写成了逗号，问题没有被填进提示词
"""
import logging

from src.rag.llm import get_deepseek

logger = logging.getLogger("bank.rag.hyde")

HYDE_PROMPT = """请为下面的银行客户问题写一段简短的参考答案，风格像银行官网 FAQ。
客户问题：{query}
要求：
1. 80 字以内，只写核心信息
2. 使用银行常用的正式术语（如 海外电汇、PayNow、FAST、GIRO、多币种账户、PIN、OTP）
3. 用和问题相同的语言回答；中英混杂的问题用中文回答
4. 直接输出答案，不要解释"""


def hyde_document(query: str) -> str | None:
    try:
        return str(get_deepseek(0.0).invoke(HYDE_PROMPT.format(query=query)).content).strip()
    except Exception as e:
        logger.warning("HyDE 生成失败，跳过这一路检索：%s", e)
        return None
