import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient

from src.api import app


class DemoFreezeFlowTests(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(app)
        self.cards = {"C001": [{"last4": "1234", "network": "Visa", "status": "active"}],
                      "C002": [{"last4": "9876", "network": "Visa", "status": "active"}]}

    def _session(self, customer="C001"):
        return self.client.post("/api/sessions", json={"customer_id": customer}).json()["session_id"]

    def _send(self, session, text):
        response = self.client.post(f"/api/sessions/{session}/messages", json={"text": text})
        self.assertEqual(response.status_code, 200)
        return response.json()

    def _freeze(self, customer, last4):
        for card in self.cards[customer]:
            if card["last4"] == last4 and card["status"] == "active":
                card["status"] = "frozen"
                return True
        return False

    @patch("src.schema.main_graph.write_turn_log")
    @patch("src.nodes.intent_router.IntentRouterNode._model_ready", return_value=False)
    @patch("src.nodes.verify_otp.query_one", return_value={"demo_otp": "123456"})
    @patch("src.nodes.stub_nodes.create_ticket", return_value=42)
    def test_fraud_interrupts_otp_and_preserves_failed_attempts(self, ticket, otp, model, audit):
        session = self._session()
        self.assertEqual(self._send(session, "我要冻结银行卡")["pending"], "otp")
        for _ in range(2):
            self.assertEqual(self._send(session, "000000")["pending"], "otp")
        report = self._send(session, "刚收到一笔现金取款通知，不是我取的，请转人工")
        self.assertIsNone(report["pending"])
        self.assertIn("42", report["answer"])
        self.assertEqual(ticket.call_args.args[1], "urgent")
        ticket.assert_called_once()
        self.assertEqual(self._send(session, "我要冻结银行卡")["pending"], "otp")
        self.assertIsNone(self._send(session, "000000")["pending"])
        self.assertIsNone(self._send(session, "我要冻结银行卡")["pending"])

    @patch("src.schema.main_graph.write_turn_log")
    @patch("src.nodes.intent_router.IntentRouterNode._model_ready", return_value=False)
    @patch("src.nodes.verify_otp.query_one", return_value={"demo_otp": "123456"})
    def test_confirm_cancel_and_customer_isolation(self, otp, model, audit):
        with patch("src.nodes.account_agent.get_cards", side_effect=lambda customer: self.cards[customer]), \
             patch("src.nodes.account_agent.freeze_card", side_effect=self._freeze) as freeze:
            session = self._session()
            self.assertEqual(self._send(session, "我的卡被偷了，帮我冻结")["pending"], "otp")
            self.assertEqual(self._send(session, "123456")["pending"], "card_last4")
            self.assertEqual(self._send(session, "1234")["pending"], "confirm_freeze")
            self.assertEqual(self.cards["C001"][0]["status"], "active")
            self.assertIn("未冻结", self._send(session, "取消")["answer"])
            freeze.assert_not_called()

            self.assertEqual(self._send(session, "我要冻结银行卡")["pending"], "card_last4")
            self.assertIn("没有找到", self._send(session, "9876")["answer"])
            freeze.assert_not_called()

            self.assertEqual(self._send(session, "我要冻结银行卡")["pending"], "card_last4")
            self.assertEqual(self._send(session, "1234")["pending"], "confirm_freeze")
            self.assertIn("已冻结", self._send(session, "确认")["answer"])
            freeze.assert_called_once_with("C001", "1234")
            self.assertEqual(self.cards["C001"][0]["status"], "frozen")
            self.assertEqual(self.cards["C002"][0]["status"], "active")


if __name__ == "__main__":
    unittest.main()
