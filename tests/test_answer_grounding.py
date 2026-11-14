import unittest
from unittest.mock import patch
from pathlib import Path
from langchain_core.messages import AIMessage, HumanMessage

from src.nodes.stub_nodes import RefuseNode, GateNode, AskBackNode
from src.nodes.intent_router import IntentRouterNode
from src.nodes.rag_answer import RagAnswerNode
from src.schema.state import create_default_state
from src.utils.response_utils import (is_pin_unblock_question, pin_unblock_source,
                                      statement_charge_answer)


class AnswerGroundingTests(unittest.TestCase):
    def test_stolen_card_hypothetical_without_context_asks_safely(self):
        question = "如果卡被偷了呢？"
        state = create_default_state(masked_question=question, question=question, lang="zh",
                                     intent="card_lost_freeze", intent_score=0.99, messages=[HumanMessage(question)])
        state.update(GateNode().process(state))
        self.assertEqual(state["route"], "ask_back")
        self.assertIn("冻结", AskBackNode().process(state)["answer"])

        prior = AskBackNode().process(state)["answer"]
        for text, route in (("我想了解处理方法", "rag"), ("我要冻结卡", "account")):
            with self.subTest(text=text):
                followup = create_default_state(masked_question=text, question=text, lang="zh", messages=[
                    HumanMessage(question), AIMessage(prior), HumanMessage(text)])
                followup.update(IntentRouterNode().process(followup))
                self.assertEqual(GateNode().process(followup)["route"], route)

    def test_stolen_card_followup_keeps_fee_context_without_overriding_actions(self):
        from src.utils.history_utils import replacement_fee_stolen_followup
        history = [("客户", "补卡费是多少？"), ("客服", "补卡费为 SGD 15；被盗并报警则免费。")]
        question = "如果卡被偷了呢？"
        state = create_default_state(masked_question=question, question=question, lang="zh", messages=[
            HumanMessage(history[0][1]), AIMessage(history[1][1]), HumanMessage(question)])
        state.update(IntentRouterNode().process(state))
        self.assertEqual(GateNode().process(state)["route"], "rag")
        with patch.object(RagAnswerNode, "answer", return_value={"answer": "被盗并报警后补卡免费。", "sources": []}) as answer:
            result = RagAnswerNode().process(state)
            self.assertEqual(answer.call_args.args[0], "银行卡被盗并报警后补卡费用是多少？")
            self.assertIn("冻结卡片", result["answer"])
        for text in ("我的卡被偷了，帮我冻结", "如果卡被偷了，请冻结", "卡被盗刷了"):
            self.assertIsNone(replacement_fee_stolen_followup(text, history))
        self.assertIsNone(replacement_fee_stolen_followup(question, []))
        self.assertIsNone(replacement_fee_stolen_followup(question, [("客户", "补卡费多少？"), ("客服", "[私人业务结果已展示]")]))

    def test_annual_fee_followup_preserves_public_context(self):
        question = "它可以免吗？"
        messages = [HumanMessage("信用卡年费是多少？"), AIMessage("信用卡的年费：SGD 196.20。"), HumanMessage(question)]
        state = create_default_state(masked_question=question, question=question, lang="zh", messages=messages)
        state.update(IntentRouterNode().process(state))
        self.assertEqual(GateNode().process(state)["route"], "rag")
        with patch.object(RagAnswerNode, "answer", return_value={"answer": "年费"}) as answer:
            RagAnswerNode().process(state)
            self.assertEqual(answer.call_args.args[0], "信用卡年费可以豁免吗？")

        from src.utils.history_utils import annual_fee_followup
        self.assertIsNone(annual_fee_followup(question, [("客户", "我的信用卡年费是多少？"), ("客服", "[私人业务结果已展示]")]))
        self.assertIsNone(annual_fee_followup("它可以免吗？我要投诉", [("客户", "信用卡年费是多少？"), ("客服", "信用卡年费")]))

    def test_statement_fee_ordinal_followup_uses_previous_answer(self):
        previous = "常见项目包括外币交易费、ATM 费用、年费、补卡费或逾期还款费。"
        question = "第四个类型是什么"
        state = create_default_state(masked_question=question, question=question, lang="zh",
                                     messages=[HumanMessage("有哪些费用？"), AIMessage(previous), HumanMessage(question)])
        state.update(IntentRouterNode().process(state))
        self.assertEqual(state["intent"], "fees_rates")
        self.assertEqual(GateNode().process(state)["route"], "rag")
        node = RagAnswerNode()
        with patch.object(node, "answer", return_value={"answer": "补卡费", "sources": []}) as answer:
            node.process(state)
            self.assertEqual(answer.call_args.args[0], "补卡费是什么？")

        state.update(masked_question="第六个是什么", messages=[HumanMessage("费用？"), AIMessage(previous),
                                                           HumanMessage("第六个是什么")])
        self.assertIn("无法确定", node.process(state)["answer"])

    def test_statement_fee_ordinal_from_faq_source_text(self):
        previous = "账单上常见的小额费用包括：外币交易费（3.25%）、海外 ATM 取款费（SGD 5）、信用卡年费、补卡费（SGD 15）以及逾期还款费。"
        question = "第5个是什么"
        state = create_default_state(masked_question=question, question=question, lang="zh",
                                     messages=[HumanMessage("有哪些费用？"), AIMessage(previous), HumanMessage(question)])
        state.update(IntentRouterNode().process(state))
        self.assertEqual(GateNode().process(state)["route"], "rag")
        node = RagAnswerNode()
        with patch.object(node, "answer", return_value={"answer": "逾期还款费", "sources": []}) as answer:
            node.process(state)
            self.assertEqual(answer.call_args.args[0], "逾期还款费是什么？")

    def test_statement_fee_ordinal_from_generated_wording(self):
        previous = "常见的小额费用可能包括：外币交易费（3.25%）、海外 ATM 取款费（SGD 5）、信用卡年费、补卡费（SGD 15）以及逾期还款费。"
        question = "第5个是什么？"
        state = create_default_state(masked_question=question, question=question, lang="zh",
                                     messages=[HumanMessage("一般这些费用是什么？"), AIMessage(previous), HumanMessage(question)])
        state.update(IntentRouterNode().process(state))
        self.assertEqual(GateNode().process(state)["route"], "rag")
        with patch.object(RagAnswerNode, "answer", return_value={"answer": "逾期还款费"}) as answer:
            RagAnswerNode().process(state)
            self.assertEqual(answer.call_args.args[0], "逾期还款费是什么？")

        state["messages"] = [HumanMessage("有什么费用？"), AIMessage("费用种类视卡片而定。"), HumanMessage(question)]
        self.assertIn("告诉我", RagAnswerNode().process(state)["answer"])

        state["messages"] = [HumanMessage("一般这些费用是什么？"), AIMessage("账单上的小额费用常见于外币交易费（3.25%）、海外 ATM 取款费（SGD 5）、信用卡年费、补卡费（SGD 15）和逾期还款费。"), HumanMessage(question)]
        with patch.object(RagAnswerNode, "answer", return_value={"answer": "逾期还款费"}) as answer:
            RagAnswerNode().process(state)
            self.assertEqual(answer.call_args.args[0], "逾期还款费是什么？")

    def test_pin_unblock_uses_relevant_source_in_requested_language(self):
        text = (Path(__file__).resolve().parents[1] / "data/kb/card_services.md").read_text(encoding="utf-8")
        section = text.split("## 修改 PIN 与 PIN 被锁", 1)[1].split("\n## ", 1)[0]
        docs = [{"text": "PIN\n" + section}]
        for question, lang, expected in [
            ("How can I unlock my card PIN?", "en", "To unblock it"),
            ("My card PIN kena lock 了，how to unlock ah?", "zh", "解锁方法"),
        ]:
            with self.subTest(question=question):
                self.assertTrue(is_pin_unblock_question(question))
                answer, _ = pin_unblock_source(docs, lang)
                self.assertIn(expected, answer)
                self.assertNotIn("ATM", answer)

    def test_unrelated_question_does_not_mention_investment(self):
        node = RefuseNode()
        weather = node.process({"lang": "zh", "intent": "out_of_scope", "sub_intent": "chitchat",
                                "masked_question": "今天天气怎么样？"})["answer"]
        self.assertNotIn("投资", weather)
        investment = node.process({"lang": "en", "intent": "out_of_scope", "sub_intent": "investment_advice",
                                   "masked_question": "Which stock should I buy?"})["answer"]
        self.assertIn("investment advice", investment)

    def test_statement_charge_answer_is_cautious_and_uses_real_faq(self):
        text = (Path(__file__).resolve().parents[1] / "data/kb/fees_rates.md").read_text(encoding="utf-8")
        section = text.split("## 账单上看不懂的费用 / Unfamiliar charges on your statement", 1)[1].split("\n## ", 1)[0]
        doc = {
            "doc": "fees_rates",
            "title": "费用与汇率 / Fees and exchange rates",
            "section": "账单上看不懂的费用 / Unfamiliar charges on your statement",
            "sub_intents": ["extra_charge_on_statement"],
            "text": section,
            "rerank_score": 0.8,
        }

        for lang, expected in (("zh", "仅凭这段描述还无法确认"), ("en", "I can’t identify the exact charge")):
            with self.subTest(lang=lang):
                result = statement_charge_answer([doc], lang)
                self.assertIsNotNone(result)
                answer, source = result
                self.assertIn(expected, answer)
                self.assertIn("App" if lang == "zh" else "app", answer)
                self.assertIs(source, doc)

    def test_statement_charge_answer_requires_matching_faq(self):
        self.assertIsNone(statement_charge_answer([], "zh"))


if __name__ == "__main__":
    unittest.main()
