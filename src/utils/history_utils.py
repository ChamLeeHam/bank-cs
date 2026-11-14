# 对话历史
# 历史保存在 state["messages"] 里（已经脱敏），这里转成 [(角色, 文本), ...] 给问题重写和生成回答用
from langchain_core.messages import AIMessage, HumanMessage
import re

SOURCE_MARKERS = ("\n\n来源：", "\n\nSource:")
_STOLEN_CARD_HYPOTHETICAL = re.compile(r"(?:那|请问)?如果(?:银行卡|卡片|卡)(?:被偷|被盗|被人偷)(?:了)?(?:呢|的话呢|怎么办)?[？?。！!\s]*")


def stolen_card_hypothetical(question):
    return bool(_STOLEN_CARD_HYPOTHETICAL.fullmatch(question))


def fraud_confirmed_followup(question, history):
    """仅把紧接盗刷假设澄清的明确发生确认视为风险报告。"""
    if not history or history[-1][0] != "客服":
        return False
    previous = history[-1][1]
    if "这是已经发生，还是您在了解一般处理方法" not in previous and "Has this happened to you, or are you asking generally?" not in previous:
        return False
    return bool(re.fullmatch(r"(?:是的?[，, ]*)?(?:已经|确实|真的)(?:发生|发生了|被盗刷了)[。！!？?\s]*|"
                             r"(?:yes[, ]+)?(?:it (?:already )?happened|it has happened)[.!?\s]*",
                             question, re.IGNORECASE))


def fraud_general_followup(question, history):
    """仅识别紧接盗刷风险澄清的明确一般咨询答复。"""
    if not history or history[-1][0] != "客服":
        return False
    previous = history[-1][1]
    if "这是已经发生，还是您在了解一般处理方法" not in previous and "Has this happened to you, or are you asking generally?" not in previous:
        return False
    return bool(re.fullmatch(r"(?:我)?(?:只是|只是想|只想|就是想)(?:想)?(?:了解|知道|问)(?:一下|一般处理方法|怎么处理)?[。！!？?\s]*|"
                             r"(?:i(?:'m| am) )?(?:just|only) (?:asking|curious|want to know)[.!?\s]*",
                             question, re.IGNORECASE))


def negated_fraud_limit_question(question):
    """只匹配否认盗刷并单独询问额度的完整句子；其他复合情况留给风险规则。"""
    return bool(re.fullmatch(
        r"(?:我的|本人(?:的)?)\s*(?:银行卡|信用卡|借记卡|卡片|卡|card)\s*(?:没有|没)(?:被)?盗刷(?:过|了)?\s*[，,；;]\s*"
        r"(?:我)?(?:只是|只|想|只是想|只想)*(?:想)?(?:问|知道|了解)?\s*"
        r"(?:(?:怎么|如何)(?:提高|调整|修改|更改|降低)(?:额度|限额)|"
        r"(?:额度|限额)\s*(?:怎么|如何)(?:调|调整|修改|提高|降低)|limit\s*(?:怎么|如何)(?:改|调整|修改))"
        r"[？?。！!\s]*", question, re.IGNORECASE))


def stolen_card_clarification(question, history):
    """只解析紧接被盗卡澄清问题的两种明确回答。"""
    if not history or history[-1][0] != "客服" or "还是需要我协助冻结银行卡" not in history[-1][1]:
        return None
    if re.fullmatch(r"(?:我)?(?:想|要|想要)?(?:了解|看看)(?:一下)?(?:被盗后的)?(?:处理方法|怎么处理|怎么办)[？?。！!\s]*", question):
        return "info"
    if re.fullmatch(r"(?:我)?(?:想|要|想要|需要|请|帮我)?(?:协助|帮我)?冻结(?:银行卡|卡片|卡)?[？?。！!\s]*", question):
        return "freeze"
    return None


