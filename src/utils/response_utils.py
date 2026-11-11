# 根据用户的问题和检索出来的相关块，让模型组织语言回复（参考课程 03-rag/response.py）
# 生成模型由 RAG_GENERATOR 选择：deepseek（默认）或 local（本地 Qwen2.5-1.5B，与意图模型共用权重）
# DeepSeek 失败时改用本地 Qwen；都失败返回 None（回答节点改用资料原文）
# 回答后做数字核对：回答中的每个数字都必须在资料或问题中出现过，否则视为可能编造，返回 None
import logging
import re

from src.schema.config import get_config
from src.utils.history_utils import format_history
from src.utils.llm_util import chat

logger = logging.getLogger(__name__)

_NUMBER = re.compile(r"\d+(?:[.,]\d+)*")
_CJK = re.compile(r"[一-鿿]")
_LIST_MARKER = re.compile(r"^\s*(?:\d+[.、)）]|\[\d+\])\s*", re.MULTILINE)  # "1. " "2、" "[1]" 这类编号不算数字

ANSWER_PROMPT = {
    "zh": ("你是狮城银行的客服助手。只能根据<资料>回答客户的问题：\n"
           "- 如果问题涉及条件（年龄、金额、限额、时间），先把客户的情况和资料中的条件逐一比较，再给出结论\n"
           "- 资料中没有的信息，直接说不确定，并建议客户转人工客服，不要猜测或编造\n"
           "- 金额、费用、天数必须与资料完全一致\n"
           "- 用简体中文回答，2 到 4 句话，直接回答问题，不要重复问题\n"
           "- 不要提供投资建议，不要提及资料编号"),
    "en": ("You are Lion City Bank's customer service assistant. Answer only from the <documents>:\n"
           "- If the question involves a condition (age, amount, limit, time), compare the customer's situation with "
           "the condition in the documents first, then give the conclusion\n"
           "- If the documents do not contain the answer, say you are not sure and suggest a human agent; never guess\n"
           "- Amounts, fees and days must match the documents exactly\n"
           "- Answer in English in 2 to 4 sentences, directly, without repeating the question\n"
           "- Do not give investment advice and do not mention document numbers"),
}


def process(rewritten_query, history, docs, lang):
    """
    根据用户的问题和检索出来的相关块，让模型组织语言回复
    :param rewritten_query: 重写后的问题
    :param history: 历史 [(角色, 文本), ...]
    :param docs: 精排后保留的块
    :param lang: zh / en
    :return: {"answer": 回答或 None, "generator": 实际使用的生成方式}
    """
    # 1. 构建回复的提示词
    messages = _build_prompt(rewritten_query, history, docs, lang)

    # 2. 依次尝试各个生成模型
    for name, generate in _get_generators():
        try:
            answer = generate(messages)
            logger.info(f"2. 回答生成完成：{name}")
            # 3. 数字核对
            if _has_invented_numbers(answer, docs, rewritten_query):
                return {"answer": None, "generator": "source_text"}
            return {"answer": answer, "generator": name}
        except Exception as e:
            logger.warning(f"生成模型 {name} 失败，尝试下一个：{e}")
    return {"answer": None, "generator": "source_text"}


def split_languages(body: str) -> dict:
    """知识库每个章节是"中文段落 + 英文段落"，按是否含汉字分开"""
    paras = [p.strip() for p in body.split("\n\n") if p.strip()]
    return {"zh": "\n".join(p for p in paras if _CJK.search(p)),
            "en": "\n".join(p for p in paras if not _CJK.search(p))}


def _build_prompt(question, history, docs, lang):
    # 课程做法：参考内容 + 历史对话 + 用户问题；参考内容只放与回答语言相同的段落
    context = "\n\n".join(f"[{i}] {d['section']}\n{split_languages(d['text'])[lang] or d['text']}"
                          for i, d in enumerate(docs, 1))
    history_text = format_history(history)
    user = f"<资料>\n{context}\n</资料>\n\n" + (f"<历史对话>\n{history_text}\n</历史对话>\n\n" if history_text else "") + \
           f"客户问题：{question}"
    return [{"role": "system", "content": ANSWER_PROMPT[lang]}, {"role": "user", "content": user}]


def _get_generators():
    generators = []
    if get_config().rag_generator == "deepseek":
        generators.append(("deepseek", chat))
    try:
        from src.nodes.intent_router import IntentModel
        generators.append(("local", lambda messages: IntentModel.get().chat(messages)))
    except Exception as e:
        logger.warning(f"本地 Qwen 不可用：{e}")
    return generators


def _numbers_in(text: str) -> set:
    return {n.replace(",", "") for n in _NUMBER.findall(_LIST_MARKER.sub("", text))}


def _has_invented_numbers(answer, docs, question) -> bool:
    invented = _numbers_in(answer) - _numbers_in("\n".join(d["text"] for d in docs)) - _numbers_in(question)
    if invented:
        logger.warning(f"回答中有资料里没有的数字 {sorted(invented)}，改用资料原文")
    return bool(invented)


if __name__ == "__main__":
    mock_docs = [{"section": "开户条件 / Who can open an account",
                  "text": "开户条件\n年满 16 岁可以开立储蓄账户。16 岁以下需由父母开立联名账户。\n\n"
                          "Aged 16 or above can open a savings account. Under 16, a parent must open a joint account."}]
    print(process("Can I open an account if I'm 15?", [], mock_docs, "en"))
