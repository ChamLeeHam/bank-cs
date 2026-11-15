"""Resolve explicit mentions of banks represented in the isolated reference corpus."""
import re


REFERENCE_BANKS = (
    {
        "key": "bofa", "doc_prefix": "lcb__ref__bofa", "name_zh": "Bank of America", "name_en": "Bank of America",
        "jurisdiction_zh": "美国", "jurisdiction_en": "United States",
        # ASCII boundaries are deliberate: Python's \b treats adjacent Chinese characters as word chars.
        "patterns": (r"(?<![A-Za-z0-9])bank of america(?![A-Za-z0-9])",
                     r"(?<![A-Za-z0-9])bofa(?![A-Za-z0-9])",
                     r"(?<![A-Za-z0-9])boa(?![A-Za-z0-9])", r"美国银行"),
    },
    {
        "key": "maybank", "doc_prefix": "lcb__ref__maybank", "name_zh": "Maybank", "name_en": "Maybank",
        "jurisdiction_zh": "新加坡", "jurisdiction_en": "Singapore",
        "patterns": (r"(?<![A-Za-z0-9])maybank(?![A-Za-z0-9])",
                     r"(?<![A-Za-z0-9])malayan banking(?![A-Za-z0-9])", r"马来亚银行", r"马来银行"),
    },
    {
        "key": "standard_chartered", "doc_prefix": "lcb__ref__sc_", "name_zh": "渣打银行", "name_en": "Standard Chartered",
        "jurisdiction_zh": "新加坡", "jurisdiction_en": "Singapore",
        "patterns": (r"(?<![A-Za-z0-9])standard\s+chartered(?![A-Za-z0-9])",
                     r"(?<![A-Za-z0-9])scb(?![A-Za-z0-9])", r"渣打(?:银行)?"),
    },
    {
        "key": "citi", "doc_prefix": "lcb__ref__citi", "name_zh": "花旗银行", "name_en": "Citi",
        "jurisdiction_zh": "新加坡", "jurisdiction_en": "Singapore",
        "patterns": (r"(?<![A-Za-z0-9])citibank(?![A-Za-z0-9])",
                     r"(?<![A-Za-z0-9])citi(?![A-Za-z0-9])", r"花旗(?:银行)?"),
    },
    {
        "key": "deutsche", "doc_prefix": "lcb__ref__deutsche", "name_zh": "德意志银行财富管理", "name_en": "Deutsche Bank Wealth Management",
        "jurisdiction_zh": "新加坡 / 香港服务范围", "jurisdiction_en": "Singapore / Hong Kong service scope",
        "patterns": (r"(?<![A-Za-z0-9])deutsche(?:\s+bank)?(?![A-Za-z0-9])", r"德意志(?:银行)?"),
    },
    {
        "key": "ubs", "doc_prefix": "lcb__ref__ubs", "name_zh": "UBS", "name_en": "UBS",
        "jurisdiction_zh": "瑞士", "jurisdiction_en": "Switzerland",
        "patterns": (r"(?<![A-Za-z0-9])ubs(?![A-Za-z0-9])", r"瑞银(?:集团)?"),
    },
    {
        "key": "dbs", "doc_prefix": "lcb__ref__dbs", "name_zh": "星展银行", "name_en": "DBS",
        "jurisdiction_zh": "新加坡", "jurisdiction_en": "Singapore",
        "patterns": (r"(?<![A-Za-z0-9])dbs(?![A-Za-z0-9])", r"星展(?:银行)?"),
    },
    {
        "key": "uob", "doc_prefix": "lcb__ref__uob", "name_zh": "大华银行", "name_en": "UOB",
        "jurisdiction_zh": "新加坡", "jurisdiction_en": "Singapore",
        "patterns": (r"(?<![A-Za-z0-9])uob(?![A-Za-z0-9])", r"大华(?:银行)?"),
    },
)


def detect_reference_bank(question: str) -> dict | None:
    """Return a known bank only when the user explicitly names it."""
    text = str(question or "")
    for bank in REFERENCE_BANKS:
        if any(re.search(pattern, text, re.IGNORECASE) for pattern in bank["patterns"]):
            return bank
    return None


