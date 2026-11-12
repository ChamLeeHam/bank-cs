import unittest

from langchain_core.messages import HumanMessage
from langgraph.graph.message import add_messages

from src.nodes.stub_nodes import InputGuardNode, OutputGuardNode
from src.schema.state import create_default_state
from src.schema.main_graph import route_after_gate
from src.utils.history_utils import history_of


class PrivacyTests(unittest.TestCase):
    def test_identifiers_are_masked(self):
        state = create_default_state(question="手机号 91234567，账号 SA-1001-0001，邮箱 abc@example.com")
        masked = InputGuardNode().process(state)["masked_question"]
        for private in ("91234567", "SA-1001-0001", "abc@example.com"):
            self.assertNotIn(private, masked)

    def test_account_history_excludes_question_and_answer(self):
        state = create_default_state(route="account", messages=[HumanMessage("我给妈妈转了 SGD 500", id="u1")],
                                     answer="余额 SGD 5000")
        update = OutputGuardNode().process(state)
        messages = add_messages(state["messages"], update["messages"])
        messages = add_messages(messages, [HumanMessage("FAST 免费吗？")])
        history = str(history_of({"messages": messages}))
        self.assertNotIn("妈妈", history)
        self.assertNotIn("5000", history)
        self.assertNotIn("SGD 500", history)

    def test_private_input_does_not_enter_public_rag(self):
        state = create_default_state(question="我给妈妈转了 SGD 500 到了吗？")
        state.update(InputGuardNode().process(state))
        state["route"] = "rag"  # 即使意图识别误分类，也不调用公开问答模型
        self.assertTrue(state["private_input"])
        self.assertEqual(route_after_gate(state), "ask_back")


if __name__ == "__main__":
    unittest.main()
