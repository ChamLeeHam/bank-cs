# HyDE（参考课程 03-rag/search_embedding_HyDE.py）
# HyDE: 1. 先根据用户的问题生成一个参考答案
# HyDE: 2. 将用户的问题和参考答案拼接在一起
# HyDE: 3. 再做混合检索
# 客户的问法（"钱汇去中国要等多久"）和知识库的写法（"海外电汇一般 1–3 个工作日到账"）用词差别大时，
# 参考答案的用词更接近知识库，能找回只用问题检索不到的章节。参考答案只用于检索，不展示给客户，也不作为回答依据。
import logging

from src.utils import search_embedding_utils
from src.utils.llm_util import get_llm_client

logger = logging.getLogger(__name__)

HYDE_PROMPT_TEMPLATE = """请为下面的银行客户问题写一段简短的参考答案，风格像银行官网 FAQ。
客户问题：{query}
要求：
1. 80 字以内，只写核心信息
2. 使用银行常用的正式术语（如 海外电汇、PayNow、FAST、GIRO、多币种账户、PIN、OTP）
3. 用和问题相同的语言回答；中英混杂的问题用中文回答
4. 直接输出答案，不要解释"""


def process(query: str, intent: str = None):
    # 0. 根据用户问题生成一个假设性答案
    logger.info("0. 根据用户问题生成一个假设性答案")
    answer = _generate_hyde_doc(query)
    if not answer:  # 生成失败：跳过这一路检索
        return {"hyde_doc": None, "embedding_chunks": []}

    # 1. 问题 + 假设答案一起做混合检索
    combine_query = f"{query}\n{answer}"
    chunks = search_embedding_utils.process(combine_query, intent)["embedding_chunks"]
    logger.info("2. 执行完成 HyDE 检索")
    return {"hyde_doc": answer, "embedding_chunks": chunks}


def _generate_hyde_doc(query: str):
    try:
        # 修正课程代码的 bug：课程写成了 HYDE_PROMPT_TEMPLATE, format(query)（逗号），问题没有被填进提示词
        prompt = HYDE_PROMPT_TEMPLATE.format(query=query)
        result = get_llm_client().invoke(prompt)
        return str(result.content).strip()
    except Exception as e:
        logger.warning(f"HyDE 生成失败，跳过这一路检索：{e}")
        return None


if __name__ == "__main__":
    result = process("钱汇去中国要等多久")
    print("假设答案：", result["hyde_doc"])
    print("检索结果：", [c["section"].split(" / ")[0] for c in result["embedding_chunks"][:3]])
