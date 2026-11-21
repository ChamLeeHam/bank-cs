import unittest
from unittest.mock import patch
from langchain_core.messages import AIMessage, HumanMessage

from src.nodes.rag_answer import NOT_FOUND, RagAnswerNode
from src.schema.state import create_default_state
from src.utils.financial_products import financial_product_query
from src.utils.scope_utils import is_unsupported_lending_question, unsupported_scope_reason


class RagScopeTests(unittest.TestCase):
    @patch("src.nodes.rag_answer.response_utils.process")
    @patch("src.nodes.rag_answer.get_product_attr")
    def test_overseas_fee_and_arrival_require_both_sources(self, get_attr, generate):
        get_attr.side_effect = [
            {"product_zh": "海外电汇", "name_zh": "手续费", "value_zh": "每笔 SGD 10"},
            {"product_zh": "海外电汇", "name_zh": "到账时间", "value_zh": "一般 1–3 个工作日"},
        ]
        generate.return_value = {"answer": "每笔 SGD 10。", "generator": "deepseek",
                                 "citations_valid": True, "used_sources": [1], "generation_attempts": 1}
        node = RagAnswerNode()
        result = node.process(create_default_state(masked_question="境外汇款要收多少手续费，一般多久能到账？",
                                                   lang="zh", entities={"attr": "arrival_time"}))
        self.assertEqual([call.args for call in get_attr.call_args_list],
                         [("overseas_tt", "fee"), ("overseas_tt", "arrival_time")])
        self.assertIn("每笔 SGD 10", result["answer"])
        self.assertIn("1–3 个工作日", result["answer"])
        self.assertEqual([source["section"] for source in result["sources"]],
                         ["海外电汇 · 手续费", "海外电汇 · 到账时间"])

    @patch("src.nodes.rag_answer.response_utils.process")
    @patch("src.nodes.rag_answer.get_product_attr", return_value=None)
    def test_overseas_compound_searches_faq_for_both_fields_when_graph_missing(self, get_attr, generate):
        node = RagAnswerNode()
        question = "境外汇款要收多少手续费，一般多久能到账？"
        fee = {"doc": "fees", "title": "费用", "section": "电汇手续费", "text": "每笔 SGD 10。", "rerank_score": 1}
        timing = {"doc": "transfers", "title": "汇款", "section": "电汇时效", "text": "一般 1–3 个工作日。", "rerank_score": 1}
        generate.return_value = {"answer": "每笔 SGD 10，一般 1–3 个工作日。", "generator": "deepseek",
                                 "citations_valid": True, "used_sources": [1, 2], "generation_attempts": 1}
        with patch.object(node, "retrieve", side_effect=[("fee", [fee], {}), ("timing", [timing], {})]) as retrieve:
            result = node.process(create_default_state(masked_question=question, lang="zh"))
        self.assertEqual([call.args[0] for call in retrieve.call_args_list],
                         ["海外电汇手续费是多少？", "海外电汇一般多久到账？"])
        self.assertEqual([source["doc"] for source in result["sources"]], ["fees", "transfers"])

    def test_overseas_calendar_followup_keeps_only_the_requested_facet(self):
        prior = "境外汇款要收多少手续费，一般多久能到账？"
        current = "那 weekend 也算吗？"
        state = create_default_state(masked_question=current, resolved_question=f"{prior} {current}", lang="mixed",
                                     messages=[HumanMessage(prior), AIMessage("电汇资料"), HumanMessage(current)])
        node = RagAnswerNode()
        with patch.object(node, "answer", return_value={"answer": "资料未明确。", "sources": []}) as answer:
            node.process(state)
        self.assertIn("海外电汇到账时效", answer.call_args.args[0])
        self.assertNotIn("手续费", answer.call_args.args[0])

    @patch("src.nodes.rag_answer.response_utils.process")
    @patch("src.nodes.rag_answer.get_product_attr")
    def test_graph_attribute_is_generated_and_cited(self, get_attr, generate):
        get_attr.return_value = {"product_zh": "PayNow", "name_zh": "手续费", "value_zh": "免费"}
        generate.return_value = {"answer": "使用 PayNow 转账无需支付手续费。", "generator": "deepseek",
                                 "citations_valid": True, "used_sources": [1], "generation_attempts": 1}
        node = RagAnswerNode()
        result = node.process(create_default_state(masked_question="PayNow 手续费是多少？", lang="zh",
                                                   entities={"products": ["paynow"], "attr": "fee"}))
        self.assertIn("免费", generate.call_args.args[2][0]["text"])
        self.assertEqual(result["answer"], generate.return_value["answer"])
        self.assertEqual(result["sources"][0]["doc"], "neo4j")
        self.assertEqual(node.last_run["generator"], "deepseek")

    @patch("src.nodes.rag_answer.response_utils.process")
    @patch("src.nodes.rag_answer.read_financial_products")
    def test_full_catalog_generates_without_dropping_products(self, read_catalog, generate):
        read_catalog.return_value = [{"name_zh": "灵活储蓄", "category_zh": "存款"},
                                     {"name_zh": "旅行信用卡", "category_zh": "银行卡"}]
        generate.return_value = {"answer": "有灵活储蓄和旅行信用卡。", "generator": "deepseek",
                                 "citations_valid": True, "used_sources": [1, 2], "generation_attempts": 1}
        node = RagAnswerNode()
        state = create_default_state(masked_question="你们有哪些金融产品？", lang="zh")
        result = node.process(state)
        self.assertTrue(generate.call_args.kwargs["complete_catalog"])
        self.assertEqual(result["answer"], generate.return_value["answer"])
        generate.return_value["answer"] = "有灵活储蓄。"
        self.assertIn("旅行信用卡", node.process(state)["answer"])
        self.assertEqual(node.last_run["generator"], "graph")

    @patch("src.nodes.rag_answer.response_utils.process")
    def test_pin_unblock_generates_from_only_the_relevant_passage(self, generate):
        node = RagAnswerNode()
        question = "我的 PIN 被锁了，怎么解锁？"
        doc = {"doc": "card_services", "title": "银行卡服务", "section": "修改 PIN 与 PIN 被锁",
               "text": "PIN\n在 ATM 修改 PIN。连续输错 PIN 3 次会锁定，请在 App 解锁。", "rerank_score": 1}
        generate.return_value = {"answer": "请打开 App 解锁 PIN。", "generator": "deepseek",
                                 "citations_valid": True, "used_sources": [1], "generation_attempts": 1}
        with patch.object(node, "retrieve", return_value=(question, [doc], {})):
            result = node.answer(question, [], "zh")
        self.assertNotIn("ATM", generate.call_args.args[2][0]["text"])
        self.assertEqual(result["answer"], generate.return_value["answer"])
        self.assertEqual(result["sources"][0]["section"], doc["section"])

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
        state = create_default_state(masked_question="How do I apply for a student loan?", lang="en",
                                     entities={"products": ["savings_account"], "attr": "annual_fee"})
        result = node.process(state)
        self.assertEqual(result["answer"], NOT_FOUND["en"])
        self.assertEqual(result["sources"], [])
        self.assertEqual(node.last_run["scope_rejected"], "unsupported_lending")

    def test_financial_catalog_filters_by_product_category_or_use(self):
        self.assertEqual(financial_product_query("定存和目标储蓄有什么区别？"),
                         {"product_ids": ["fixed_deposit", "goal_savings"]})
        self.assertEqual(financial_product_query("有哪些贷款产品？"), {"category": "retail_loans"})
        self.assertEqual(financial_product_query("What financing options do you offer for buying a car?"),
                         {"use_case": "vehicle_purchase"})
        self.assertEqual(financial_product_query("有哪些金融产品？"), {})
        self.assertEqual(financial_product_query("外币定存支持哪些币种？"),
                         {"product_ids": ["foreign_currency_deposit"]})
        self.assertEqual(financial_product_query("准备留一笔应急钱，哪些产品可以随时取？"),
                         {"use_case": "emergency_saving"})
        self.assertEqual(financial_product_query("日常消费和出国消费分别有哪些卡？年费和外币手续费怎么比？"),
                         {"use_cases": ["daily_spend", "travel_spend"]})
        self.assertEqual(financial_product_query("What debit cards do you offer?"),
                         {"category": "card", "use_case": "debit_payments"})
        for question in ("帮我申请汽车贷款", "How do I check my home loan application status?",
                         "What student loans do you offer?", "买车转账有哪些方式？", "推荐买什么基金？",
                         "PayNow 手续费是多少？", "有哪些方法解冻银行卡？"):
            with self.subTest(question=question):
                self.assertIsNone(financial_product_query(question))

    @patch("src.nodes.rag_answer.response_utils.process")
    @patch("src.nodes.rag_answer.read_financial_products")
    def test_emergency_savings_answer_focuses_on_verified_withdrawal(self, read_catalog, generate):
        read_catalog.return_value = [
            {"name_zh": "储蓄账户", "withdrawal_zh": "可留用余额，锁定资金须先解锁。"},
            {"name_zh": "灵活储蓄", "withdrawal_zh": "可单独存放新元备用金。"},
            {"name_zh": "未核实支取条款的产品", "withdrawal_zh": None},
        ]
        generate.return_value = {"answer": "储蓄账户和灵活储蓄都没有固定存期。储蓄账户的锁定资金须先解锁。",
                                 "generator": "deepseek", "citations_valid": True,
                                 "used_sources": [1, 2], "generation_attempts": 1}
        state = create_default_state(masked_question="准备留一笔应急钱，哪些产品可以随时取？", lang="zh")
        node = RagAnswerNode()
        result = node.process(state)
        self.assertEqual(result["answer"], generate.return_value["answer"])
        self.assertEqual(node.last_run["generator"], "deepseek")
        self.assertEqual(result["sources"][0]["title"], "金融产品目录")
        self.assertEqual([source["section"] for source in result["sources"]], ["储蓄账户", "灵活储蓄"])
        evidence = generate.call_args.args[2]
        self.assertEqual([doc["section"] for doc in evidence], ["储蓄账户", "灵活储蓄"])
        self.assertIn("锁定资金须先解锁", evidence[0]["text"])

        generate.return_value = {"answer": None, "generator": "source_text", "generation_attempts": 1}
        fallback = node.process(state)
        self.assertIn("锁定资金须先解锁", fallback["answer"])
        self.assertNotIn("目录未列", fallback["answer"])
        self.assertEqual(node.last_run["generator"], "graph")

    @patch("src.nodes.rag_answer.response_utils.process",
           return_value={"answer": None, "generator": "source_text", "generation_attempts": 1})
    @patch("src.nodes.rag_answer.read_financial_products")
    def test_home_loan_overview_reads_graph_and_returns_source(self, read_catalog, generate):
        read_catalog.return_value = [
            {"name_en": "Home Loan", "use_en": "Home purchase", "overview_en": "Demo overview.",
             "terms_en": "Approval requires assessment."}
        ]
        state = create_default_state(masked_question="What is a home loan?", lang="en")
        result = RagAnswerNode().process(state)
        read_catalog.assert_called_once_with({"product_ids": ["home_loan"]})
        self.assertEqual(result["sources"][0]["section"], "Home Loan")
        self.assertIn("Approval requires assessment.", result["answer"])


if __name__ == "__main__":
    unittest.main()
