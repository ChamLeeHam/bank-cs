import unittest
from datetime import date, datetime
from decimal import Decimal
from unittest.mock import patch

from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import END, StateGraph
from langgraph.types import Command

from src.nodes.account_agent import AccountAgentNode
from src.nodes.stub_nodes import GateNode, InputGuardNode
from src.schema.state import GraphState, create_default_state
from src.utils.account_utils import find_transfers, transfer_filters
from src.utils.transfer_context import begin_transfer_resume, end_transfer_resume


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
        self.assertIn("已完成", result["answer"])
        self.assertNotIn("TT1", result["answer"])
        self.assertEqual(result["transfer_candidates"], ["TT1", "TT2"])
        self.assertEqual(result["transfer_customer_id"], "C001")
        self.assertEqual(search.call_args.args[0], "C001")

    @patch("src.nodes.account_agent.get_transfer_by_id", return_value=row("TT2", "1200"))
    def test_followup_uses_previous_list_and_customer(self, fetch):
        self.state.update(masked_question="那第二笔呢？", transfer_candidates=["TT1", "TT2"],
                          transfer_customer_id="C001")
        result = self.app.invoke(self.state, self.config)
        self.assertIn("TT2", result["answer"])
        fetch.assert_called_once_with("C001", "TT2")

    def test_followup_routing_and_context_clear(self):
        self.state.update(masked_question="那第二笔呢？", transfer_candidates=["TT1", "TT2"],
                          transfer_customer_id="C001", intent="unknown", intent_score=0.1)
        result = GateNode().process(self.state)
        self.assertEqual((result["route"], result["intent"]), ("account", "transfer_status"))
        self.state["question"] = "PayNow 手续费多少？"
        self.assertEqual(InputGuardNode().process(self.state)["transfer_candidates"], [])

    @patch("src.nodes.account_agent.get_transfer_by_id")
    def test_out_of_range_does_not_query(self, fetch):
        self.state.update(masked_question="第三笔呢？", transfer_candidates=["TT1", "TT2"],
                          transfer_customer_id="C001")
        result = self.app.invoke(self.state, self.config)
        self.assertIn("没有这个序号", result["answer"])
        fetch.assert_not_called()

    @patch("src.nodes.account_agent.find_transfers", return_value=[row("TT1", "500"), row("TT2", "1200"),
                                                                    dict(row("TT3", "45"), nickname="Jason")])
    def test_other_transfers_asks_scope_and_excludes_previous(self, search):
        self.state.update(masked_question="我还有其他汇款吗？", transfer_candidates=["TT1", "TT2"],
                          transfer_customer_id="C001")
        first = self.app.invoke(self.state, self.config)
        self.assertEqual(first["__interrupt__"][0].value["type"], "transfer_scope")
        result = self.app.invoke(Command(resume="所有收款人"), self.config)
        self.assertIn("TT3", result["answer"])
        self.assertNotIn("TT1", result["answer"])
        search.assert_called_once_with("C001", None)

    @patch("src.nodes.account_agent.find_transfers", return_value=[row("TT1", "500"), row("TT2", "1200")])
    @patch("src.nodes.account_agent.find_payees", return_value=[{"payee_id": 1, "nickname": "妈妈"}])
    @patch("src.nodes.account_agent.get_transfer_by_id", side_effect=[row("TT1", "500"), row("TT2", "1200")])
    def test_other_transfers_same_recipient_no_more(self, fetch, payees, search):
        self.state.update(masked_question="我还有其他汇款吗？", transfer_candidates=["TT1", "TT2"],
                          transfer_customer_id="C001")
        self.app.invoke(self.state, self.config)
        result = self.app.invoke(Command(resume="同一收款人"), self.config)
        self.assertIn("没有找到", result["answer"])
        search.assert_called_once_with("C001", [1])

    @patch("src.nodes.account_agent.find_payees", return_value=[{"payee_id": 1}])
    @patch("src.nodes.account_agent.find_transfers", return_value=[row("TT1", "500"), row("TT2", "1200")])
    def test_invalid_reference_cannot_select_other_transfer(self, search, payees):
        self.app.invoke(self.state, self.config)
        result = self.app.invoke(Command(resume="OTHER"), self.config)
        self.assertEqual(result["__interrupt__"][0].value["type"], "select_transfer")
        result = self.app.invoke(Command(resume="OTHER"), self.config)
        self.assertIn("无法确定", result["answer"])

    @patch("src.nodes.account_agent.find_payees", return_value=[{"payee_id": 1}])
    @patch("src.nodes.account_agent.find_transfers", side_effect=[[row("TT1", "500"), row("TT2", "1200")],
                                                                  [row("TT1", "500")]])
    @patch("src.nodes.account_agent.get_transfer_by_id", side_effect=[row("TT1", "500"), row("TT2", "1200"),
                                                                      row("TT2", "1200")])
    def test_changed_results_do_not_replace_selected_transfer(self, fetch, search, payees):
        self.state["session_id"] = "changed-test"
        self.app.invoke(self.state, self.config)
        begin_transfer_resume("changed-test", ["TT1", "TT2"])
        try:
            result = self.app.invoke(Command(resume={"value": "TT2", "candidate_ids": ["TT1", "TT2"]}), self.config)
        finally:
            end_transfer_resume("changed-test")
        self.assertIn("TT2", result["answer"])
        self.assertNotIn("TT1", result["answer"])

    @patch("src.nodes.account_agent.find_payees", return_value=[{"payee_id": 1}])
    @patch("src.nodes.account_agent.find_transfers", side_effect=[[row("TT1", "500"), row("TT2", "1200")], []])
    @patch("src.nodes.account_agent.get_transfer_by_id", side_effect=[row("TT1", "500"), None])
    def test_missing_candidate_fails_closed(self, fetch, search, payees):
        self.state["session_id"] = "missing-test"
        self.app.invoke(self.state, self.config)
        begin_transfer_resume("missing-test", ["TT1", "TT2"])
        try:
            result = self.app.invoke(Command(resume={"value": "TT2", "candidate_ids": ["TT1", "TT2"]}), self.config)
        finally:
            end_transfer_resume("missing-test")
        self.assertIn("发生变化", result["answer"])

    @patch("src.nodes.account_agent.find_payees", return_value=[{"payee_id": 1}])
    @patch("src.nodes.account_agent.find_transfers", return_value=[row("TT1", "500"), row("TT2", "1200")])
    def test_cancel_transfer_selection(self, search, payees):
        self.app.invoke(self.state, self.config)
        result = self.app.invoke(Command(resume="取消"), self.config)
        self.assertIn("已取消", result["answer"])

    @patch("src.nodes.account_agent.find_payees", return_value=[])
    @patch("src.nodes.account_agent.find_transfers")
    def test_unknown_recipient_does_not_search_everyone(self, search, payees):
        self.state["masked_question"] = "我给爸爸转500块到了吗"
        result = self.app.invoke(self.state, self.config)
        self.assertIn("没有匹配", result["answer"])
        search.assert_not_called()

    @patch("src.nodes.account_agent.find_transfers")
    def test_unverified_never_queries(self, search):
        self.state["otp_verified"] = False
        self.app.invoke(self.state, self.config)
        search.assert_not_called()

    @patch("src.nodes.account_agent.get_accounts")
    def test_balance_respects_requested_account_type(self, accounts):
        accounts.return_value = [{"account_type": "savings", "currency": "SGD", "balance": 100},
                                 {"account_type": "multi_currency", "currency": "CNY", "balance": 200}]
        self.state.update(intent="balance_inquiry", masked_question="帮我 check savings account balance")
        answer = self.app.invoke(self.state, self.config)["answer"]
        self.assertIn("储蓄账户", answer)
        self.assertNotIn("多币种账户", answer)


if __name__ == "__main__":
    unittest.main()
