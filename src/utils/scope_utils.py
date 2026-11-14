"""Detect product areas that are explicitly outside this demo knowledge base."""
import re


_LENDING_TERMS = re.compile(
    r"\b(?:mortgages?|home loans?|housing loans?|property loans?|hdb loans?|"
    r"personal loans?|car loans?|student loans?|loans?)\b",
    re.IGNORECASE,
)
_LENDING_TERMS_ZH = re.compile(r"房贷|房屋贷款|住房贷款|按揭|购房贷款|房屋抵押贷款")
_BUSINESS_ACCOUNT_TERMS = re.compile(
    r"\b(?:business|corporate|company|commercial)\s+(?:banking\s+)?accounts?\b",
    re.IGNORECASE,
)
_BUSINESS_ACCOUNT_TERMS_ZH = re.compile(
    r"企业(?:银行)?账户|企业(?:银行)?帐户|公司(?:银行)?账户|公司(?:银行)?帐户|"
    r"商业(?:银行)?账户|商业(?:银行)?帐户|商务(?:银行)?账户|商务(?:银行)?帐户"
)
_TRANSACTION_DISPUTE_TERMS = re.compile(
    r"\bchargebacks?\b|\bdisput(?:e|ed|ing)\s+(?:a\s+)?(?:card|credit card|bank|transaction|payment)\b|"
    r"\b(?:charged twice|double charged|charged two times)\b|"
    r"\b(?:wrong|incorrect) amount\b.{0,30}\b(?:charge|payment|transaction|card|cash)\b|"
    r"\b(?:charge|payment|transaction|card|cash)\b.{0,30}\b(?:wrong|incorrect) amount\b|"
    r"\brefund\s+(?:hasn't|has not|didn't|did not|not)\s*(?:arrived|shown up|appeared|been received)\b",
    re.IGNORECASE,
)
_TRANSACTION_DISPUTE_TERMS_ZH = re.compile(
    r"争议退款|拒付|交易争议|重复扣款|重复收费|退款.{0,8}(?:未到账|没到账|未收到|没收到|还没到)|"
    r"金额不符|金额不对|金额有误|多扣了?钱|扣错金额"
)


def is_unsupported_lending_question(question):
    """Return True for lending-product questions absent from the demo FAQ corpus."""
    text = str(question or "")
    return bool(_LENDING_TERMS.search(text) or _LENDING_TERMS_ZH.search(text))


def unsupported_scope_reason(question):
    """Return the reason a question is outside the supported demo FAQ, if any."""
    text = str(question or "")
    if _LENDING_TERMS.search(text) or _LENDING_TERMS_ZH.search(text):
        return "unsupported_lending"
    if _BUSINESS_ACCOUNT_TERMS.search(text) or _BUSINESS_ACCOUNT_TERMS_ZH.search(text):
        return "unsupported_business_banking"
    if _TRANSACTION_DISPUTE_TERMS.search(text) or _TRANSACTION_DISPUTE_TERMS_ZH.search(text):
        return "unsupported_transaction_dispute"
    return None


def is_unsupported_scope_question(question):
    """Return True when the question is about a product area absent from this FAQ."""
    return unsupported_scope_reason(question) is not None
