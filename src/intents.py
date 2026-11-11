"""
意图定义（唯一来源）：数据集构建、意图路由、gate 分流都从这里读取
详细说明与边界规则见 data/intent_design.md
"""

# 意图 -> 中文名、说明、分支、必填槽位
INTENTS: dict[str, dict] = {
    # ---------- account：需要 OTP + 查询客户本人数据 ----------
    "balance_inquiry": {"zh": "余额查询", "route": "account", "slots": [],
                        "desc": "查询本人账户余额"},
    "transfer_status": {"zh": "汇款进度", "route": "account", "slots": [],
                        "desc": "查询本人某笔转账/汇款是否到账、为什么失败（有多笔时由账户智能体追问）"},
    "card_lost_freeze": {"zh": "挂失冻结", "route": "account", "slots": ["card_last4"],
                         "desc": "银行卡丢失/被盗，需要冻结"},
    # ---------- rag：一般性问题，查知识库 ----------
    "fees_rates": {"zh": "费用汇率", "route": "rag", "slots": [],
                   "desc": "手续费、汇率、为什么收费"},
    "transfer_howto": {"zh": "转账指南", "route": "rag", "slots": [],
                       "desc": "怎么转账/收款、一般多久到账、收款人限制（不涉及本人某笔交易）"},
    "card_service": {"zh": "银行卡服务", "route": "rag", "slots": [],
                     "desc": "激活、寄送、PIN、过期换卡、卡不能用、ATM 吞卡"},
    "account_service": {"zh": "账户服务", "route": "rag", "slots": [],
                        "desc": "开户条件、身份验证、修改资料、忘记密码、销户、多币种"},
    # ---------- handoff：转人工 ----------
    "fraud_report": {"zh": "盗刷举报", "route": "handoff", "slots": [], "priority": "urgent",
                     "desc": "有不是本人做的交易/取现/扣款，或怀疑卡被盗用"},
    "dispute_refund": {"zh": "争议退款", "route": "handoff", "slots": [],
                       "desc": "本人做的交易但有问题：重复扣款、退款未到、金额或汇率不对"},
    "complaint": {"zh": "投诉", "route": "handoff", "slots": [],
                  "desc": "主要表达不满，或要求人工/经理"},
    # ---------- refuse ----------
    "out_of_scope": {"zh": "超出范围", "route": "refuse", "slots": [],
                     "desc": "投资建议、闲聊、其他公司、与银行业务无关"},
    "greeting": {"zh": "问候", "route": "refuse", "slots": [],
                 "desc": "打招呼（用规则识别，不需要训练数据）"},
}

INTENT_NAMES = list(INTENTS)
INTENT_ROUTES = {name: info["route"] for name, info in INTENTS.items()}

# Banking77 标签 -> 本项目意图（未列出的 26 个标签丢弃）
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

# Banking77 中没有、需要 LLM 生成数据的意图（greeting 用规则，不生成）
GENERATE_ONLY = ["balance_inquiry", "complaint", "out_of_scope"]
# Banking77 中太少、需要 LLM 补充的意图
NEEDS_TOP_UP = ["card_lost_freeze"]
# 每个意图最多使用的样本数（避免 card_service 等大类压倒小类）
MAX_PER_INTENT = 300


def route_of(intent: str) -> str:
    return INTENT_ROUTES.get(intent, "rag")


def required_slots(intent: str) -> list[str]:
    return INTENTS.get(intent, {}).get("slots", [])


if __name__ == "__main__":
    assert set(BANKING77_TO_INTENT.values()) <= set(INTENTS), "映射到了不存在的意图"
    print(f"{len(INTENTS)} 个意图，{len(BANKING77_TO_INTENT)} 个 Banking77 标签被使用")
    for name, info in INTENTS.items():
        n = sum(1 for v in BANKING77_TO_INTENT.values() if v == name)
        print(f"  {name:<17} {info['zh']:<6} -> {info['route']:<8} Banking77 标签 {n:>2} 个")