def replacement_fee_stolen_followup(question, history):
    """仅识别补卡费问答后对卡片被盗这一条件的假设性追问。"""
    if not stolen_card_hypothetical(question):
        return None
    if len(history) < 2 or history[-2][0] != "客户" or history[-1][0] != "客服":
        return None
    if not re.search(r"补卡费|补卡.*(?:费用|多少钱|收费)|(?:费用|多少钱|收费).*补卡", history[-2][1]):
        return None
    if not re.search(r"补卡|补发", history[-1][1]) or "[私人业务结果已展示]" in history[-1][1]:
        return None
    return "银行卡被盗并报警后补卡费用是多少？"


def annual_fee_followup(question, history):
    """将对上一轮信用卡年费豁免条件的简短追问补全为公开知识问题。"""
    if not re.fullmatch(r"(?:那|请问)?(?:它|这个|这个费用|这笔年费)(?:可以|能|能不能|可不可以)(?:免|免掉|减免|豁免)(?:吗|么|吗？)?[？?。！!\s]*", question):
        return None
    if len(history) < 2 or history[-2][0] != "客户" or history[-1][0] != "客服":
        return None
    previous_question, previous_answer = history[-2][1], history[-1][1]
    if not re.search(r"信用卡|credit\s*card", previous_question, re.IGNORECASE):
        return None
    if not re.search(r"年费|annual\s*fee", previous_question, re.IGNORECASE):
        return None
    if not re.search(r"年费|annual\s*fee", previous_answer, re.IGNORECASE):
        return None
    return "信用卡年费可以豁免吗？"


def statement_fee_followup(question, history):
    """仅解析上一条账单费用回答中的序号；返回 (是否匹配, 费用名或 None)。"""
    match = re.fullmatch(r"(?:那|请问)?\s*第([一二三四五六七八九1-9])(?:个|项)(?:类型|费用|那个)?(?:是|指)?(?:什么|哪个|啥)(?:意思)?[？?。！!\s]*", question)
    if not match or not history or history[-1][0] != "客服":
        return False, None
    answer = history[-1][1]
    listing = re.search(r"(?:常见项目|(?:账单上)?常见的?(?:小额)?费用)(?:可能)?包括[：:]?\s*([^。；;]+)", answer)
    if listing:
        items = [re.sub(r"\s*[（(][^）)]*[）)]", "", item).strip()
                 for item in re.split(r"、|，|或|以及|和", listing.group(1)) if item.strip()]
    else:
        # 生成式回答的引导语可能变化，只从同一句中识别已明确列出的费用名称。
        items = []
        fee_names = r"外币交易费|海外\s*ATM\s*取款费|ATM\s*(?:取款费|费用)|信用卡年费|年费|补卡费|逾期还款费"
        for sentence in re.split(r"[。；;]", answer):
            found = re.findall(fee_names, sentence, re.IGNORECASE)
            if len(found) >= 2:
                items = found
                break
        if not items:
            return (True, None) if re.search(r"费用|手续费|charge|fee", answer, re.IGNORECASE) else (False, None)
    number = match.group(1)
    index = int(number) if number.isdigit() else "一二三四五六七八九".index(number) + 1
    return True, items[index - 1] if index <= len(items) else None


def history_of(state):
    """本轮之前的对话。最后一条是本轮的问题，不要"""
    history = []
    for message in list(state.get("messages") or [])[:-1]:
        if isinstance(message, HumanMessage):
            history.append(("客户", str(message.content)))
        elif isinstance(message, AIMessage):
            history.append(("客服", strip_sources(str(message.content))))
    return history


def strip_sources(answer):
    """去掉回答最后的来源"""
    for marker in SOURCE_MARKERS:
        answer = answer.split(marker)[0]
    return answer


def format_history(history, max_turns=4, max_chars=1000):
    """最近几条对话拼成文本"""
    lines = [f"{role}：{text}" for role, text in history[-max_turns:]]
    return "\n".join(lines)[-max_chars:]


if __name__ == "__main__":
    mock_state = {"messages": [HumanMessage("海外电汇手续费多少？"),
                               AIMessage("每笔 SGD 10。\n\n来源：《费用与汇率》海外电汇（TT）手续费"),
                               HumanMessage("那 FAST 呢？")]}
    history = history_of(mock_state)
    print(history)
    print(format_history(history))
