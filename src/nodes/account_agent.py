# 账户查询：验证 OTP 后在本地读取和整理数据，不把账户资料发给模型
import re

from langgraph.types import interrupt

from src.nodes.stub_nodes import pick
from src.schema.base import BaseNode, StateUpdate
from src.schema.exceptions import MySQLError
from src.schema.state import GraphState
from src.utils.account_utils import (find_payees, find_transfers, freeze_card, get_accounts, get_cards, get_transfer_by_id,
                                     transfer_filters)
from src.utils.transfer_context import pending_transfer_ids, transfer_ordinal, transfer_other

ACCOUNT_NAMES = {
    "savings": ("储蓄账户", "savings account"),
    "current": ("往来账户", "current account"),
    "multi_currency": ("多币种账户", "multi-currency account"),
    "fixed_deposit": ("定期存款账户", "fixed deposit account"),
}
TRANSFER_STATUS = {
    "completed": ("已完成", "completed"),
    "processing": ("处理中", "being processed"),
    "pending_review": ("等待审核", "pending review"),
    "failed": ("失败", "failed"),
    "returned": ("已退回", "returned"),
}


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
                text = state.get("masked_question", "")
                requested = []
                for account_type, pattern in (("savings", r"储蓄|savings?"),
                                              ("current", r"往来|current account|checking account"),
                                              ("multi_currency", r"多币种|multi.currency"),
                                              ("fixed_deposit", r"定期存款|fixed deposit")):
                    if re.search(pattern, text, re.I):
                        requested.append(account_type)
                if requested:
                    rows = [r for r in rows if r["account_type"] in requested]
                if not rows:
                    answer = pick(lang, "Sorry, I couldn't find a matching active account.",
                                  "不好意思，我没有找到符合条件的可用账户。")
                else:
                    lines = []
                    for r in rows:
                        zh, en = ACCOUNT_NAMES.get(r["account_type"], (r["account_type"], r["account_type"]))
                        lines.append(pick(lang, f"{en.capitalize()}: {r['currency']} {r['balance']}",
                                          f"{zh}：{r['currency']} {r['balance']}"))
                    answer = pick(lang, "Here are your account balances:\n", "帮您查到了，账户余额如下：\n") + "\n".join(lines)
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
        if transfer_other(text):
            return self._other_transfers(state, customer_id, lang)
        ordinal = transfer_ordinal(text)
        if ordinal is not None and state.get("transfer_customer_id") != customer_id:
            return {"answer": pick(lang, "Please start a new transfer search first.",
                                   "请先重新查询汇款，再选择具体的一笔。"), "tool_calls": []}
        if ordinal is not None and state.get("transfer_customer_id") == customer_id:
            candidates = state.get("transfer_candidates") or []
            if ordinal > len(candidates):
                return {"answer": pick(lang, "There isn't a transfer at that position in the previous list.",
                                       "上一组汇款中没有这个序号，请核对后再试。"), "tool_calls": []}
            transfer = get_transfer_by_id(customer_id, candidates[ordinal - 1])
            if not transfer:
                return {"answer": pick(lang, "That transfer is no longer available. Please start a new search.",
                                       "这笔汇款暂时无法查询，请重新发起查询。"),
                        "tool_calls": [{"tool": "get_transfer_by_id"}]}
            return {"answer": self._transfer_answer(transfer, lang), "tool_calls": [{"tool": "get_transfer_by_id"}]}
        calls = [{"tool": "find_payees"}, {"tool": "find_transfers"}]
        try:
            filters = transfer_filters(text)
        except ValueError:
            return {"answer": pick(lang, "Please provide a date as YYYY-MM-DD, today or yesterday.",
                                   "请提供具体日期（如 2026-11-12），或说今天、昨天、前天。"), "tool_calls": []}
        payees = find_payees(customer_id, text)
        # 指明了收款人但没有匹配上时，不能改查其他收款人的交易并当成答案
        named = re.search(r"给.{1,20}?(?:的|转|汇|打|付)|"
                          r"\bto\s+(?!arrive\b|reach\b|receive\b|be\b)\S+", text, re.I)
        if named and not payees:
            return {"answer": pick(lang, "I couldn't match that recipient. Please check their saved nickname.",
                                   "没有匹配到该收款人，请核对已保存的收款人昵称。"),
                    "tool_calls": [{"tool": "find_payees"}]}
        rows = find_transfers(customer_id, [p["payee_id"] for p in payees] if payees else None, **filters)
        snapshot = self._restore_candidates(state, customer_id)
        if snapshot is not None:
            if not snapshot:
                return {"answer": pick(lang, "The transfer list changed. Please start a new search.",
                                       "汇款列表已发生变化，请重新查询。"), "tool_calls": calls}
            rows = snapshot
        if not rows:
            period = "the specified date" if filters["date"] else "the last 30 days"
            answer = pick(lang, f"Sorry, I couldn't find a matching transfer in {period}. "
                                "Please check the date, recipient or amount and try again.",
                          (f"不好意思，在 {filters['date']:%Y-%m-%d} 没有找到符合条件的转账。"
                           if filters["date"] else "不好意思，最近 30 天没有找到符合条件的转账。")
                          + "您可以核对一下日期、收款人或金额，再试一次。")
            return {"answer": answer, "tool_calls": calls}
        return self._present_transfers(rows, customer_id, lang, calls)

    def _other_transfers(self, state, customer_id, lang):
        previous = state.get("transfer_candidates") or []
        if not previous or state.get("transfer_customer_id") != customer_id:
            return {"answer": pick(lang, "Please search for a transfer first.", "请先查询汇款，再询问其他汇款。"),
                    "tool_calls": []}
        for _ in range(2):
            reply = str(interrupt({"type": "transfer_scope",
                                   "message_zh": "您想查询同一收款人的其他汇款，还是所有收款人的其他汇款？请回复“同一收款人”或“所有收款人”。",
                                   "message_en": "Do you mean other transfers to the same recipient, or to all recipients? Reply 'same recipient' or 'all recipients'."})).strip().lower()
            if reply in ("取消", "cancel"):
                return {"answer": pick(lang, "Transfer search cancelled.", "已取消查询。"), "tool_calls": []}
            if reply in ("同一收款人", "同一个收款人", "same recipient"):
                scope = "same"
                break
            if reply in ("所有收款人", "全部", "所有", "all recipients", "all"):
                scope = "all"
                break
        else:
            return {"answer": pick(lang, "I couldn't determine the scope. Please start a new search.",
                                   "无法确定查询范围，请重新发起查询。"), "tool_calls": []}
        payee_ids = None
        calls = [{"tool": "find_transfers"}]
        if scope == "same":
            prior_rows = [get_transfer_by_id(customer_id, ref) for ref in previous]
            if any(r is None for r in prior_rows) or len({r["nickname"] for r in prior_rows}) != 1:
                return {"answer": pick(lang, "Please specify a recipient in a new search.",
                                       "上一组包含不同收款人或记录已变化，请重新指定收款人查询。"), "tool_calls": []}
            nickname = prior_rows[0]["nickname"]
            matches = [p for p in find_payees(customer_id, nickname) if p["nickname"].casefold() == nickname.casefold()]
            if len(matches) != 1:
                return {"answer": pick(lang, "Please specify a recipient in a new search.",
                                       "无法唯一确定收款人，请重新指定收款人查询。"), "tool_calls": []}
            payee_ids = [matches[0]["payee_id"]]
            calls = [{"tool": "get_transfer_by_id"}, {"tool": "find_payees"}, *calls]
        rows = find_transfers(customer_id, payee_ids)
        snapshot = self._restore_candidates(state, customer_id)
        if snapshot is not None:
            if not snapshot:
                return {"answer": pick(lang, "The transfer list changed. Please start a new search.",
                                       "汇款列表已发生变化，请重新查询。"), "tool_calls": calls}
            rows = snapshot
        if len(rows) > 50:
            return {"answer": pick(lang, "There are too many transfers. Please provide a date or amount.",
                                   "转账记录太多，请提供具体日期或金额。"), "tool_calls": calls}
        rows = [r for r in rows if r["transfer_id"] not in previous]
        if not rows:
            return {"answer": pick(lang, "No other matching transfers were found in the last 30 days.",
                                   "最近 30 天没有找到符合这个范围的其他汇款。"), "tool_calls": calls}
        return self._present_transfers(rows, customer_id, lang, calls)

    @staticmethod
    def _restore_candidates(state, customer_id):
        ids = pending_transfer_ids(state.get("session_id", ""))
        if ids is None:
            return None
        rows = [get_transfer_by_id(customer_id, ref) for ref in ids]
        return rows if rows and all(rows) else []

    def _present_transfers(self, rows, customer_id, lang, calls):
        if len(rows) > 50:
            return {"answer": pick(lang, "There are too many matches. Please provide a date or amount.",
                                   "符合条件的转账太多，请提供具体日期或金额。"), "tool_calls": calls}

        candidates = [r["transfer_id"] for r in rows] if len(rows) > 1 else []
        if candidates:
            options = "\n".join(f"{r['transfer_id']} · {r['created_at']:%Y-%m-%d} · {r['nickname']} · "
                                f"{r['currency']} {r['amount']}" for r in rows)
            for attempt in range(2):
                prompt_zh = ("找到多笔转账，请输入要查询的交易编号：" if not attempt else "编号不匹配，请再输入一次交易编号：")
                prompt_en = ("Multiple transfers match. Enter the transaction reference:" if not attempt
                             else "That reference did not match. Enter it again:")
                reply = interrupt({"type": "select_transfer", "message_zh": f"{prompt_zh}\n{options}",
                                   "message_en": f"{prompt_en}\n{options}", "candidate_ids": candidates})
                displayed = reply.get("candidate_ids") if isinstance(reply, dict) else None
                if displayed is not None:
                    candidates = displayed
                reply = str(reply.get("value", "") if isinstance(reply, dict) else reply).strip()
                if reply.lower() in ("取消", "cancel"):
                    return {"answer": pick(lang, "Transfer selection cancelled. What else can I help with?",
                                           "已取消选择汇款，还有什么可以帮您？"), "tool_calls": calls}
                selected_id = next((ref for ref in candidates if ref.lower() == reply.lower()), None)
                selected = get_transfer_by_id(customer_id, selected_id) if selected_id and displayed is not None else next(
                    (r for r in rows if r["transfer_id"].lower() == reply.lower()), None)
                if selected:
                    rows = [selected]
                    break
            else:
                return {"answer": pick(lang, "I couldn't identify the transfer. Please start a new request.",
                                       "无法确定是哪笔转账，请重新发起查询。"), "tool_calls": calls}

        return {"answer": self._transfer_answer(rows[0], lang), "tool_calls": calls,
                "transfer_candidates": candidates, "transfer_customer_id": customer_id if candidates else ""}

    @staticmethod
    def _transfer_answer(r, lang):
        note = r["status_note_en"] if lang == "en" else r["status_note_zh"]
        zh_status, en_status = TRANSFER_STATUS.get(r["status"], (r["status"], r["status"]))
        if lang == "en":
            answer = (f"I found your {r['currency']} {r['amount']} transfer to {r['nickname']}. "
                      f"Its current status is {en_status}.")
            if note:
                answer += f" {note}"
            answer += f"\n\nReference: {r['transfer_id']} · {r['created_at']:%Y-%m-%d %H:%M}"
        else:
            answer = (f"帮您查到了，您转给{r['nickname']}的 {r['currency']} {r['amount']} "
                      f"目前状态是{zh_status}。")
            if note:
                answer += f"{note}"
            answer += f"\n\n交易编号：{r['transfer_id']} · {r['created_at']:%Y-%m-%d %H:%M}"
        return answer

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
