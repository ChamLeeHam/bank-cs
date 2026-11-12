import unittest
from unittest.mock import patch

from langchain_core.messages import AIMessage, HumanMessage

from src.nodes.stub_nodes import HandoffNode, InputGuardNode
from src.schema.exceptions import MySQLError
from src.schema.state import create_default_state
from src.utils.ticket_utils import create_ticket


class HandoffTests(unittest.TestCase):
    def setUp(self):
        self.state = create_default_state(session_id="test", customer_id="C001", intent="complaint",
                                          sub_intent="staff_attitude", lang="zh", masked_question="我要投诉",
                                          messages=[HumanMessage("我要投诉")])

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


if __name__ == "__main__":
    unittest.main()
