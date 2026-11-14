import unittest
from unittest.mock import patch

from langchain_core.messages import AIMessage, HumanMessage

from src.nodes.intent_router import IntentRouterNode
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

    def test_explicit_transaction_disputes_route_to_handoff(self):
        cases = (
            ("How do I dispute a card transaction and get a chargeback?", "transaction_dispute"),
            ("I was charged twice for one payment", "transaction_charged_twice"),
            ("信用卡这笔交易不认可，想申请拒付", "transaction_dispute"),
            ("退款一直未到账，能帮我查吗？", "Refund_not_showing_up"),
            ("我的 card 被扣了两次钱，可以处理吗？", "transaction_charged_twice"),
        )
        router = IntentRouterNode()
        for question, expected_sub_intent in cases:
            with self.subTest(question=question):
                state = create_default_state(masked_question=question, question=question)
                routed = router.process(state)
                self.assertEqual(routed["intent"], "dispute_refund")
                self.assertEqual(routed["sub_intent"], expected_sub_intent)
                self.assertEqual(routed["router_backend"], "keyword")
                state.update(routed)
                decision = GateNode().process(state)
                self.assertEqual((decision["decision"], decision["route"]), ("handoff", "handoff"))

    def test_non_recognised_transaction_keeps_urgent_fraud_route(self):
        question = "I don't recognize this card transaction; please dispute it."
        state = create_default_state(masked_question=question, question=question)
        routed = IntentRouterNode().process(state)
        self.assertEqual(routed["intent"], "fraud_report")
        state.update(routed)
        decision = GateNode().process(state)
        self.assertEqual(decision["route"], "handoff")

    def test_reviewed_unauthorised_activity_routes_to_urgent_handoff(self):
        questions = (
            "I didn't take out money from an ATM but my app statement shows that I did. How can I fix this?",
            "Even though it's late, I wanted to see about disputing a charge. I was just looking through my transactions over the past couple of months and noticed a pretty large amount that was not completed by me. Is this possibly something I can still do.",
            "有人偷了我的卡。虽然我的卡还在我身上，但刚刚有人提了500英镑现金。请尽快帮我。",
        )
        for question in questions:
            with self.subTest(question=question):
                state = create_default_state(masked_question=question, question=question)
                state.update(IntentRouterNode().process(state))
                self.assertEqual(state["intent"], "fraud_report")
                state.update(GateNode().process(state))
                self.assertEqual(state["route"], "handoff")
                with patch("src.nodes.stub_nodes.create_ticket", return_value=42) as create:
                    self.assertEqual(HandoffNode().process(state)["priority"], "urgent")
                    self.assertEqual(create.call_args.args[1], "urgent")

    def test_atm_cash_not_received_does_not_match_unauthorised_rule(self):
        from src.utils.fraud_context import fraud_context

        for question in ("I didn't get cash from an ATM, but the app says I did",
                         "I withdrew cash but received the wrong amount"):
            with self.subTest(question=question):
                self.assertNotEqual(fraud_context(question)[0], "reported")

    def test_general_statement_charge_question_stays_in_faq_route(self):
        questions = (
            "statement 上有一笔看不懂的小额 charge 是什么？",
            "I see a small fee on my statement, what is it?",
            "账单里有笔小额扣费，不太明白是什么费用",
        )
        for question in questions:
            with self.subTest(question=question):
                state = create_default_state(masked_question=question, question=question)
                routed = IntentRouterNode().process(state)
                self.assertEqual((routed["intent"], routed["sub_intent"]),
                                 ("fees_rates", "extra_charge_on_statement"))
                state.update(routed)
                decision = GateNode().process(state)
                self.assertEqual(decision["route"], "rag")

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

    def test_fraud_guidance_and_compound_limit_request(self):
        cases = (
            ("我的卡被盗刷了，额度怎么调？", "zh", "额度问题"),
            ("My card has an unauthorized transaction. How do I change my limit?", "en", "limit"),
            ("我的 card 被盗刷了，how to change limit?", "mixed", "额度问题"),
        )
        router = IntentRouterNode()
        for question, lang, expected in cases:
            with self.subTest(question=question):
                state = create_default_state(masked_question=question, question=question, lang=lang)
                state.update(router.process(state))
                self.assertEqual(state["intent"], "fraud_report")
                state.update(GateNode().process(state))
                self.assertEqual(state["route"], "handoff")
                with patch("src.nodes.stub_nodes.create_ticket", return_value=42):
                    result = HandoffNode().process(state)
                self.assertEqual(result["priority"], "urgent")
                self.assertIn(expected, result["answer"])
                self.assertIn("freeze" if lang == "en" else "冻结", result["answer"])
                self.assertIn("has been frozen" if lang == "en" else "自动冻结", result["answer"])

    def test_hypothetical_and_denial_do_not_create_urgent_ticket(self):
        for question in ("如果我的卡被盗刷了怎么办？", "我的卡没有被盗刷。",
                         "What should I do if my card is used fraudulently?"):
            with self.subTest(question=question):
                state = create_default_state(masked_question=question, question=question,
                                             lang="en" if question.startswith("What") else "zh")
                state.update(IntentRouterNode().process(state))
                self.assertEqual(state["intent"], "unknown")
                state.update(GateNode().process(state))
                self.assertEqual(state["route"], "ask_back")
                if question.startswith(("如果", "What")):
                    answer = AskBackNode().process(state)["answer"]
                    self.assertIn("冻结" if question.startswith("如果") else "freeze", answer)

    def test_hypothetical_does_not_override_actual_report(self):
        question = "如果我的卡被盗刷了怎么办？刚才我的卡被盗刷了，请转人工"
        state = create_default_state(masked_question=question, human_requested=True)
        state.update(IntentRouterNode().process(state))
        self.assertEqual(state["intent"], "fraud_report")
        self.assertEqual(GateNode().process(state)["route"], "handoff")

    def test_confirmed_fraud_after_hypothetical_escalates(self):
        prompt = "如果发现非本人交易，请通过银行官方 App 冻结卡片，或通过银行官方紧急渠道联系银行。请问这是已经发生，还是您在了解一般处理方法？"
        question = "已经发生了"
        state = create_default_state(masked_question=question, question=question, lang="zh", messages=[
            HumanMessage("如果我的卡被盗刷了怎么办？"), AIMessage(prompt), HumanMessage(question)])
        state.update(IntentRouterNode().process(state))
        self.assertEqual(state["intent"], "fraud_report")
        state.update(GateNode().process(state))
        self.assertEqual(state["route"], "handoff")
        with patch("src.nodes.stub_nodes.create_ticket", return_value=42):
            self.assertEqual(HandoffNode().process(state)["priority"], "urgent")

    def test_general_information_followup_remains_in_fraud_context(self):
        prompt = "如果发现非本人交易，请通过银行官方 App 冻结卡片，或通过银行官方紧急渠道联系银行。请问这是已经发生，还是您在了解一般处理方法？"
        question = "我只是想了解一下"
        state = create_default_state(masked_question=question, question=question, lang="zh", messages=[
            HumanMessage("如果我的卡被盗刷了怎么办？"), AIMessage(prompt), HumanMessage(question)])
        state.update(IntentRouterNode().process(state))
        state.update(GateNode().process(state))
        self.assertEqual(state["route"], "ask_back")
        self.assertIn("非本人交易", AskBackNode().process(state)["answer"])

    def test_negated_fraud_with_limit_question_does_not_create_urgent_ticket(self):
        for question in ("我的卡没有被盗刷，我想问怎么提高额度", "我的 card 没有被盗刷，只是想问 limit 怎么改"):
            with self.subTest(question=question):
                state = create_default_state(masked_question=question, question=question, lang="zh")
                state.update(IntentRouterNode().process(state))
                self.assertEqual(state["intent"], "unknown")
                state.update(GateNode().process(state))
                self.assertEqual(state["route"], "ask_back")
                self.assertIn("额度", AskBackNode().process(state)["answer"])

    def test_negation_does_not_hide_an_actual_fraud_report(self):
        question = "我的卡没有被盗刷，但这笔交易不是我做的，请转人工"
        state = create_default_state(masked_question=question, question=question, human_requested=True)
        state.update(IntentRouterNode().process(state))
        self.assertEqual(state["intent"], "fraud_report")
        self.assertEqual(GateNode().process(state)["route"], "handoff")

    def test_explicit_report_beats_a_low_score(self):
        state = create_default_state(masked_question="卡一直在我身边，但那笔扣款根本不是我付的。",
                                     intent="card_service", intent_score=0.1)
        state.update(IntentRouterNode().process(state))
        self.assertEqual(state["fraud_signal"], "reported")
        state["intent_score"] = 0.1
        self.assertEqual(GateNode().process(state)["route"], "handoff")

    def test_context_blocks_false_urgent_without_losing_remaining_question(self):
        router = IntentRouterNode()
        for question in ("假设未来遇到非本人交易，我该找谁？",
                         "For future reference, how do I report an unauthorized withdrawal?"):
            with self.subTest(question=question):
                state = create_default_state(masked_question=question)
                state.update(router.process(state))
                self.assertEqual(state["fraud_signal"], "hypothetical")
                self.assertEqual(GateNode().process(state)["route"], "ask_back")

        state = create_default_state(masked_question="没有发生盗刷，我只是想了解借记卡年费。")
        classified = {"intent": "fees_rates", "intent_score": 0.9, "sub_intent": "", "router_backend": "finetuned"}
        with patch.object(router, "_model_ready", return_value=True), \
             patch.object(router, "_classify", return_value=classified) as classify:
            state.update(router.process(state))
        classify.assert_called_once_with("我只是想了解借记卡年费")
        self.assertEqual(GateNode().process(state)["route"], "rag")

    def test_fraud_label_without_evidence_prompts_for_confirmation(self):
        state = create_default_state(masked_question="Something looks off on my card.",
                                     intent="fraud_report", intent_score=0.98, lang="en")
        state.update(GateNode().process(state))
        self.assertEqual(state["route"], "ask_back")
        self.assertIn("Has this happened", AskBackNode().process(state)["answer"])

    @patch("src.nodes.stub_nodes.create_ticket", side_effect=MySQLError("offline", "test"))
    def test_fraud_guidance_remains_when_ticket_creation_fails(self, create):
        self.state.update(intent="fraud_report", masked_question="卡被盗刷了", lang="zh")
        result = HandoffNode().process(self.state)
        self.assertIsNone(result["ticket_id"])
        self.assertIn("无法创建", result["answer"])
        self.assertIn("冻结", result["answer"])

    @patch("src.nodes.stub_nodes.create_ticket", return_value=42)
    def test_non_fraud_handoff_does_not_add_fraud_guidance(self, create):
        self.state.update(intent="dispute_refund", masked_question="同一笔消费被扣两次", lang="zh")
        answer = HandoffNode().process(self.state)["answer"]
        self.assertNotIn("盗用", answer)
        self.assertNotIn("冻结", answer)

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
