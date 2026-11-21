# Bank Agent：DeepSeek 根据客户的问题自己决定调哪些工具、调几次
# 外面的 LangGraph 还是安全和规则这一层：脱敏、意图、转人工、拒答都在 Agent 前面做完
# 出错（比如 DeepSeek 连不上）就退回原来的 rag_answer
# 在项目根目录运行测试：uv run python -m src.nodes.bank_agent
import json
import re
import time
from decimal import Decimal

from langchain.agents import create_agent
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

from src.nodes.agent_tools import get_product_info, search_faq
from src.nodes.loan_calculator import (
    LOAN_CALCULATION_INTENT, LoanPlan, calculate_loan_plans, explicit_single_loan, is_loan_calculation_request,
    loan_followup_arguments, loan_types_mentioned, quoted_loan_rates, recommend_catalog_loan,
)
from src.nodes.rag_answer import NOT_FOUND, RagAnswerNode, asks_overseas_calendar, asks_overseas_fee_and_arrival
from src.schema.base import BaseNode, StateUpdate
from src.schema.state import GraphState
from src.utils.llm_util import get_llm_client
from src.utils.loan_response import organize_loan_answer
from src.utils.response_utils import is_pin_unblock_question, process as generate_grounded_response
from src.utils.history_utils import history_of
from src.utils.reference_bank_utils import is_reference_policy_question, resolve_reference_bank

TOOLS = [search_faq, get_product_info, recommend_catalog_loan, calculate_loan_plans]
MAX_HISTORY = 6  # 最近几条消息给 Agent
GRAPH_SOURCE = {"doc": "neo4j", "title": "术语图谱 / Product graph", "section": "", "rerank_score": None}

SYSTEM_PROMPT = """你是狮城银行（Lion City Bank）的 AI 客服。
1. 只根据工具查到的资料回答，资料里没有的不要自己编，特别是数字
2. 问具体产品的手续费、限额、到账时间、年费，先用 get_product_info；其他问题用 search_faq
3. 只查询客户实际问的内容；先用一次检索，资料足够回答就停止，不要自行增加费用、时间或其他问题
   只有客户问了多件事，或者已有资料不足以回答客户的问题，才继续调用工具
4. 客户用英文就用英文回答，中文或者中英混杂就用中文回答
5. 只回答客户问的内容，不要加入无关的情况；回答简短清楚，不用写"根据资料"这种话，不用自己写来源名称
6. 先用工具取得相关资料，最终回复会基于工具提供的证据重新生成并校验
7. 如果客户给出了账单中的具体交易和已记录手续费，要先明确指出账单金额与收费规则是否吻合；本行手续费、中转行/商户可能另收的费用要区分，不能把“可能收取”说成已经实际扣收
8. 交易状态（如处理中）要单独解释，不要暗示它必然导致某笔费用；如果客户同时问费用和状态，分别回答这两点
9. 不提供投资建议
10. 客户要试算或推荐贷款方案，默认调用 recommend_catalog_loan。客户只需给贷款类型和本金，
    有月供预算就传 monthly_budget，没有就省略。类型、本金缺哪项就追问哪项，
    从客户已说的话里保留车贷、房贷或个人贷款类型。期限、利率和手续费由工具从目录取，别让客户填。
11. 客户明确要算自己提供的报价时才用 calculate_loan_plans。缺期限或固定名义年利率就追问。
    未提供一次性手续费时传 null，明确免手续费才传 0。单个方案直接给月供，不做推荐。
    年换算成月，等额本金传 equal_principal。不要假造金额、报价或审批。"""

LOAN_DETAILS = {
    "zh": "你想借多少新元？如果有月供上限，也可以一起告诉我，我来比较贷款方案。",
    "en": "How much would you like to borrow in SGD? If you have a monthly repayment cap, please include it.",
}
LOAN_TYPE_QUESTION = {
    "zh": "你想比较房贷、车贷，还是个人贷款？",
    "en": "Would you like to compare home loans, car loans, or personal loans?",
}
LOAN_UNAVAILABLE = {
    "zh": "贷款方案暂时无法查询，请稍后重试。",
    "en": "Loan plans are temporarily unavailable. Please try again.",
}

bank_agent = None


# 1. 获取 Agent
def get_bank_agent():
    global bank_agent
    if bank_agent is None:
        bank_agent = create_agent(model=get_llm_client(), tools=TOOLS, system_prompt=SYSTEM_PROMPT)
    return bank_agent


