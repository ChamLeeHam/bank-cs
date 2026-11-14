# 根据问题和检索到的块生成回答
# 默认用 DeepSeek，失败时返回 None，由节点直接给出资料原文
import logging
import re

from src.schema.config import get_config
from src.utils.history_utils import format_history
from src.utils.llm_util import chat

logger = logging.getLogger(__name__)

_NUMBER = re.compile(r"\d+(?:[.,]\d+)*")
_CJK = re.compile(r"[一-鿿]")
_LIST_MARKER = re.compile(r"^\s*(?:\d+[.、)）]|\[\d+\])\s*", re.MULTILINE)  # 列表编号，比如 "1." "2、"
_PIN_BLOCKED = re.compile(r"(?:\bpin\b|密码).{0,40}(?:锁|解锁|lock|unblock|unlock)|(?:锁|解锁|lock|unblock|unlock).{0,40}(?:\bpin\b|密码)", re.IGNORECASE)


def is_pin_unblock_question(question):
    """PIN 锁定和解锁需要区分于普通修改 PIN。"""
    return bool(_PIN_BLOCKED.search(question))


def pin_unblock_source(docs, lang):
    """从检索到的原文取出锁定和解锁段，避免生成时混入修改 PIN 的步骤。"""
    marker = "After 3 wrong PIN" if lang == "en" else "连续输错 PIN"
    for doc in docs:
        body = split_languages(doc["text"].split("\n", 1)[-1])[lang]
        index = body.lower().find(marker.lower())
        if index >= 0:
            return body[index:].strip(), doc
    return None


def statement_charge_answer(docs, lang):
    """用对应 FAQ 给出谨慎的账单费用说明，不把常见项目说成该笔交易的确定原因。"""
    for doc in docs:
        section = doc.get("section", "").lower()
        sub_intents = doc.get("sub_intents") or []
        if "extra_charge_on_statement" not in sub_intents and "unfamiliar charges on your statement" not in section:
            continue

        if lang == "en":
            answer = (
                "I can’t identify the exact charge from that description alone. Please open the transaction in the app "
                "to check its fee type; common items include foreign transaction, ATM, annual, replacement-card, or "
                "late-payment fees. If you didn’t make the transaction, freeze your card and report it as fraud; "
                "if you think a fee was applied incorrectly, ask a human agent to review it."
            )
        else:
            answer = (
                "仅凭这段描述还无法确认具体是哪项费用。请在 App 点开该笔交易查看费用类型；常见项目包括外币交易费、"
                "ATM 费用、年费、补卡费或逾期还款费。如果这笔交易不是您本人操作，请立即冻结卡片并举报盗刷；"
                "如果您认为费用收错了，可以申请人工客服核查。"
            )
        return answer, doc
    return None

ANSWER_PROMPT = {
    "zh": """你是狮城银行的客服助手，只能根据<资料>回答客户的问题。
1. 问题里有条件（年龄、金额、限额、时间）时，先把客户的情况和资料里的条件对比一下，再下结论
2. 资料里没有的内容，就说不确定，建议客户转人工，不要自己猜
3. 金额、费用、天数要和资料一致
4. 用简体中文回答，2 到 4 句话，不用重复问题
5. 不提供投资建议，不要提到资料的编号""",
    "en": """You are Lion City Bank's customer service assistant. Answer only from the <documents>.
1. If the question has a condition (age, amount, limit, time), compare the customer's situation with the documents first, then answer
2. If the documents don't have the answer, say you're not sure and suggest a human agent. Don't guess
3. Amounts, fees and days must match the documents
4. Answer in English in 2 to 4 sentences, without repeating the question
5. No investment advice, and don't mention document numbers""",
}


def process(rewritten_query, history, docs, lang):
    """返回 {"answer": 回答或 None, "generator": 用的是哪个模型}"""
    # 1. 提示词
    messages = _build_prompt(rewritten_query, history, docs, lang)

    # 2. 按顺序试各个模型
    for name, generate in _get_generators():
        try:
            answer = generate(messages)
        except Exception as e:
            logger.warning(f"{name} 生成失败：{e}")
            continue
        # 3. 检查数字有没有乱编
        if _has_invented_numbers(answer, docs, rewritten_query):
            return {"answer": None, "generator": "source_text"}
        return {"answer": answer, "generator": name}

    return {"answer": None, "generator": "source_text"}


def split_languages(body):
    """章节里的中文段和英文段分开"""
    paras = [p.strip() for p in body.split("\n\n") if p.strip()]
    zh = [p for p in paras if _CJK.search(p)]
    en = [p for p in paras if not _CJK.search(p)]
    return {"zh": "\n".join(zh), "en": "\n".join(en)}


def _build_prompt(question, history, docs, lang):
    # 资料只放和回答同一种语言的段落
    parts = []
    for i, doc in enumerate(docs, 1):
        text = split_languages(doc["text"])[lang] or doc["text"]
        parts.append(f"[{i}] {doc['section']}\n{text}")
    context = "\n\n".join(parts)

    user = f"<资料>\n{context}\n</资料>\n\n"
    history_text = format_history(history)
    if history_text:
        user += f"<历史对话>\n{history_text}\n</历史对话>\n\n"
    user += f"客户问题：{question}"
    return [{"role": "system", "content": ANSWER_PROMPT[lang]}, {"role": "user", "content": user}]


def _get_generators():
    generators = []
    if get_config().rag_generator == "deepseek":
        generators.append(("deepseek", chat))
    else:
        try:
            from src.nodes.intent_router import chat as local_chat
            generators.append(("local", local_chat))
        except Exception as e:
            logger.warning(f"本地 Qwen 不可用：{e}")
    return generators


def _numbers_in(text):
    text = _LIST_MARKER.sub("", text)
    return {n.replace(",", "") for n in _NUMBER.findall(text)}


def _has_invented_numbers(answer, docs, question):
    """回答里的数字必须在资料或问题里出现过"""
    source = "\n".join(d["text"] for d in docs)
    invented = _numbers_in(answer) - _numbers_in(source) - _numbers_in(question)
    if invented:
        logger.warning(f"回答里有资料中没有的数字：{sorted(invented)}")
    return bool(invented)


if __name__ == "__main__":
    mock_docs = [{"section": "开户条件 / Who can open an account",
                  "text": "开户条件\n年满 16 岁可以开立储蓄账户，16 岁以下需要父母开联名账户。\n\n"
                          "Aged 16 or above can open a savings account. Under 16, a parent must open a joint account."}]
    print(process("Can I open an account if I'm 15?", [], mock_docs, "en"))
