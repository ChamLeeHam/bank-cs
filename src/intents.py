# 意图的定义都放在这里，做数据集、意图识别和 gate 分流都用这个文件
# 更详细的说明在 data/intent_design.md
import re

# 意图 -> 中文名、说明、分支、必填信息
INTENTS: dict[str, dict] = {
    # account：要验证 OTP，查客户自己的数据
    "balance_inquiry": {"zh": "余额查询", "route": "account", "slots": [],
                        "desc": "查询本人账户余额"},
    "transfer_status": {"zh": "汇款进度", "route": "account", "slots": [],
                        "desc": "查询本人某笔转账/汇款是否到账、为什么失败（有多笔时由账户智能体追问）"},
    "card_lost_freeze": {"zh": "挂失冻结", "route": "account", "slots": ["card_last4"],
                         "desc": "银行卡丢失/被盗，需要冻结"},
    "card_transaction_inquiry": {"zh": "最近银行卡交易", "route": "account", "slots": [],
                                 "desc": "查询本人最近一笔银行卡交易"},
    # rag：一般性的问题，查知识库
    "fees_rates": {"zh": "费用汇率", "route": "rag", "slots": [],
                   "desc": "手续费、汇率、为什么收费"},
    "transfer_howto": {"zh": "转账指南", "route": "rag", "slots": [],
                       "desc": "怎么转账/收款、一般多久到账、收款人限制（不涉及本人某笔交易）"},
    "card_service": {"zh": "银行卡服务", "route": "rag", "slots": [],
                     "desc": "激活、寄送、PIN、过期换卡、卡不能用、ATM 吞卡"},
    "account_service": {"zh": "账户服务", "route": "rag", "slots": [],
                        "desc": "开户条件、身份验证、修改资料、忘记密码、销户、多币种"},
    # handoff：转人工
    "fraud_report": {"zh": "盗刷举报", "route": "handoff", "slots": [], "priority": "urgent",
                     "desc": "有不是本人做的交易/取现/扣款，或怀疑卡被盗用"},
    "dispute_refund": {"zh": "争议退款", "route": "handoff", "slots": [],
                       "desc": "本人做的交易但有问题：重复扣款、退款未到、金额或汇率不对"},
    "complaint": {"zh": "投诉", "route": "handoff", "slots": [],
                  "desc": "主要表达不满（是否要求转人工由 human_requested 单独判断，不是意图）"},
    # refuse：拒答；greeting：欢迎语（复用固定回复节点）
    "out_of_scope": {"zh": "超出范围", "route": "refuse", "slots": [],
                     "desc": "投资建议、闲聊、其他公司、与银行业务无关"},
    "greeting": {"zh": "问候", "route": "greeting", "slots": [],
                 "desc": "打招呼（用规则识别，不需要训练数据）"},
}

INTENT_NAMES = list(INTENTS)
INTENT_ROUTES = {name: info["route"] for name, info in INTENTS.items()}