class BankAgentNode(BaseNode):
    name = "bank_agent"

    def __init__(self):
        super().__init__()
        self.fallback = RagAnswerNode()
        self.last_run = {}  # 这一次调了哪些工具，测试和评估用

    def process(self, state: GraphState) -> StateUpdate:
        lang = "en" if state.get("lang") == "en" else "zh"
        question = state.get("masked_question", "")
        reference_bank = resolve_reference_bank(question, history_of(state))
        if is_reference_policy_question(question, reference_bank):
            # The agent is trained as Lion City Bank support; external-bank questions use
            # the source-filtered RAG path so their policies cannot be mixed with local FAQ.
            return self.fallback.process(state)
        if (is_pin_unblock_question(question) or asks_overseas_fee_and_arrival(question)
                or asks_overseas_calendar(state.get("resolved_question") or question)):
            return self.fallback.process(state)
        # state["messages"] 里已经有这一轮的问题了（input_guard 加的），而且是脱敏过的
        messages = (state.get("messages") or [])[-MAX_HISTORY:]
        loan_request = state.get("sub_intent") == LOAN_CALCULATION_INTENT
        if loan_request:
            context = state.get("loan_calculation_context") or {}
            followup = loan_followup_arguments(question, context)
            if followup:
                args, changed_field = followup
                tool = recommend_catalog_loan if context["tool"] == recommend_catalog_loan.name else calculate_loan_plans
                calculation = tool.invoke(args)
                calls = [{"tool": tool.name, "args": args}]
                self.last_run = {"tool_calls": calls}
                return self._finish_loan(calculation, calls, lang, changed_field)
            if not state.get("loan_calculation_pending"):
                messages = messages[-1:]
            else:
                for i in range(len(messages) - 2, -1, -1):
                    message = messages[i]
                    if isinstance(message, HumanMessage) and is_loan_calculation_request(str(message.content)):
                        messages = messages[i:]
                        break
            supplied = " ".join(str(m.content) for m in messages if isinstance(m, HumanMessage))
            rates = quoted_loan_rates(supplied)
            loan_types = loan_types_mentioned(supplied)
            has_amount = re.search(r"\d|[一二两三四五六七八九][万千百]", supplied)
            if len(loan_types) != 1 or not has_amount:
                self.last_run = {"tool_calls": []}
                prompts = []
                if len(loan_types) != 1:
                    prompts.append(LOAN_TYPE_QUESTION[lang])
                if not has_amount:
                    prompts.append(LOAN_DETAILS[lang])
                return {"answer": " ".join(prompts), "loan_calculation_pending": True}
            comparison = explicit_single_loan(supplied, next(iter(loan_types)))
            if comparison:
                if lang == "en":
                    comparison.plans[0].name = "Loan plan"
                args = comparison.model_dump(mode="json")
                calculation = calculate_loan_plans.invoke(args)
                calls = [{"tool": calculate_loan_plans.name, "args": args}]
                self.last_run = {"tool_calls": calls}
                return self._finish_loan(calculation, calls, lang)
        try:
            # recursion_limit：Agent 最多走这么多步，防止一直调工具停不下来
            result = get_bank_agent().invoke({"messages": messages}, config={"recursion_limit": 12})
        except Exception as e:
            self.logger.error(f"Agent 出错，用原来的 RAG 回答：{e}")
            self.last_run = {"fallback": "error"}
            if loan_request:
                return {"answer": LOAN_UNAVAILABLE[lang], "loan_calculation_pending": True}
            return self.fallback.process(state)

        # 只看这一轮新加的消息（前面的是历史）
        new_messages = result["messages"][len(messages):]
        ai_messages = [m for m in new_messages if isinstance(m, AIMessage)]
        tool_results = [m for m in new_messages if isinstance(m, ToolMessage)]
        answer = str(ai_messages[-1].content).strip() if ai_messages else ""
        tool_calls = [{"tool": c["name"], "args": c["args"]} for m in ai_messages for c in m.tool_calls]

        if loan_request:
            self.last_run = {"tool_calls": tool_calls}
            for message in reversed(tool_results):
                if message.name == recommend_catalog_loan.name and message.status == "success":
                    calculation = json.loads(message.content)
                    if "status" in calculation or calculation["loan_type"] in loan_types:
                        return self._finish_loan(calculation, tool_calls, lang)
                if message.name == calculate_loan_plans.name and message.status == "success":
                    calculation = json.loads(message.content)
                    if {Decimal(row["annual_rate_percent"]) for row in calculation["plans"]} <= rates:
                        return self._finish_loan(calculation, tool_calls, lang)
            return {"answer": answer if answer and not tool_calls else LOAN_DETAILS[lang],
                    "tool_calls": tool_calls, "loan_calculation_pending": True}

        docs = []
        seen = set()
        for m in tool_results:
            if m.name not in (search_faq.name, get_product_info.name):
                continue
            for artifact in m.artifact or []:
                text = artifact.get(f"text_{lang}") or artifact["text"]
                key = (artifact["doc"], artifact["section"], text)
                if key not in seen:
                    seen.add(key)
                    docs.append({**artifact, "text": text})
        if not docs:
            self.last_run = {"tool_calls": tool_calls, "fallback": "sources"}
            return {**self.fallback.process(state), "tool_calls": tool_calls}

        generated = generate_grounded_response(question, history_of(state), docs, lang)
        if not generated["answer"]:
            self.last_run = {"tool_calls": tool_calls, "fallback": generated["status"]}
            return {"answer": NOT_FOUND[lang], "sources": [], "tool_calls": tool_calls}
        sources = []
        for index in generated["used_sources"]:
            doc = docs[index - 1]
            sources.append({key: doc[key] for key in ("doc", "title", "section", "rerank_score")})
        self.last_run = {"tool_calls": tool_calls, "sources": sources}
        return {"answer": generated["answer"], "sources": sources, "tool_calls": tool_calls}

    def _finish_loan(self, calculation, calls, lang, changed_field=None):
        if "status" in calculation:
            missing = calculation["status"] == "no_catalog_plans"
            unavailable = ("产品目录暂未收录该类贷款的试算方案。" if lang == "zh"
                           else "The catalog has no estimate plans for this loan type.")
            return {"answer": unavailable if missing else LOAN_UNAVAILABLE[lang],
                    "tool_calls": calls, "loan_calculation_pending": not missing,
                    "loan_calculation_context": {}}

        args = {key: calculation[key] for key in ("principal", "monthly_budget")}
        sources = []
        if "catalog_product" in calculation:
            tool = recommend_catalog_loan
            args.update(loan_type=calculation["loan_type"], repayment=calculation["plans"][0]["repayment"])
            sources.append({**GRAPH_SOURCE, "title": "贷款方案 / Loan plans",
                            "section": calculation["catalog_product"][lang]})
        else:
            tool = calculate_loan_plans
            args["plans"] = [{key: row[key] for key in LoanPlan.model_fields} for row in calculation["plans"]]
        answer = self._loan_answer(calculation, lang)
        facts = answer
        if changed_field == "principal":
            principal = calculation["principal"]
            label = "本金" if lang == "zh" else "Principal"
            facts = f"{label}: SGD {principal}\n{facts}"
            prefix = (f"把本金改为 SGD {principal}，其他条件沿用上一轮。"
                      if lang == "zh" else f"Using a principal of SGD {principal} and keeping the previous terms.")
            answer = f"{prefix}\n\n{answer}"
        organized = organize_loan_answer(facts, lang, changed_field)
        self.last_run["loan_reply_generator"] = "deepseek" if organized else "calculator"
        self.log_step(self.name, f"贷款回复组织：{self.last_run['loan_reply_generator']}")
        if organized:
            answer = organized
        return {"answer": answer, "sources": sources, "tool_calls": calls, "loan_calculation_pending": False,
                "loan_calculation_context": {"tool": tool.name, "args": args}}

    @staticmethod
    def _loan_answer(calculation, lang):
        plans = calculation["plans"]
        from_catalog = "catalog_product" in calculation
        if len(plans) == 1:
            return BankAgentNode._single_loan_answer(plans[0], calculation["monthly_budget"], from_catalog, lang)

        plan_names = [plan["name"] for plan in plans]
        if from_catalog:
            suffix = " 期" if lang == "zh" else "-month plan"
            plan_names = [f"{plan['term_months']}{suffix}" for plan in plans]
        selected = calculation["recommended_plan_index"]
        if selected is None:
            if calculation["recommendation_reason"] == "unknown_upfront_fees":
                intro = ("有方案的手续费待确认，可以先比较月供。" if lang == "zh"
                         else "Some upfront fees are unknown. You can compare the monthly payments below.")
            else:
                intro = ("所有方案的最高月供都超过预算。" if lang == "zh"
                         else "Every plan exceeds the monthly budget.")
        else:
            choice = plan_names[selected]
            has_budget = calculation["monthly_budget"] is not None
            if lang == "zh":
                reason = "月供预算内总成本最低" if has_budget else "总成本最低"
                intro = f"按你给出的条件，建议选「{choice}」，{reason}。"
            else:
                reason = "lowest total cost within budget" if has_budget else "lowest total cost"
                intro = f"{choice} has the {reason} based on your inputs."
        lines = [intro, ""]
        for name, plan in zip(plan_names, plans):
            months, rate = plan["term_months"], plan["annual_rate_percent"]
            first, last = plan["first_month_payment"], plan["last_month_payment"]
            payment = f"SGD {first}" if first == last else f"SGD {first} → SGD {last}"
            cost, fee = plan["total_cost"], plan["upfront_fee"]
            if lang == "zh":
                term = "" if from_catalog else f"{months} 期，"
                over = "，超出月供预算" if plan["within_budget"] is False else ""
                details = "手续费待确认" if fee is None else f"手续费 SGD {fee}"
                total = "" if cost is None else f"，总成本 SGD {cost}"
                line = f"- {name}（{term}年利率 {rate}%，{details}）：月供 {payment}{total}{over}。"
            else:
                term = "" if from_catalog else f"{months} months, "
                over = ", over budget" if plan["within_budget"] is False else ""
                details = "upfront fee unknown" if fee is None else f"SGD {fee} upfront fee"
                total = "" if cost is None else f", total cost SGD {cost}"
                line = f"- {name} ({term}{rate}% annual rate, {details}): monthly payment {payment}{total}{over}."
            lines.append(line)
        lines.extend(("", "总成本含利息和一次性手续费，结果按输入条件估算。" if lang == "zh"
                      else "Total cost includes interest and upfront fees. Figures are estimates based on your inputs."))
        if from_catalog:
            lines.append("利率和费用为目录试算参数，贷款条件以实际审批和合同为准。" if lang == "zh"
                         else "Catalog rates and fees are for estimates. Loan terms are subject to approval and contract.")
        return "\n".join(lines)

    @staticmethod
    def _single_loan_answer(plan, budget, from_catalog, lang):
        first, last = plan["first_month_payment"], plan["last_month_payment"]
        equal_principal = plan["repayment"] == "equal_principal"
        cost = plan["total_cost"]
        if lang == "zh":
            payment = (f"按等额本金估算，首期月供约 SGD {first}，末期约 SGD {last}。"
                       if equal_principal else f"按等额本息估算，每月约还 SGD {first}。")
            fees = ("一次性手续费待确认，未计入月供。" if cost is None
                    else f"总成本 SGD {cost}（含利息和一次性手续费）。")
        else:
            payment = (f"Estimated equal-principal payments start at SGD {first} and end at SGD {last}."
                       if equal_principal else f"Estimated fixed monthly payment: SGD {first}.")
            fees = ("The upfront fee is unknown and is excluded from the monthly payment." if cost is None
                    else f"Total cost, including interest and upfront fees: SGD {cost}.")
        parts = [payment]
        if plan["within_budget"] is False:
            overage = Decimal(plan["peak_monthly_payment"]) - Decimal(budget)
            parts.append(f"最高月供比预算高 SGD {overage}。" if lang == "zh"
                         else f"The highest payment exceeds your monthly budget by SGD {overage}.")
        parts.append(fees)
        if from_catalog:
            parts.append("利率和费用为目录试算参数。" if lang == "zh" else "Rates and fees are catalog estimates.")
        return " ".join(parts)