def resolve_reference_bank(question: str, history=None) -> dict | None:
    """Use an explicit bank mention, or inherit the most recent customer-mentioned bank."""
    bank = detect_reference_bank(question)
    if bank:
        return bank
    if _LOCAL_BANK_TOPIC.search(str(question or "")):
        return None
    for role, text in reversed(list(history or [])[-6:]):
        if role == "客户":
            bank = detect_reference_bank(text)
            if bank:
                return bank
    return None


_REFERENCE_QUESTION = re.compile(
    r"\b(?:what|how|which|where|when|does|do|can|could|explain|tell me|guide|form|process|policy|procedure|"
    r"fee|fees|charge|charges|pricing|dispute|fraud|unauthori[sz]ed)\b|"
    r"什么|怎么|如何|哪些|哪种|哪里|何时|解释|介绍|流程|政策|规定|表格|资料|费用|收费|手续费|费率|"
    r"争议|盗刷处理|挂失流程|未经授权处理",
    re.IGNORECASE,
)
_REFERENCE_CONTEXT_FOLLOWUP = re.compile(
    r"\b(?:if|then|and|what about|in that case)\b.{0,45}\b(?:card|still have|in my possession|materials|documents|evidence)\b|"
    r"(?:如果|假如|那|那么|还有|接着).{0,24}(?:卡|手里|材料|证据|流程|处理|申请)|"
    r"(?:还在.{0,8}手里|仍在.{0,8}手里|需要.{0,8}(?:材料|证据)|第[一二三四五六七八九\d]+(?:个|项))",
    re.IGNORECASE,
)
_LOCAL_BANK_TOPIC = re.compile(
    r"狮城银行|本行|Lion City Bank|海外电汇|电汇|Overseas TT|PayNow|\bFAST\b|\bGIRO\b|"
    r"\bSGD\s*\d|新币\s*\d",
    re.IGNORECASE,
)

_REFERENCE_FEE_QUESTION = re.compile(
    r"\b(?:fee|fees|charge|charges|pricing|cost|rate)\b|费用|收费|手续费|费率|价格|收费标准",
    re.IGNORECASE,
)
_REFERENCE_PERSONAL_INCIDENT = re.compile(
    r"\b(?:my|our)\b.{0,36}\b(?:card|account)\b.{0,24}\b(?:lost|stolen|missing|fraud|compromised)\b|"
    r"(?:我的|本人).{0,12}(?:银行卡|信用卡|借记卡|卡片).{0,12}(?:丢|遗失|被盗|盗刷)|"
    r"(?:帮我|请.{0,5})(?:立即)?(?:冻结|挂失|举报盗刷)",
    re.IGNORECASE,
)


def is_reference_policy_question(question: str, reference_bank=None) -> bool:
    """Identify general policy/document questions suitable for the external reference RAG path."""
    text = str(question or "")
    explicit_bank = detect_reference_bank(text)
    if (reference_bank or explicit_bank) is None:
        return False
    contextual_followup = bool(reference_bank and not explicit_bank and _REFERENCE_CONTEXT_FOLLOWUP.search(text))
    if not contextual_followup and not _REFERENCE_QUESTION.search(text):
        return False
    # A personal incident or explicit freeze/report request must stay on the urgent local safety path.
    if _REFERENCE_PERSONAL_INCIDENT.search(text):
        return False
    # Leave personal transfer/account status checks to normal verification and account routing.
    if re.search(r"\b(?:where is|track|check the status|status of|has my .* transfer|my .* transfer .* arrived)\b|"
                 r"\bmy\b.{0,24}\b(?:balance|transfer|payment status|transaction status|latest transaction)\b|"
                 r"(?:查|查询).{0,12}(?:余额|到账|进度|状态)|"
                 r"(?:我的|本人).{0,12}(?:余额|转账|汇款|付款|交易).{0,12}(?:哪里|何时|到账|进度|状态)|"
                 r"(?:我的|本人).{0,12}(?:汇款进度|转账进度|这笔交易状态)",
                 text, re.IGNORECASE):
        return False
    return True


def reference_question_intent(question: str) -> str:
    """Pick a RAG intent for the existing router; this does not authorize account actions."""
    return "fees_rates" if _REFERENCE_FEE_QUESTION.search(str(question or "")) else "card_service"
