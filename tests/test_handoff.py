import unittest
from unittest.mock import patch

from langchain_core.messages import AIMessage, HumanMessage

from src.nodes.stub_nodes import AskBackNode, GateNode, HandoffNode, InputGuardNode
from src.schema.exceptions import MySQLError
from src.schema.state import create_default_state
from src.utils.ticket_utils import claim_ticket_notification, create_ticket


class HandoffTests(unittest.TestCase):
    def setUp(self):
        self.state = create_default_state(session_id="test", customer_id="C001", intent="complaint",
                                          sub_intent="staff_attitude", lang="zh", masked_question="我要投诉",
                                          messages=[HumanMessage("我要投诉")])

    def test_explicit_complaint_routes_to_handoff_even_with_low_score(self):
        for question in ("我要投诉", "我想投诉！", "I want to make a complaint."):
            with self.subTest(question=question):
                self.state.update(masked_question=question, intent="unknown", sub_intent="wrong", intent_score=0.1)
                result = GateNode().process(self.state)
                self.assertEqual((result["route"], result["intent"], result["sub_intent"]),
                                 ("handoff", "complaint", ""))

    def test_complaint_howto_does_not_create_ticket(self):
        for question in ("怎么投诉？", "How do I make a complaint?"):
            with self.subTest(question=question):
                self.state.update(masked_question=question, intent_score=0.99,
                                  lang="en" if question.startswith("How") else "zh")
                result = GateNode().process(self.state)
                self.assertEqual(result["route"], "ask_back")
                self.state.update(result)
                self.assertTrue(AskBackNode().process(self.state)["answer"])

    def test_uncertain_complaint_gets_relevant_question(self):
        self.state.update(masked_question="服务让我不满意", intent_score=0.4)
        result = GateNode().process(self.state)
        self.assertEqual(result["route"], "ask_back")
        self.state.update(result)
        self.assertIn("投诉", AskBackNode().process(self.state)["answer"])

    def test_fraud_with_complaint_keeps_urgent_priority(self):
        self.state.update(masked_question="我的卡被盗刷了，我要投诉", intent="fraud_report", intent_score=0.9)
        result = GateNode().process(self.state)
        self.assertEqual(result["route"], "handoff")
        self.state.update(result)
        with patch("src.nodes.stub_nodes.create_ticket", return_value=42) as create:
            self.assertEqual(HandoffNode().process(self.state)["priority"], "urgent")
            self.assertEqual(create.call_args.args[1], "urgent")

    @patch("src.utils.ticket_utils.execute", return_value={"rowcount": 1, "lastrowid": 42})
    def test_create_parameterized_ticket_and_verification(self, execute):
        self.state.update(otp_verified=True, otp_customer_id="C001")
        self.state["messages"].append(AIMessage("[账户查询结果已展示]"))
        self.assertEqual(create_ticket(self.state, "normal"), 42)
        sql, args = execute.call_args.args
        self.assertIn("values (%s", sql.lower())
        self.assertEqual(args[:5], ("test", "C001", "complaint", "staff_attitude", "normal"))
        self.assertIn("身份验证：已通过", args[5])
        self.assertNotIn("账户查询结果", args[5])

    @patch("src.nodes.stub_nodes.create_ticket", return_value=42)
    def test_urgent_ticket_does_not_claim_notification(self, create):
        self.state["intent"] = "fraud_report"
        result = HandoffNode().process(self.state)
        self.assertEqual(result["priority"], "urgent")
        self.assertEqual(result["ticket_id"], 42)
        self.assertIn("尚未接通人工通知", result["answer"])
        create.assert_called_once_with(self.state, "urgent")

    @patch("src.nodes.stub_nodes.create_ticket", side_effect=MySQLError("offline", "test"))
    def test_failure_clears_stale_ticket_and_reports_failure(self, create):
        self.state["ticket_id"] = 10
        result = HandoffNode().process(self.state)
        self.assertIsNone(result["ticket_id"])
        self.assertIn("无法创建", result["answer"])

    @patch("src.utils.ticket_utils.execute", return_value={"rowcount": 1, "lastrowid": 42})
    def test_masked_card_and_id_are_used_in_summary(self, execute):
        state = create_default_state(question="卡号 1234567812345678，身份证 S1234567D", customer_id="C001")
        update = InputGuardNode().process(state)
        self.state.update(update)
        create_ticket(self.state, "normal")
        summary = execute.call_args.args[1][-1]
        self.assertNotIn("1234567812345678", summary)
        self.assertNotIn("S1234567D", summary)
        self.assertIn("[NRIC]", summary)

    @patch("src.utils.ticket_utils.execute")
    def test_claim_pending_notification(self, execute):
        execute.return_value = {"rowcount": 1, "lastrowid": None}

        self.assertTrue(claim_ticket_notification(123))

        sql, params = execute.call_args.args
        self.assertIn("notification_status = 'pending'", sql)
        self.assertIn("notification_status = 'processing'", sql)
        self.assertEqual(params, (123,))

    @patch("src.utils.ticket_utils.execute")
    def test_cannot_claim_notification_twice(self, execute):
        execute.return_value = {"rowcount": 0, "lastrowid": None}

        self.assertFalse(claim_ticket_notification(123))


if __name__ == "__main__":
    unittest.main()
