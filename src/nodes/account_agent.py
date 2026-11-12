# 账户查询：验证 OTP 后在本地读取和整理数据，不把账户资料发给模型
from langgraph.types import interrupt

from src.nodes.stub_nodes import pick
from src.schema.base import BaseNode, StateUpdate
from src.schema.exceptions import MySQLError
from src.schema.state import GraphState
from src.utils.account_utils import find_payees, freeze_card, get_accounts, get_cards, get_recent_transfers


class AccountAgentNode(BaseNode):
    name = "account_agent"

    def process(self, state: GraphState) -> StateUpdate:
        lang = state.get("lang", "en")
        customer_id = state.get("customer_id")
        if not customer_id or not state.get("otp_verified") or state.get("otp_customer_id") != customer_id:
            return {"answer": pick(lang, "Please verify your identity before accessing your account.",
                                   "请先验证身份，再查询账户资料。"), "tool_calls": []}

        intent = state.get("intent")
        try:
            if intent == "balance_inquiry":
                rows = get_accounts(customer_id)
                if not rows:
                    answer = pick(lang, "No active accounts were found.", "没有找到可用的账户。")
                else:
                    lines = [f"{r['account_type']} · {r['currency']} {r['balance']}" for r in rows]
                    answer = pick(lang, "Your active account balances:\n", "您的可用账户余额：\n") + "\n".join(lines)
                return {"answer": answer, "tool_calls": [{"tool": "get_accounts"}]}

            if intent == "transfer_status":
                payees = find_payees(customer_id, state.get("masked_question", ""))
                rows = get_recent_transfers(customer_id, [p["payee_id"] for p in payees] if payees else None)
                if not rows:
                    answer = pick(lang, "No matching transfers were found in the last 30 days.",
                                  "最近 30 天没有找到符合条件的转账。")
                else:
                    lines = []
                    for r in rows:
                        note = r["status_note_en"] if lang == "en" else r["status_note_zh"]
                        line = (f"{r['created_at']:%Y-%m-%d %H:%M} · {r['nickname']} · "
                                f"{r['currency']} {r['amount']} · {r['status']}")
                        lines.append(line + (f" ({note})" if note else ""))
                    answer = pick(lang, "Recent transfers in the last 30 days (up to 5):\n",
                                  "最近 30 天的转账（最多 5 笔）：\n") + "\n".join(lines)
                return {"answer": answer, "tool_calls": [{"tool": "find_payees"}, {"tool": "get_recent_transfers"}]}

            if intent == "card_lost_freeze":
                return self._freeze_card(state, customer_id, lang)

            return {"answer": pick(lang, "I can't handle that account request yet.",
                                   "目前无法处理这项账户请求。"), "tool_calls": []}
        except MySQLError as e:
            self.logger.error(f"账户查询失败：{e}")
            return {"answer": pick(lang, "Account information is unavailable right now. Please try again later.",
                                   "账户资料暂时无法查询，请稍后再试。"), "tool_calls": []}

    def _freeze_card(self, state, customer_id, lang):
        cards = get_cards(customer_id)
        last4 = (state.get("entities") or {}).get("card_last4")
        if not last4:
            # 暂停等客户指定卡片，不猜测或自动选择
            last4 = str(interrupt({"type": "card_last4",
                                   "message_en": "Enter the last four digits of the card you want to freeze.",
                                   "message_zh": "请输入要冻结的银行卡后四位。"})).strip()
        card = next((c for c in cards if c["last4"] == last4), None)
        if not card:
            return {"answer": pick(lang, "No card with those last four digits was found in your account.",
                                   "您的账户中没有找到该尾号的银行卡。"), "tool_calls": [{"tool": "get_cards"}]}
        if card["status"] != "active":
            return {"answer": pick(lang, "This card is not active, so it cannot be frozen through this flow.",
                                   "这张卡不是正常使用状态，无法通过此流程冻结。"),
                    "tool_calls": [{"tool": "get_cards"}]}

        reply = interrupt({"type": "confirm_freeze",
                           "message_en": f"Freeze your {card['network']} card ending {last4}? Reply confirm or cancel.",
                           "message_zh": f"确认冻结尾号 {last4} 的 {card['network']} 卡吗？请回复确认或取消。"})
        if str(reply).strip().lower() not in ("确认", "确定", "confirm", "yes"):
            return {"answer": pick(lang, "The card was not frozen.", "未冻结银行卡。"),
                    "tool_calls": [{"tool": "get_cards"}]}
        if not freeze_card(customer_id, last4):
            return {"answer": pick(lang, "The card could not be frozen. Please check its status and try again.",
                                   "未能冻结银行卡，请检查卡片状态后重试。"),
                    "tool_calls": [{"tool": "get_cards"}, {"tool": "freeze_card"}]}
        return {"answer": pick(lang, f"The card ending {last4} has been frozen.", f"尾号 {last4} 的银行卡已冻结。"),
                "tool_calls": [{"tool": "get_cards"}, {"tool": "freeze_card"}]}
