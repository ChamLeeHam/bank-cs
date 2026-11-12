import unittest
from datetime import date, datetime
from decimal import Decimal
from unittest.mock import patch

from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import END, StateGraph
from langgraph.types import Command

from src.nodes.account_agent import AccountAgentNode
from src.schema.state import GraphState, create_default_state
from src.utils.account_utils import find_transfers, transfer_filters


def row(ref, amount):
    return {"transfer_id": ref, "created_at": datetime(2026, 11, 11, 10), "nickname": "妈妈",
            "amount": Decimal(amount), "currency": "SGD", "status": "completed",
            "status_note_zh": "已到账", "status_note_en": "Credited"}


class TransferStatusTests(unittest.TestCase):
    def setUp(self):
        graph = StateGraph(GraphState)
        graph.add_node("account_agent", AccountAgentNode())
        graph.set_entry_point("account_agent")
        graph.add_edge("account_agent", END)
        self.app = graph.compile(checkpointer=InMemorySaver())
        self.config = {"configurable": {"thread_id": "test"}}
        self.state = create_default_state(customer_id="C001", otp_verified=True, otp_customer_id="C001",
                                          intent="transfer_status", lang="zh", masked_question="给妈妈的转账到了吗")

    def test_filter_extraction(self):
        result = transfer_filters("昨天汇了 SGD 500", today=date(2026, 11, 12))
        self.assertEqual(result, {"date": date(2026, 11, 11), "amount": Decimal("500"), "currency": "SGD"})
        self.assertEqual(transfer_filters("2026-11-01 转 500 元")["date"], date(2026, 11, 1))
        with self.assertRaises(ValueError):
            transfer_filters("上周汇款")

    def test_query_filters_before_limit_and_binds_customer(self):
        with patch("src.utils.account_utils.query", return_value=[]) as query:
            find_transfers("C001", [3], date(2026, 11, 1), Decimal("500"))
        sql, params = query.call_args.args
        self.assertIn("t.customer_id = %s", sql)
        self.assertIn("t.amount = %s", sql)
        self.assertEqual(params, ["C001", 3, date(2026, 11, 1), date(2026, 11, 2), Decimal("500"), 51])

    @patch("src.nodes.account_agent.find_payees", return_value=[{"payee_id": 1}])
    @patch("src.nodes.account_agent.find_transfers", return_value=[row("TT1", "500"), row("TT2", "1200")])
    def test_multiple_candidates_ask_and_select_reference(self, search, payees):
        result = self.app.invoke(self.state, self.config)
        self.assertEqual(result["__interrupt__"][0].value["type"], "select_transfer")
        result = self.app.invoke(Command(resume="TT2"), self.config)
        self.assertIn("TT2", result["answer"])
        self.assertNotIn("TT1", result["answer"])
        self.assertEqual(search.call_args.args[0], "C001")

    @patch("src.nodes.account_agent.find_payees", return_value=[{"payee_id": 1}])
    @patch("src.nodes.account_agent.find_transfers", return_value=[row("TT1", "500"), row("TT2", "1200")])
    def test_invalid_reference_cannot_select_other_transfer(self, search, payees):
        self.app.invoke(self.state, self.config)
        result = self.app.invoke(Command(resume="OTHER"), self.config)
        self.assertEqual(result["__interrupt__"][0].value["type"], "select_transfer")
        result = self.app.invoke(Command(resume="OTHER"), self.config)
        self.assertIn("无法确定", result["answer"])

    @patch("src.nodes.account_agent.find_payees", return_value=[])
    @patch("src.nodes.account_agent.find_transfers")
    def test_unknown_recipient_does_not_search_everyone(self, search, payees):
        self.state["masked_question"] = "给爸爸的转账到了吗"
        result = self.app.invoke(self.state, self.config)
        self.assertIn("没有匹配", result["answer"])
        search.assert_not_called()

    @patch("src.nodes.account_agent.find_transfers")
    def test_unverified_never_queries(self, search):
        self.state["otp_verified"] = False
        self.app.invoke(self.state, self.config)
        search.assert_not_called()


if __name__ == "__main__":
    unittest.main()