if __name__ == "__main__":
    import logging

    from langchain_core.messages import HumanMessage

    from src.schema.base import setup_logging
    from src.schema.state import create_default_state

    setup_logging(logging.WARNING)
    node = BankAgentNode()
    tests = [
        ("电汇手续费多少？", "zh"),                           # 应该用 get_product_info
        ("Is PayNow free? How long does a TT take?", "en"),  # 两件事，可能调两次工具
        ("我的 PIN 被 lock 了怎么办", "zh"),                  # 应该用 search_faq
        ("Can I open an account if I'm 15?", "en"),          # search_faq
        ("定期存款利率是多少？", "zh"),                        # 知识库里没有，不能编
    ]
    for q, lang in tests:
        state = create_default_state(masked_question=q, lang=lang, messages=[HumanMessage(q)])
        start = time.perf_counter()
        agent = node.process(state)
        t_agent = time.perf_counter() - start
        start = time.perf_counter()
        rag = node.fallback.process(state)
        t_rag = time.perf_counter() - start
        print(f"\n===== {q}")
        calls = [(c["tool"], c["args"]) for c in node.last_run.get("tool_calls", [])]
        print(f"[Agent {t_agent:.1f}s] 工具：{calls}  {node.last_run.get('fallback', '')}")
        print(agent["answer"])
        print(f"[原来的 RAG {t_rag:.1f}s]")
        print(rag["answer"])
