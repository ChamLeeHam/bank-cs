"""Small deterministic query expansions for recurring paraphrases in the demo KB."""
import re


_CARD_REFERENCE = re.compile(r"(?:\bcard\b|银行卡|信用卡|借记卡|卡片|卡)", re.IGNORECASE)
_CARD_DELIVERY_SIGNAL = re.compile(
    r"(?:arriv\w*|deliver\w*|receiv\w*|come\b|where\s+is|how\s+long|when\b|"
    r"没收到|未收到|还没到|没到|迟迟|寄到|寄送|到货|多久|几天|什么时候)",
    re.IGNORECASE,
)
_NEW_OR_REPLACEMENT_CARD = re.compile(
    r"(?:\bnew\b|\breplacement\b|\breplace\w*\b|\brenew\w*\b|\breissue\w*\b|"
    r"新卡|补发|补卡|换卡|到期换卡)",
    re.IGNORECASE,
)
_OVERDUE_DELIVERY_FOLLOWUP = re.compile(
    r"(?:超过|过了|已过|after)\s*(?:10|十)\s*(?:个\s*)?(?:工作日|(?:business|working)\s+days)"
    r".{0,24}(?:没(?:有)?收到|未收到|没到|还没到|未送达|not\s+arrived|"
    r"has(?:n't|\s+not)\s+arrived|haven't\s+received|have\s+not\s+received)|"
    r"(?:没(?:有)?收到|未收到|没到|还没到|未送达|not\s+arrived|has(?:n't|\s+not)\s+arrived|"
    r"haven't\s+received|have\s+not\s+received)"
    r".{0,24}(?:超过|过了|已过|after)\s*(?:10|十)\s*(?:个\s*)?(?:工作日|(?:business|working)\s+days)",
    re.IGNORECASE,
)
_CARD_DELIVERY_WORDS = re.compile(r"寄|送达|配送|寄送|寄到|delivery|deliver|arriv|track", re.IGNORECASE)
_CARD_WORDS = re.compile(r"银行卡|新卡|补发卡|补办卡|补卡|换卡|\bcard\b", re.IGNORECASE)


def expand_card_delivery_query(question):
    """Map a new/replacement-card arrival question to terms used by the delivery FAQ."""
    if not (_CARD_REFERENCE.search(question) and _NEW_OR_REPLACEMENT_CARD.search(question)
            and _CARD_DELIVERY_SIGNAL.search(question)):
        return None

    if re.search(r"[\u4e00-\u9fff]", question):
        return "新卡或补发卡寄送时间 新卡未收到 超过10个工作日 查询寄送进度"
    return "How long does delivery take for a new or replacement card? Card delivery time not received after 10 working days"


def resolve_card_delivery_followup(question, history):
    """Resolve a short overdue-delivery follow-up only when the immediately prior turn is about card delivery."""
    if not _OVERDUE_DELIVERY_FOLLOWUP.search(question or "") or len(history) < 2:
        return None

    previous_question, previous_answer = history[-2:]
    if previous_question[0] != "客户" or previous_answer[0] != "客服":
        return None
    context = f"{previous_question[1]} {previous_answer[1]}"
    if not (_CARD_WORDS.search(context) and _CARD_DELIVERY_WORDS.search(context)):
        return None

    if re.search(r"[\u4e00-\u9fff]", question):
        return "新卡或补发卡超过10个工作日仍未收到怎么办？"
    return "What should I do if my new or replacement card has not arrived after 10 working days?"


def is_unanchored_overdue_delivery_followup(question, history):
    """Identify an overdue-arrival ellipsis that has no card term or immediate delivery antecedent."""
    if not _OVERDUE_DELIVERY_FOLLOWUP.search(question or ""):
        return False
    if _CARD_WORDS.search(question):
        return False
    return resolve_card_delivery_followup(question, history) is None
