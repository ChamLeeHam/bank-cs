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
_NEW_REQUEST = re.compile(r"(?:我)?(?:只是|只想|想问)|\b(?:i only want|i am asking|i just want|how much|only asking)\b", re.IGNORECASE)
_GENERAL = re.compile(
    r"防止|预防|防范|怎么分辨|是什么意思|朋友.{0,24}(?:他的卡|她的卡)|"
    r"\b(?:prevent|protect against|how do i tell|what does .{0,20} mean)\b", re.IGNORECASE,
)
_UNFAMILIAR = re.compile(
    r"(?:不认识|认不出|不熟悉).{0,12}(?:交易|扣款|消费)|"
    r"\b(?:transaction|payment|charge|withdrawal)s?\b.{0,100}\bi (?:don'?t|do not) recogni[sz]e\b",
    re.IGNORECASE,
)
_CARD_SAFETY = re.compile(r"(?:银行卡|信用卡|借记卡)安全.{0,16}(?:注意|建议|防范)", re.IGNORECASE)
_ACTION_STATUS = re.compile(
    r"(?:卡.{0,18}(?:已经|被)冻结了吗|(?:额度|限额|limit).{0,12}(?:已经|已)(?:改|调整)了?吗)|"
    r"\b(?:has my card been frozen|is my card (?:now |already )?frozen|has my limit been changed)\b",
    re.IGNORECASE,
)
_DISPUTE_COMPARISON = re.compile(
    r"有什么区别|区别是什么|有什么不同|怎么区分|如何区分|"
    r"what(?:'s| is) the difference|how do .{0,30} differ",
    re.IGNORECASE,
)
_FRAUD_COMPARISON_SIDE = re.compile(
    r"盗刷|盗用|非本人(?:交易|消费)?|未经授权|(?:没|没有|未)授权|"
    r"\b(?:fraud|unauthori[sz]ed|not authorized|not authorised)\b",
    re.IGNORECASE,
)
_MERCHANT_COMPARISON_SIDE = re.compile(
    r"(?:本人|自己|商户|商品|消费|退款|重复扣款)|"
    r"\b(?:merchant|purchase|refund|goods|services|charged twice)\b",
    re.IGNORECASE,
)
_URGENT_FRAUD_ACTION = re.compile(
    r"帮我|请.{0,5}(?:冻结|挂失|举报|报告)|立即(?:冻结|挂失|举报|报告)|马上(?:冻结|挂失|举报|报告)|"
    r"转人工|freeze my card|block my card|report (?:this|the) transaction|"
    r"\b(?:urgent|immediately freeze|immediately block)\b",
    re.IGNORECASE,
)
_MERCHANT_NAME_MENTION = re.compile(
    r"(?:商户|商家).{0,12}(?:名称|名字|显示)|(?:名称|名字).{0,12}(?:商户|商家)|"
    r"\b(?:merchant|billing descriptor)\b.{0,20}\bname\b",
    re.IGNORECASE,
)
_MERCHANT_NAME_UNFAMILIAR = re.compile(
    r"不认识|认不出|不熟悉|对不上|看不懂|为什么|为何|怎么回事|"
    r"unfamiliar|don't recognize|do not recognize|not recognise|not sure|why",
    re.IGNORECASE,
)


def fraud_dispute_comparison_question(text):
    """识别把疑似盗刷与本人交易争议作概念比较的问题；显式紧急操作仍交给风险路由。"""
    text = text or ""
    return bool(_DISPUTE_COMPARISON.search(text)
                and _FRAUD_COMPARISON_SIDE.search(text)
                and _MERCHANT_COMPARISON_SIDE.search(text))


def fraud_comparison_requests_action(text):
    return bool(_URGENT_FRAUD_ACTION.search(text or ""))


def unfamiliar_merchant_name_question(text):
    text = text or ""
    return bool(_MERCHANT_NAME_MENTION.search(text) and _MERCHANT_NAME_UNFAMILIAR.search(text))


def fraud_action_status_question(text, fraud_ticket_id):
    return bool(fraud_ticket_id and _ACTION_STATUS.search(text))
