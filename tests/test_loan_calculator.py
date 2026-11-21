import unittest
from unittest.mock import patch
from uuid import uuid4

from langchain_core.messages import HumanMessage
from pydantic import ValidationError

from src.nodes.bank_agent import TOOLS, BankAgentNode
from src.nodes.loan_calculator import (
    LOAN_CALCULATION_INTENT, calculate_loan_plans, is_loan_calculation_request, recommend_catalog_loan,
)
from src.schema.main_graph import route_after_gate, run_query
from src.schema.state import create_default_state
from src.utils.loan_response import organize_loan_answer


class LoanCalculatorTests(unittest.TestCase):
    def setUp(self):
        organizer = patch("src.nodes.bank_agent.organize_loan_answer", return_value=None)
        organizer.start()
        self.addCleanup(organizer.stop)

    @patch("src.utils.loan_response.get_llm_client")
    def test_deepseek_rewrites_followup_only_with_calculator_values(self, get_client):
        facts = "把本金改为 SGD 5000.00。首期月供 SGD 466.67，末期 SGD 420.83。"
        reply = get_client.return_value.invoke.return_value
        reply.content = '{"answer":"借 SGD [N1] 的话，首期约还 SGD [N2]，最后一期约还 SGD [N3]。"}'
        self.assertEqual(organize_loan_answer(facts, "zh", "principal"),
                         "借 SGD 5000.00 的话，首期约还 SGD 466.67，最后一期约还 SGD 420.83。")
        messages = get_client.return_value.invoke.call_args.args[0]
        self.assertNotIn("5000.00", messages[1]["content"])

        reply.content = '{"answer":"借 SGD [N1]，首期 SGD [N3]，末期 SGD [N2]。"}'
        self.assertIsNone(organize_loan_answer(facts, "zh", "principal"))
        reply.content = '{"answer":"借 SGD [N1]，首期 SGD [N2]，末期 SGD [N3]。手续费 SGD 0。"}'
        self.assertIsNone(organize_loan_answer(facts, "zh", "principal"))

    def test_calculation_uses_bank_agent_and_asks_for_missing_rates(self):
        question = "想比较一下车贷月供，应该怎么选？"
        self.assertTrue(is_loan_calculation_request(question))
        self.assertFalse(is_loan_calculation_request("有哪些贷款产品？"))
        self.assertFalse(is_loan_calculation_request("帮我查贷款申请进度", awaiting_details=True))
        self.assertIn(calculate_loan_plans, TOOLS)
        state = create_default_state(route="rag", sub_intent=LOAN_CALCULATION_INTENT,
                                     masked_question=question, lang="zh", messages=[HumanMessage(question)])
        self.assertEqual(route_after_gate(state), "bank_agent")
        with patch("src.nodes.bank_agent.get_bank_agent") as get_agent:
            result = BankAgentNode().process(state)
        self.assertIn("想借多少", result["answer"])
        self.assertNotIn("利率", result["answer"])
        self.assertTrue(result["loan_calculation_pending"])
        get_agent.assert_not_called()

    def test_complete_quoted_plan_does_not_depend_on_the_model(self):
        question = "试算个人贷款 SGD 12000，固定年利率 12%，12 期，等额本金，手续费 SGD 50。我的月供预算是 SGD 1119。"
        state = create_default_state(route="rag", sub_intent=LOAN_CALCULATION_INTENT,
                                     masked_question=question, lang="zh", messages=[HumanMessage(question)])
        with patch("src.nodes.bank_agent.get_bank_agent", side_effect=ConnectionError) as get_agent:
            result = BankAgentNode().process(state)
        get_agent.assert_not_called()
        self.assertEqual(result["tool_calls"][0]["tool"], "calculate_loan_plans")
        self.assertIn("SGD 1.00", result["answer"])
        self.assertIn("首期月供约 SGD 1120.00，末期约 SGD 1010.00", result["answer"])
        self.assertIn("总成本 SGD 830.00", result["answer"])

    def test_single_plan_monthly_estimate_excludes_unknown_fee_without_ranking(self):
        question = "帮我试算个人贷款，借 SGD 12000，固定年利率 8%，24 期，月供多少？"
        state = create_default_state(route="rag", sub_intent=LOAN_CALCULATION_INTENT,
                                     masked_question=question, lang="zh", messages=[HumanMessage(question)])
        with patch("src.nodes.bank_agent.get_bank_agent") as get_agent:
            result = BankAgentNode().process(state)
        get_agent.assert_not_called()
        self.assertIn("每月约还 SGD 542.73", result["answer"])
        self.assertIn("手续费待确认", result["answer"])
        for text in ("建议选", "总成本", "手续费 SGD 0"):
            self.assertNotIn(text, result["answer"])
        args = result["tool_calls"][0]["args"]
        self.assertIsNone(args["plans"][0]["upfront_fee"])
        estimate = calculate_loan_plans.invoke(args)
        self.assertIsNone(estimate["recommended_plan_index"])
        self.assertEqual(estimate["recommendation_reason"], "single_plan_estimate")
        self.assertIsNone(estimate["plans"][0]["total_cost"])

    @patch("src.schema.main_graph.write_turn_log")
    @patch("src.nodes.stub_nodes.find_products", return_value=[])
    def test_followup_recalculates_with_prior_terms_until_topic_changes(self, find_products, write_log):
        session_id = str(uuid4())
        opening = "试算个人贷款 SGD 12000，固定年利率 12%，12 期，等额本金，手续费 SGD 50。我的月供预算是 SGD 1119"
        with patch("src.nodes.bank_agent.get_bank_agent", side_effect=ConnectionError) as get_agent:
            first = run_query(session_id, "C001", opening, verbose=False)
            second = run_query(session_id, "C001", "那如果是150000呢？", verbose=False)
            third = run_query(session_id, "C001", "4000?", verbose=False)
            fourth = run_query(session_id, "C001", "月供预算改成 1500 呢？", verbose=False)
            new_topic = run_query(session_id, "C001", "你好", verbose=False)
            after_topic_change = run_query(session_id, "C001", "4000?", verbose=False)
            isolated = run_query(str(uuid4()), "C001", "4000?", verbose=False)
        get_agent.assert_not_called()
        self.assertEqual(first["loan_calculation_context"]["args"]["principal"], "12000.00")
        self.assertEqual(second["tool_calls"][0]["tool"], "calculate_loan_plans")
        self.assertIn("本金改为 SGD 150000.00", second["answer"])
        self.assertIn("首期月供约 SGD 14000.00，末期约 SGD 12625.00", second["answer"])
        self.assertEqual(third["lang"], "zh")
        self.assertEqual(third["tool_calls"][0]["tool"], "calculate_loan_plans")
        self.assertIn("本金改为 SGD 4000.00", third["answer"])
        self.assertIn("首期月供约 SGD 373.33，末期约 SGD 336.67", third["answer"])
        self.assertIn("总成本 SGD 310.00", third["answer"])
        self.assertEqual(fourth["loan_calculation_context"]["args"]["monthly_budget"], "1500.00")
        self.assertEqual(new_topic["loan_calculation_context"], {})
        self.assertEqual(after_topic_change["route"], "ask_back")
        self.assertEqual(after_topic_change["lang"], "zh")
        self.assertIn("贷款本金、月供预算，还是其他金额", after_topic_change["answer"])
        self.assertEqual(after_topic_change["tool_calls"], [])
        self.assertEqual(isolated["route"], "ask_back")
        self.assertEqual(isolated["tool_calls"], [])

    @patch("src.nodes.loan_calculator.run_cypher")
    def test_catalog_tool_loads_terms_and_ranks_without_customer_quotes(self, read_plans):
        read_plans.return_value = [
            {"plan_id": "car_36", "term_months": 36, "annual_rate_percent": "4.50", "upfront_fee": "100",
             "name_zh": "汽车贷款", "name_en": "Car Loan"},
            {"plan_id": "car_48", "term_months": 48, "annual_rate_percent": "4.90", "upfront_fee": "150",
             "name_zh": "汽车贷款", "name_en": "Car Loan"},
        ]
        result = recommend_catalog_loan.invoke({"loan_type": "car_loan", "principal": "30000", "monthly_budget": "750"})
        self.assertEqual(result["recommended_plan_index"], 1)
        self.assertEqual(result["plans"][1]["first_month_payment"], "689.52")
        self.assertEqual(result["catalog_product"]["zh"], "汽车贷款")
        self.assertEqual(read_plans.call_args.args[1], {"loan_type": "car_loan"})

    def test_compares_cost_only_among_plans_within_budget(self):
        plans = [
            {"name": "A", "loan_type": "car_loan", "annual_rate_percent": 0, "term_months": 12, "upfront_fee": "0"},
            {"name": "B", "loan_type": "car_loan", "annual_rate_percent": 12, "term_months": 24, "upfront_fee": "100"},
        ]
        result = calculate_loan_plans.invoke({"principal": "12000", "plans": plans, "monthly_budget": "700"})
        self.assertEqual(result["recommended_plan_index"], 1)
        self.assertEqual(result["plans"][0]["total_repayment"], "12000.00")
        self.assertFalse(result["plans"][0]["within_budget"])
        self.assertEqual(result["plans"][1]["first_month_payment"], "564.88")
        self.assertEqual(result["plans"][1]["total_cost"], "1657.16")

        no_fit = calculate_loan_plans.invoke({"principal": "12000", "plans": plans, "monthly_budget": "500"})
        self.assertIsNone(no_fit["recommended_plan_index"])
        self.assertEqual(no_fit["recommendation_reason"], "no_plan_within_budget")

    def test_equal_principal_uses_first_month_for_budget_and_includes_fees(self):
        plan = {"name": "A", "loan_type": "home_loan", "annual_rate_percent": 12,
                "term_months": 12, "upfront_fee": "50", "repayment": "equal_principal"}
        result = calculate_loan_plans.invoke({"principal": "12000", "plans": [plan], "monthly_budget": "1119"})
        row = result["plans"][0]
        self.assertEqual(row["first_month_payment"], "1120.00")
        self.assertEqual(row["last_month_payment"], "1010.00")
        self.assertEqual(row["total_interest"], "780.00")
        self.assertEqual(row["total_repayment"], "12830.00")
        self.assertIsNone(result["recommended_plan_index"])

    def test_requires_explicit_rate_and_upfront_fees(self):
        plan = {"name": "A", "loan_type": "personal_loan", "term_months": 12}
        with self.assertRaises(ValidationError):
            calculate_loan_plans.invoke({"principal": "12000", "plans": [plan]})
