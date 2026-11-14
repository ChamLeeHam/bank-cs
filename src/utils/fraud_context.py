"""识别当前消息中的盗刷报告、否定和假设咨询。"""
import re


_CLAUSE_BREAK = re.compile(
    r"[，,；;。！？!?\n]|(?<!\d)\.(?=\s|$)|(?:但是|但|不过|然而)|\b(?:but|however)\b",
    re.IGNORECASE,
)
_HYPOTHETICAL = re.compile(
    r"如果|假如|假设|万一|\b(?:if|hypothetically|for future reference|in case)\b", re.IGNORECASE,
)
_PROCEDURE = re.compile(r"怎么办|如何处理|怎么处理|如何举报|\bhow (?:do|should|can) (?:i|we) report\b", re.IGNORECASE)
_NEW_REQUEST = re.compile(r"(?:我)?(?:只是|只想|想问)|\b(?:i only want|i am asking|i just want|how much)\b", re.IGNORECASE)
_DENIAL = re.compile(
    r"(?:没有|没|并无|未发现)\s*(?:发生|出现|被|遭遇)?\s*"
    r"(?:盗刷|盗用|未[经]?授权|fraud\b|unauthori[sz]ed|非本人交易)|"
    r"\b(?:no|not|without|never had|did not have|didn't have)\s+(?:any\s+)?"
    r"(?:used fraudulently|used without authori[sz]ation|fraud\w*|unauthori[sz]ed)\b",
    re.IGNORECASE,
)
_RISK_TOPIC = re.compile(
    r"盗刷|盗用|非本人|未(?:经(?:我|本人)?)?授权|从未授权|"
    r"\b(?:fraud\w*|unauthori[sz]ed|without (?:my )?(?:permission|authori[sz]ation))\b",
    re.IGNORECASE,
)
_REPORT = re.compile(
    r"(?:被(?:人)?|遭到?|发现.{0,8})(?:盗刷|盗用)|"
    r"未经(?:我|本人)?授权|从未授权|非本人(?:交易|消费|扣款)|不认识这笔(?:交易|消费|扣款)|"
    r"(?:交易|消费|扣款|支付).{0,12}不是我(?:本人)?(?:做|付|刷|授权|支付|操作)的?|"
    r"不是我(?:本人)?(?:的(?:交易|消费|扣款)|(?:做|付|刷|授权|支付|操作)的)|"
    r"(?:有人|别人|他人).{0,12}(?:提取|取走|取了|提了|提走).{0,20}(?:现金|钱|英镑|美元|人民币)|"
    r"(?:交易|扣款|消费|支付).{0,12}\bnot\s+authori[sz]ed\s+by\s+me\b|"
    r"\b(?:unauthori[sz]ed (?:card |debit |credit )?(?:payment|transaction|charge|withdrawal)|"
    r"not mine|without (?:my )?(?:permission|authori[sz]ation))\b|"
    r"\b(?:transaction|payment|charge|withdrawal)\s+(?:was|is)\s+unauthori[sz]ed\b|"
    r"\b(?:didn't|did not|never)\s+(?:make|authori[sz]e)\b|"
    r"\b(?:purchase|transaction|payment|charge|withdrawal)\b.{0,60}"
    r"\bi (?:never|did not|didn't) (?:made|make|authori[sz]e[ds]?)\b|"
    r"\bsomeone else (?:made|initiated) (?:this|that|the) (?:payment|transaction|withdrawal)\b|"
    r"我(?:本人)?没有.{0,14}(?:进行|完成|授权)(?:那笔|这笔)?(?:卡)?(?:交易|消费|扣款)|"
    r"\b(?:didn't|did not|never)\s+(?:take out|withdraw)\s+(?:any\s+)?(?:money|cash)\b|"
    r"\b(?:transaction|payment|charge|amount|withdrawal)\b[^.!?]{0,100}"
    r"\bnot\s+(?:made|completed|authori[sz]ed)\s+by\s+me\b|"
    r"\b(?:don't|do not) recogni[sz]e\s+(?:(?:this|that|these|those|the|a|an|some)\s+)?"
    r"(?:card\s+)?(?:transactions?|payments?|charges?|withdrawals?)\b",
    re.IGNORECASE,
)


def fraud_context(text):
    """返回风险判断和剔除已否认风险后仍需分类的内容。"""
    remaining = []
    hypothetical = False
    normalized = text.replace("’", "'")
    future_reference = bool(re.match(r"^\s*(?:for future reference\b|一般来说|只是了解一下)", normalized, re.IGNORECASE))
    for clause in _CLAUSE_BREAK.split(normalized):
        clause = clause.strip()
        if not clause:
            continue
        if (_HYPOTHETICAL.search(clause) or (future_reference and _PROCEDURE.search(clause))) and (
                _RISK_TOPIC.search(clause) or _REPORT.search(clause)):
            hypothetical = True
            continue
        denial = _DENIAL.search(clause)
        if denial:
            # 保留同一分句后面的独立风险报告或明确业务问题。
            tail = clause[denial.end():]
            if _REPORT.search(clause[:denial.start()]) or _REPORT.search(tail):
                return "reported", text
            followup = _NEW_REQUEST.search(tail)
            if followup:
                remaining.append(tail[followup.start():].strip())
            continue
        if _REPORT.search(clause):
            return "reported", text
        remaining.append(clause)
    if hypothetical:
        return "hypothetical", text
    if re.search(r"我(?:本人)?没(?:有)?在.{1,20}(?:买|消费|付款)过?.{0,12}[，,]\s*"
                 r"(?:(?:银行卡|信用卡|借记卡|卡).{0,8})?(?:却|但|仍).{0,12}(?:出现|显示|有).{0,20}(?:消费|扣款|交易)",
                 normalized):
        return "reported", text
    if not remaining:
        return "none", ""
    if _DENIAL.search(text):
        return "none", " ".join(remaining)
    return "none", text