# Banking77 的标签对应到我们的意图，没列出来的 26 个标签不用
BANKING77_TO_INTENT: dict[str, str] = {
    # transfer_status
    "balance_not_updated_after_bank_transfer": "transfer_status",
    "declined_transfer": "transfer_status",
    "failed_transfer": "transfer_status",
    "pending_transfer": "transfer_status",
    "transfer_not_received_by_recipient": "transfer_status",
    # card_lost_freeze
    "lost_or_stolen_card": "card_lost_freeze",
    # fees_rates
    "card_payment_fee_charged": "fees_rates",
    "cash_withdrawal_charge": "fees_rates",
    "exchange_charge": "fees_rates",
    "exchange_rate": "fees_rates",
    "extra_charge_on_statement": "fees_rates",
    "transfer_fee_charged": "fees_rates",
    # transfer_howto
    "beneficiary_not_allowed": "transfer_howto",
    "receiving_money": "transfer_howto",
    "transfer_into_account": "transfer_howto",
    "transfer_timing": "transfer_howto",
    # card_service
    "activate_my_card": "card_service",
    "atm_support": "card_service",
    "card_about_to_expire": "card_service",
    "card_acceptance": "card_service",
    "card_arrival": "card_service",
    "card_delivery_estimate": "card_service",
    "card_not_working": "card_service",
    "card_swallowed": "card_service",
    "change_pin": "card_service",
    "contactless_not_working": "card_service",
    "declined_card_payment": "card_service",
    "declined_cash_withdrawal": "card_service",
    "get_physical_card": "card_service",
    "getting_spare_card": "card_service",
    "order_physical_card": "card_service",
    "pin_blocked": "card_service",
    "visa_or_mastercard": "card_service",
    # account_service
    "age_limit": "account_service",
    "edit_personal_details": "account_service",
    "fiat_currency_support": "account_service",
    "passcode_forgotten": "account_service",
    "terminate_account": "account_service",
    "unable_to_verify_identity": "account_service",
    "verify_my_identity": "account_service",
    "why_verify_identity": "account_service",
    # fraud_report
    "card_payment_not_recognised": "fraud_report",
    "cash_withdrawal_not_recognised": "fraud_report",
    "compromised_card": "fraud_report",
    "direct_debit_payment_not_recognised": "fraud_report",
    # dispute_refund
    "Refund_not_showing_up": "dispute_refund",
    "request_refund": "dispute_refund",
    "transaction_charged_twice": "dispute_refund",
    "wrong_amount_of_cash_received": "dispute_refund",
    "card_payment_wrong_exchange_rate": "dispute_refund",
    "wrong_exchange_rate_for_cash_withdrawal": "dispute_refund",
}

# Banking77 里没有的意图，要用 LLM 生成数据（greeting 用规则，不用生成）
GENERATE_ONLY = ["balance_inquiry", "complaint", "out_of_scope"]
# Banking77 里数据太少的意图，用 LLM 补一些
NEEDS_TOP_UP = ["card_lost_freeze"]
# 每个意图最多用 300 条，免得 card_service 这种大类太多
MAX_PER_INTENT = 300

# 二级意图
# Banking77 来的意图，二级意图就是 Banking77 原来的标签
# Banking77 没有的意图，在下面手动定义，按二级意图分别生成数据
# 翻译成中文和中英混杂的句子，二级意图跟英文原句一样
# gate 只看一级意图，二级意图用在检索、统计和评估
GENERATED_SUB_INTENTS: dict[str, list[str]] = {
    "balance_inquiry": ["check_balance"],
    "card_lost_freeze": ["lost_or_stolen_card"],  # 和 Banking77 的标签同名
    "complaint": ["waiting_time", "staff_attitude", "app_problem", "unfair_fee"],
    "out_of_scope": ["investment_advice", "chitchat", "other_company", "non_banking"],
    "greeting": ["hello"],
}

LABEL_SEP = "/"  # 模型输出的格式是 一级意图/二级意图，比如 card_service/pin_blocked

# 微调用的系统提示词，训练和使用的时候要一模一样
INTENT_SYSTEM = "你是狮城银行客服系统的意图分类器。判断客户消息的意图，只输出“一级意图/二级意图”，不要输出其他内容。"

# 要求转人工
# "我的卡丢了，帮我转人工" 既是挂失，又要转人工。如果把转人工也当成一个意图，模型只能选一个，
# 所以单独用规则判断，意图照常识别。
# 只放不容易误判的说法，单独一个"人工"不行，"人工智能"也会匹配上
HUMAN_REQUEST_RE = re.compile(
    r"转人工|人工客服|找人工|要人工|真人客服|"
    r"human agent|live agent|(want|need|get) (a )?real person|"
    r"(speak|talk) to (a |an )?(human|person|agent|someone|staff|officer)",
    re.IGNORECASE,
)


def is_human_request(text: str) -> bool:
    return bool(HUMAN_REQUEST_RE.search(text or ""))


