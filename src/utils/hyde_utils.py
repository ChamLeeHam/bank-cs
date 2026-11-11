# HyDE：先让 LLM 写一个参考答案，再用"问题 + 参考答案"去检索
# 客户的说法和知识库的写法差别比较大时，参考答案的用词更接近知识库，比较容易找到
# 参考答案只用来检索，不会给客户看
import logging

from src.utils import search_embedding_utils
from src.utils.llm_util import get_llm_client

logger = logging.getLogger(__name__)

HYDE_PROMPT_TEMPLATE = """请为下面的银行客户问题写一段简短的参考答案，风格像银行官网的 FAQ。
客户问题：{query}
要求：
1. 80 字以内，只写重点
2. 用银行常用的说法（海外电汇、PayNow、FAST、GIRO、多币种账户、PIN、OTP 等）
3. 用和问题一样的语言回答，中英混杂的问题用中文
4. 直接写答案，不用解释"""


def process(query, intent=None):
    # 1. 生成参考答案
    answer = _generate_hyde_doc(query)
    if not answer:
        return {"hyde_doc": None, "embedding_chunks": []}

    # 2. 问题和参考答案拼在一起检索
    combine_query = f"{query}\n{answer}"
    chunks = search_embedding_utils.process(combine_query, intent)["embedding_chunks"]
    return {"hyde_doc": answer, "embedding_chunks": chunks}


def _generate_hyde_doc(query):
    try:
        prompt = HYDE_PROMPT_TEMPLATE.format(query=query)
        result = get_llm_client().invoke(prompt)
        return str(result.content).strip()
    except Exception as e:
        logger.warning(f"HyDE 生成失败，跳过这一路：{e}")
        return None


if __name__ == "__main__":
    result = process("钱汇去中国要等多久")
    print("参考答案：", result["hyde_doc"])
    print("检索结果：", [c["section"].split(" / ")[0] for c in result["embedding_chunks"][:3]])
