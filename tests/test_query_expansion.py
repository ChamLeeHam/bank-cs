import unittest
from unittest.mock import patch

from langchain_core.messages import AIMessage, HumanMessage

from src.nodes.intent_router import IntentRouterNode
from src.nodes.rag_answer import RagAnswerNode
from src.utils.query_expansion_utils import (expand_card_delivery_query,
                                             is_unanchored_overdue_delivery_followup,
                                             resolve_card_delivery_followup)


class CardDeliveryQueryExpansionTests(unittest.TestCase):
    def test_expands_english_new_card_delay(self):
        expanded = expand_card_delivery_query("My new card still hasn't arrived after two weeks")
        self.assertIn("new or replacement card", expanded)
        self.assertIn("10 working days", expanded)

    def test_expands_chinese_replacement_card_delay(self):
        expanded = expand_card_delivery_query("补发卡两个星期还没收到")
        self.assertIn("补发卡寄送时间", expanded)
        self.assertIn("10个工作日", expanded)

    def test_ignores_unrelated_card_questions(self):
        self.assertIsNone(expand_card_delivery_query("My card was charged twice"))
        self.assertIsNone(expand_card_delivery_query("How do I activate my new card?"))

    def test_resolves_overdue_followup_from_immediately_preceding_delivery_turn(self):
        history = [
            ("客户", "新卡可以寄到新加坡以外吗？"),
            ("客服", "新卡或补发卡只寄送到登记的新加坡地址。"),
        ]
        self.assertEqual(
            resolve_card_delivery_followup("那超过10个工作日没收到呢？", history),
            "新卡或补发卡超过10个工作日仍未收到怎么办？",
        )
        self.assertIsNone(resolve_card_delivery_followup(
            "那超过10个工作日没收到呢？",
            history + [("客户", "PayNow收费吗？"), ("客服", "PayNow免费。")],
        ))

    def test_unanchored_overdue_followup_requires_context_or_card_reference(self):
        question = "What if it still has not arrived after 10 working days?"
        self.assertTrue(is_unanchored_overdue_delivery_followup(question, []))
        self.assertTrue(is_unanchored_overdue_delivery_followup(question, [
            ("客户", "PayNow transfer fee?"), ("客服", "PayNow is free.")
        ]))
        self.assertFalse(is_unanchored_overdue_delivery_followup(question, [
            ("客户", "How long does my new card take to arrive?"),
            ("客服", "Your new card arrives within 5–7 working days by registered mail.")
        ]))
        self.assertFalse(is_unanchored_overdue_delivery_followup(
            "What if my replacement card has not arrived after 10 working days?", []
        ))
        routed = IntentRouterNode().process({"masked_question": question,
                                             "messages": [HumanMessage(question)]})
        self.assertEqual(routed["intent"], "unknown")

    def test_router_recognizes_standalone_english_replacement_card_delivery(self):
        result = IntentRouterNode().process({
            "masked_question": "How long does a replacement card take to arrive?",
            "messages": [HumanMessage("How long does a replacement card take to arrive?")],
        })
        self.assertEqual(result["intent"], "card_service")
        self.assertEqual(result["sub_intent"], "card_delivery_estimate")

    def test_router_and_rag_use_resolved_card_delivery_followup(self):
        question = "那超过10个工作日没收到呢？"
        resolved = "新卡或补发卡超过10个工作日仍未收到怎么办？"
        messages = [
            HumanMessage("新卡可以寄到新加坡以外吗？"),
            AIMessage("新卡或补发卡只寄送到登记的新加坡地址。"),
            HumanMessage(question),
        ]
        routed = IntentRouterNode().process({"masked_question": question, "messages": messages})
        self.assertEqual(routed.get("resolved_question"), resolved)

        state = {"masked_question": question, "resolved_question": resolved, "lang": "zh",
                 "messages": messages, "entities": {}}
        with patch.object(RagAnswerNode, "answer", return_value={"answer": "已联系安排补寄。", "sources": []}) as answer:
            RagAnswerNode().process(state)
        self.assertEqual(answer.call_args.args[0], resolved)


if __name__ == "__main__":
    unittest.main()