# 别的银行的名字，gate 用来判断"信用卡年费"问的是不是本行
# 训练数据里提到信用卡年费的句子都带着别的银行（HSBC、OCBC…），标的是 out_of_scope/other_company，
# 模型就学成了"信用卡 + 年费 = 别的银行"。没提别的银行、图谱又找到了本行的产品，就是问本行
# 生成数据的脚本里也有一份（build_intent_dataset.py 的 _OTHER_BANK），那份不改，免得重新生成的数据变了
OTHER_BANK_RE = re.compile(
    r"\b(POSB|DBS|OCBC|UOB|Citi\w*|HSBC|Maybank|Standard Chartered|Revolut|CIMB|RHB|Trust Bank|GXS|MariBank|Amex|"
    r"American Express)\b|星展|华侨银行|大华银行|汇丰|花旗|渣打|马来亚银行|联昌",
    re.IGNORECASE,
)


def mentions_other_bank(text: str) -> bool:
    return bool(OTHER_BANK_RE.search(text or ""))


def sub_intents_of(intent: str) -> list[str]:
    """一个一级意图下面所有的二级意图"""
    subs = [label for label, i in BANKING77_TO_INTENT.items() if i == intent]
    for s in GENERATED_SUB_INTENTS.get(intent, []):
        if s not in subs:
            subs.append(s)
    return subs


def full_label(intent: str, sub_intent: str) -> str:
    return f"{intent}{LABEL_SEP}{sub_intent}"


def split_label(label: str) -> tuple[str, str]:
    """'card_service/pin_blocked' 拆成 ('card_service', 'pin_blocked')"""
    intent, _, sub = label.strip().partition(LABEL_SEP)
    return intent.strip(), sub.strip()


# 模型可能输出的全部标签
ALL_LABELS = [full_label(i, s) for i in INTENTS for s in sub_intents_of(i)]


def zero_shot_system() -> str:
    """给没微调过的模型用的提示词，把所有标签都列出来（做对比实验用）"""
    lines = [INTENT_SYSTEM, "", "可选的标签（只能从中选择一个）："]
    for intent, info in INTENTS.items():
        if intent == "greeting":
            continue
        lines.append(f"- {intent}（{info['zh']}：{info['desc']}）")
        lines += [f"    {full_label(intent, s)}" for s in sub_intents_of(intent)]
    return "\n".join(lines)


def route_of(intent: str) -> str:
    return INTENT_ROUTES.get(intent, "rag")


def required_slots(intent: str) -> list[str]:
    return INTENTS.get(intent, {}).get("slots", [])


if __name__ == "__main__":
    assert set(BANKING77_TO_INTENT.values()) <= set(INTENTS), "映射到了不存在的意图"
    assert set(GENERATED_SUB_INTENTS) <= set(INTENTS), "二级意图挂到了不存在的一级意图"
    assert all(sub_intents_of(i) for i in INTENTS), "有一级意图没有任何二级意图"
    assert split_label("card_service/pin_blocked") == ("card_service", "pin_blocked")
    assert len(ALL_LABELS) == len(set(ALL_LABELS)), "标签重复"
    for text in ("我的卡丢了，帮我转人工", "Can I speak to a human?", "I want a live agent", "I need a real person"):
        assert is_human_request(text), text
    for text in ("你们的人工智能客服太差了", "How do I transfer money?", "人工费怎么算", "我要投诉你们的客服人员",
                 "are you a real person or a bot ah"):
        assert not is_human_request(text), text

    print(f"{len(INTENTS)} 个一级意图，{len(ALL_LABELS)} 个二级意图（其中 {len(BANKING77_TO_INTENT)} 个来自 Banking77）")
    for name, info in INTENTS.items():
        subs = sub_intents_of(name)
        print(f"  {name:<17} -> {info['route']:<8} 二级 {len(subs):>2} 个: {', '.join(subs[:4])}{' …' if len(subs) > 4 else ''}")