_DENIAL = re.compile(
    r"(?:没有|没|并无|未发现|不是)\s*(?:发生|出现|被|遭遇)?\s*"
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
    r"(?:被(?:人)?|遭到?|发现.{0,8})(?:盗刷|盗用)|未经我(?:本人)?允许.{0,12}(?:刷|用).{0,8}(?:卡)|"
    r"未经(?:我|本人)?授权|从未授权|非本人(?:交易|消费|扣款)|"
    r"(?:交易|消费|扣款|支付).{0,12}不是我(?:本人)?(?:做|付|刷|授权|支付|操作)的?|"
    r"不是我(?:本人)?(?:的(?:交易|消费|扣款)|(?:做|付|刷|授权|支付|操作)的)|"
    r"不是我(?:本人)?取的|我(?:根本|从来)?没刷过的(?:网购|消费|交易)|"
    r"(?:有人|别人|他人).{0,12}(?:用|拿).{0,12}(?:卡).{0,12}\bwithdraw\b|"
    r"(?:有人|别人|他人).{0,12}(?:提取|取走|取了|提了|提走).{0,20}(?:现金|钱|英镑|美元|人民币)|"
    r"(?:交易|扣款|消费|支付).{0,12}\bnot\s+authori[sz]ed\s+by\s+me\b|"
    r"\b(?:unauthori[sz]ed (?:card |debit |credit )?(?:payment|transaction|charge|withdrawal)|"
    r"not mine|without (?:my )?(?:permission|authori[sz]ation))\b|"
    r"\b(?:transaction|payment|charge|withdrawal)\s+(?:was|is)\s+unauthori[sz]ed\b|"
    r"\b(?:didn'?t|did not|never)\s+(?:make|authori[sz]e)\b|"
    r"\bsomeone withdrew cash from my account\b|"
    r"\bwithout (?:me knowing|my knowledge)\b|"
    r"\b(?:purchase|transaction|payment|charge|withdrawal)\b.{0,60}"
    r"\bi (?:never|did not|didn't) (?:made|make|authori[sz]e[ds]?)\b|"
    r"\bsomeone else (?:made|initiated) (?:this|that|the) (?:payment|transaction|withdrawal)\b|"
    r"我(?:本人)?没有.{0,14}(?:进行|完成|授权)(?:那笔|这笔)?(?:卡)?(?:交易|消费|扣款)|"
    r"\b(?:didn't|did not|never)\s+(?:take out|withdraw)\s+(?:any\s+)?(?:money|cash)\b|"
    r"\b(?:transaction|payment|charge|amount|withdrawal)\b[^.!?]{0,100}"
    r"\bnot\s+(?:made|completed|authori[sz]ed)\s+by\s+me\b|"
    r"\b(?:don't|do not) recogni[sz]e this card transaction\b|"
    r"\b(?:don't|do not) recogni[sz]e\s+(?:(?:this|that|these|those|the|a|an|some)\s+)?"
    r"(?:card\s+)?(?:transactions?|payments?|charges?|withdrawals?)\b.{0,60}\b(?:used my card|got my card details|dispute it)\b",
    re.IGNORECASE,
)


_RECORDED_UNAUTHORIZED_EVENT = re.compile(
    r"(?:\b(?:card payment|card transaction|cash withdrawal)\b.{0,70}"
    r"\bi (?:never|did not|didn't) authori[sz]e(?:d)? (?:it|that)\b)|"
    r"(?:\bi (?:never|did not|didn't) use(?:d)? (?:that|the) atm\b.{0,80}"
    r"\bmy account (?:shows|lists|has) (?:a |the )?(?:cash )?withdrawal\b)|"
    r"(?:(?:我)?(?:没(?:有)?|从未)去过.{0,12}(?:柜员机|ATM).{0,25}"
    r"(?:账上|账单|账户).{0,12}(?:出现|显示|有).{0,12}(?:取款|提现))",
    re.IGNORECASE,
)


def fraud_context(text):
    """返回风险判断和剔除已否认风险后仍需分类的内容。"""
    remaining = []
    hypothetical = False
    normalized = text.replace("’", "'")
    future_reference = bool(re.match(r"^\s*(?:for future reference\b|一般来说|只是了解一下)", normalized, re.IGNORECASE))
    if re.search(r"我.{0,20}(?:根本|完全)?没刷卡\s*[，,]\s*(?:但是|但|却).{0,20}(?:扣款|消费)(?:短信|通知)",
                 normalized) and not _HYPOTHETICAL.search(normalized):
        return "reported", text
    if not _HYPOTHETICAL.search(normalized) and _RECORDED_UNAUTHORIZED_EVENT.search(normalized):
        return "reported", text
    for clause in _CLAUSE_BREAK.split(normalized):
        clause = clause.strip()
        if not clause:
            continue
        if (_HYPOTHETICAL.search(clause) or _GENERAL.search(clause) or (future_reference and _PROCEDURE.search(clause))) and (
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
            if denial.start() and _NEW_REQUEST.search(clause[:denial.start()]):
                remaining.append(clause[:denial.start()].strip())
            elif re.search(r"额度|限额|\blimit\b|年费|手续费|\bfee\b", clause[:denial.start()], re.I):
                remaining.append(clause[:denial.start()].strip())
            continue
        if _REPORT.search(clause):
            return "reported", text
        remaining.append(clause)
    if hypothetical or _UNFAMILIAR.search(normalized) or _CARD_SAFETY.search(normalized):
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
