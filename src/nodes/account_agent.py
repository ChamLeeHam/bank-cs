# 账户查询：验证 OTP 后在本地读取和整理数据，不把账户资料发给模型
import re

from langgraph.types import interrupt

from src.nodes.stub_nodes import pick
from src.schema.base import BaseNode, StateUpdate
from src.schema.exceptions import MySQLError
from src.schema.state import GraphState
from src.utils.account_utils import (find_payees, find_transfers, freeze_card, get_accounts, get_cards,
                                     transfer_filters)


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
                return self._transfer_status(state, customer_id, lang)

            if intent == "card_lost_freeze":
                return self._freeze_card(state, customer_id, lang)

            return {"answer": pick(lang, "I can't handle that account request yet.",
                                   "目前无法处理这项账户请求。"), "tool_calls": []}
        except MySQLError as e:
            self.logger.error(f"账户查询失败：{e}")
            return {"answer": pick(lang, "Account information is unavailable right now. Please try again later.",
                                   "账户资料暂时无法查询，请稍后再试。"), "tool_calls": []}

    def _transfer_status(self, state, customer_id, lang):
        text = state.get("masked_question", "")
        calls = [{"tool": "find_payees"}, {"tool": "find_transfers"}]
        try:
            filters = transfer_filters(text)
        except ValueError:
            return {"answer": pick(lang, "Please provide a date as YYYY-MM-DD, today or yesterday.",
                                   "请提供具体日期（如 2026-11-12），或说今天、昨天、前天。"), "tool_calls": []}
        payees = find_payees(customer_id, text)
        # 指明了收款人但没有匹配上时，不能改查其他收款人的交易并当成答案
        named = re.search(r"给.{1,20}?(?:的|转账|汇款|转了|汇了)|"
                          r"\bto\s+(?!arrive\b|reach\b|receive\b|be\b)\S+", text, re.I)
        if named and not payees:
            return {"answer": pick(lang, "I couldn't match that recipient. Please check their saved nickname.",
                                   "没有匹配到该收款人，请核对已保存的收款人昵称。"),
                    "tool_calls": [{"tool": "find_payees"}]}
        rows = find_transfers(customer_id, [p["payee_id"] for p in payees] if payees else None, **filters)
        if not rows:
            period = "the specified date" if filters["date"] else "the last 30 days"
            answer = pick(lang, f"No matching transfers were found in {period}.",
                          "指定日期没有找到符合条件的转账。" if filters["date"] else "最近 30 天没有找到符合条件的转账。")
            return {"answer": answer, "tool_calls": calls}
        if len(rows) > 50:
            return {"answer": pick(lang, "There are too many matches. Please provide a date or amount.",
                                   "符合条件的转账太多，请提供具体日期或金额。"), "tool_calls": calls}

        if len(rows) > 1:
            options = "\n".join(f"{r['transfer_id']} · {r['created_at']:%Y-%m-%d} · {r['nickname']} · "
                                f"{r['currency']} {r['amount']}" for r in rows)
            for attempt in range(2):
                prompt_zh = ("找到多笔转账，请输入要查询的交易编号：" if not attempt else "编号不匹配，请再输入一次交易编号：")
                prompt_en = ("Multiple transfers match. Enter the transaction reference:" if not attempt
                             else "That reference did not match. Enter it again:")
                reply = str(interrupt({"type": "select_transfer", "message_zh": f"{prompt_zh}\n{options}",
                                       "message_en": f"{prompt_en}\n{options}"})).strip()
                selected = next((r for r in rows if r["transfer_id"].lower() == reply.lower()), None)
                if selected:
                    rows = [selected]
                    break
            else:
                return {"answer": pick(lang, "I couldn't identify the transfer. Please start a new request.",
                                       "无法确定是哪笔转账，请重新发起查询。"), "tool_calls": calls}

        r = rows[0]
        note = r["status_note_en"] if lang == "en" else r["status_note_zh"]
        line = (f"{r['transfer_id']} · {r['created_at']:%Y-%m-%d %H:%M} · {r['nickname']} · "
                f"{r['currency']} {r['amount']} · {r['status']}")
        return {"answer": line + (f"\n{note}" if note else ""), "tool_calls": calls}

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
