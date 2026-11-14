import json
import unittest
from unittest.mock import MagicMock, patch

from src.schema.exceptions import MySQLError
from src.schema.main_graph import _run
from src.schema.main_graph import run_query
from src.nodes.stub_nodes import GateNode, RefuseNode
from src.schema.state import create_default_state, create_turn_input
from src.utils.audit_utils import write_turn_log


class AuditTests(unittest.TestCase):
    @patch("src.schema.main_graph.write_turn_log")
    def test_greeting_is_answered_and_recorded_as_greeting(self, write):
        state = run_query("greeting-route-test", "C001", "你好", verbose=False)
        self.assertEqual((state["intent"], state["decision"], state["route"]),
                         ("greeting", "answer", "greeting"))
        self.assertIn("您好", state["answer"])
        logged_state = write.call_args.args[0]
        self.assertEqual((logged_state["decision"], logged_state["route"]), ("answer", "greeting"))

    def test_out_of_scope_remains_refusal(self):
        state = create_default_state(intent="out_of_scope", sub_intent="investment_advice", intent_score=0.99,
                                     masked_question="推荐股票", lang="zh")
        state.update(GateNode().process(state))
        self.assertEqual((state["decision"], state["route"]), ("refuse", "refuse"))
        self.assertIn("不能提供投资建议", RefuseNode().process(state)["answer"])
    @patch("src.utils.audit_utils.time.time", return_value=105.0)
    @patch("src.utils.audit_utils.execute", return_value={"rowcount": 1, "lastrowid": 1})
    def test_account_data_and_tool_arguments_not_logged(self, execute, clock):
        state = create_default_state(session_id="s", customer_id="C001", route="account", lang="zh",
                                     masked_question="查询尾号 **** 1234", answer="余额 5000",
                                     turn_started_at=100.0, trace=["input_guard", "account_agent", "output_guard"],
                                     tool_calls=[{"tool": "get_accounts", "args": {"secret": "123456"}}])
        write_turn_log(state)
        sql, args = execute.call_args.args
        self.assertIn("insert into turn_log", sql.lower())
        self.assertEqual(args[2], "[私人业务问题已隐藏]")
        self.assertEqual(json.loads(args[10]), state["trace"])
        self.assertEqual(json.loads(args[11]), [{"tool": "get_accounts"}])
        self.assertEqual(args[12], "[账户结果已隐藏]")
        self.assertEqual(args[13], 5000)
        self.assertNotIn("123456", str(args))

    def test_start_time_is_set_each_turn(self):
        self.assertIsNotNone(create_turn_input("s", "C001", "你好")["turn_started_at"])

    @patch("src.schema.main_graph.write_turn_log")
    @patch("src.schema.main_graph._app")
    def test_pause_does_not_log_and_completion_logs_once(self, app, write):
        state = {"lang": "zh", "answer": "完成"}
        app.get_state.return_value.values = state
        item = MagicMock()
        item.value = {"type": "otp", "message_zh": "请输入", "message_en": "Enter"}
        app.stream.return_value = [{"__interrupt__": [item]}]
        self.assertEqual(_run("s", {}, False)["answer"], "请输入")
        write.assert_not_called()
        app.stream.return_value = [{"output_guard": {}}]
        _run("s", {}, False)
        write.assert_called_once()

    @patch("src.schema.main_graph.write_turn_log", side_effect=MySQLError("offline", "test"))
    @patch("src.schema.main_graph._app")
    def test_log_failure_does_not_hide_answer(self, app, write):
        app.get_state.return_value.values = {"lang": "zh", "answer": "正常回答"}
        app.stream.return_value = [{"output_guard": {}}]
        self.assertEqual(_run("s", {}, False)["answer"], "正常回答")


if __name__ == "__main__":
    unittest.main()
