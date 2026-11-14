import unittest

from src.nodes.rag_answer import NOT_FOUND, RagAnswerNode
from src.schema.state import create_default_state
from src.utils.scope_utils import is_unsupported_lending_question, unsupported_scope_reason


class RagScopeTests(unittest.TestCase):
    def test_detects_unsupported_lending_topics_in_english_and_chinese(self):
        for question in (
            "How do I apply for a home loan?",
            "Does Lion City Bank offer mortgages or home loans?",
            "HDB loan eligibility",
            "请问房贷怎么申请？",
            "我想了解按揭利率",
        ):
            with self.subTest(question=question):
                self.assertTrue(is_unsupported_lending_question(question))

    def test_does_not_flag_supported_faq_questions(self):
        for question in (
            "Can I open an account if I'm 15?",
            "What is the transfer fee?",
            "My card was declined overseas",
            "PayNow 转账要收费吗？",
        ):
            with self.subTest(question=question):
                self.assertFalse(is_unsupported_lending_question(question))

    def test_rejects_business_account_questions_that_match_personal_account_faq(self):
        for question in (
            "How do I open a business account for my Singapore company?",
            "Does Lion City Bank offer corporate banking accounts?",
            "我公司想开企业账户，需要什么资料？",
            "商业银行账户要怎么申请？",
        ):
            with self.subTest(question=question):
                self.assertEqual(unsupported_scope_reason(question), "unsupported_business_banking")

    def test_business_context_without_a_business_account_is_not_rejected(self):
        self.assertIsNone(unsupported_scope_reason("My business paid a supplier using PayNow"))

    def test_rejects_transaction_disputes_that_require_handoff(self):
        for question in (
            "How do I dispute a card transaction and get a chargeback?",
            "I was charged twice for one payment",
            "The card payment was charged the wrong amount",
            "退款一直未到账，能帮我查吗？",
            "这笔信用卡交易我不认可，想申请拒付",
            "这笔交易金额不对，多扣了钱",
        ):
            with self.subTest(question=question):
                self.assertEqual(unsupported_scope_reason(question), "unsupported_transaction_dispute")

    def test_retrieval_rejects_business_accounts_without_search(self):
        node = RagAnswerNode()
        question = "我公司想开企业账户，需要什么资料？"
        rewritten, docs, info = node.retrieve(question, [])
        self.assertEqual(rewritten, question)
        self.assertEqual(docs, [])
        self.assertEqual(info["scope_rejected"], "unsupported_business_banking")

    def test_retrieval_rejects_lending_question_without_search(self):
        node = RagAnswerNode()
        rewritten, docs, info = node.retrieve("How do I apply for a home loan?", [])
        self.assertEqual(rewritten, "How do I apply for a home loan?")
        self.assertEqual(docs, [])
        self.assertEqual(info["scope_rejected"], "unsupported_lending")

    def test_process_rejects_before_graph_lookup(self):
        node = RagAnswerNode()
        state = create_default_state(masked_question="How do I apply for a home loan?", lang="en",
                                     entities={"products": ["savings_account"], "attr": "annual_fee"})
        result = node.process(state)
        self.assertEqual(result["answer"], NOT_FOUND["en"])
        self.assertEqual(result["sources"], [])
        self.assertEqual(node.last_run["scope_rejected"], "unsupported_lending")


if __name__ == "__main__":
    unittest.main()
