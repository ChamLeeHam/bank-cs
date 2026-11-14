import unittest
from datetime import datetime
from decimal import Decimal
from unittest.mock import patch

from src.nodes.account_agent import AccountAgentNode
from src.nodes.intent_router import IntentRouterNode
from src.nodes.stub_nodes import GateNode
from src.schema.state import create_default_state
from src.utils.account_utils import get_latest_card_transaction


class CardTransactionTests(unittest.TestCase):
    def test_latest_transaction_routes_to_account_and_preserves_fraud_priority(self):
        router = IntentRouterNode()
        for question in ("And what was my last card transaction?", "我的最近一笔银行卡交易是什么？"):
            with self.subTest(question=question):
                state = create_default_state(question=question, masked_question=question)
                state.update(router.process(state))
                self.assertEqual(state["intent"], "card_transaction_inquiry")
                self.assertEqual(GateNode().process(state)["route"], "account")
        state = create_default_state(masked_question="My last card transaction was unauthorized")
        self.assertEqual(router.process(state)["intent"], "fraud_report")

    @patch("src.utils.account_utils.query", return_value=[])
    def test_query_is_scoped_to_customer(self, query):
        self.assertIsNone(get_latest_card_transaction("C001"))
        sql, params = query.call_args.args
        self.assertIn("where c.customer_id = %s", sql.lower())
        self.assertEqual(params, ("C001",))

    @patch("src.nodes.account_agent.get_latest_card_transaction")
    def test_transaction_requires_verified_customer_and_handles_empty(self, get_latest):
        node = AccountAgentNode()
        state = create_default_state(customer_id="C001", intent="card_transaction_inquiry", lang="en")
        self.assertIn("verify", node.process(state)["answer"].lower())
        get_latest.assert_not_called()
        state.update(otp_verified=True, otp_customer_id="C002")
        node.process(state)
        get_latest.assert_not_called()
        state["otp_customer_id"] = "C001"
        get_latest.return_value = None
        self.assertIn("couldn't find", node.process(state)["answer"])
        get_latest.return_value = {"merchant": "Demo shop", "amount": Decimal("8.20"), "currency": "SGD",
                                   "txn_time": datetime(2026, 11, 1), "status": "posted", "last4": "1234"}
        result = node.process(state)
        self.assertIn("Demo shop", result["answer"])
        self.assertIn("1234", result["answer"])
        self.assertEqual(result["tool_calls"], [{"tool": "get_latest_card_transaction"}])


if __name__ == "__main__":
    unittest.main()
