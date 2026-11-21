"""Compare fixed-rate SGD loan scenarios without looking up or guessing quotes."""

import re
from copy import deepcopy
from decimal import Decimal, ROUND_HALF_UP, localcontext
from typing import Annotated, Literal

from langchain.tools import tool
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from src.intents import mentions_other_bank
from src.schema.exceptions import Neo4jError
from src.utils.neo4j_utils import run_cypher

Amount = Annotated[Decimal, Field(ge=0, max_digits=14, decimal_places=2)]
LoanType = Literal["home_loan", "car_loan", "personal_loan"]
Repayment = Literal["equal_payment", "equal_principal"]
LOAN_CALCULATION_INTENT = "loan_plan_calculation"
_LOAN = re.compile(r"贷款|房贷|车贷|消费贷|按揭|\b(?:loans?|mortgages?|car financing)\b", re.I)
_CALCULATE = re.compile(
    r"月供|每月.{0,5}还|总利息|总成本|试算|测算|算一下|帮我算|怎么选|比较|对比|哪个.{0,5}划算|推荐.{0,8}方案|"
    r"\b(?:calculate|compare|estimate|monthly (?:payment|repayment|budget)|total (?:cost|interest))\b", re.I,
)
_RATE = re.compile(r"(?<![\d.])(-?\d+(?:\.\d+)?)\s*(?:%|％|percent\b)", re.I)
_PLAN_DETAILS = re.compile(
    r"^[AB甲乙一二三方案\s:：]*\d|本金|年利率|利率|期数|月供|手续费|免息|无费用|预算|"
    r"\b(?:principal|annual rate|months|years|no fees|upfront fee|budget)\b", re.I,
)
_OTHER_TASK = re.compile(
    r"申请.{0,5}(?:进度|状态)|审批|查余额|冻结|挂失|转账|汇款|盗刷|投诉|"
    r"\b(?:application status|balance|freeze|transfer|fraud|complaint)\b", re.I,
)
_TYPE_PATTERNS = {
    "home_loan": re.compile(r"房贷|按揭|住房贷款|买房|购房|\b(?:home loans?|mortgages?)\b", re.I),
    "car_loan": re.compile(r"车贷|汽车贷款|买车|购车|\b(?:car loans?|auto loans?|car financing)\b", re.I),
    "personal_loan": re.compile(r"个人贷款|个人贷|消费贷|\bpersonal loans?\b", re.I),
}
_SGD_AMOUNT = r"(?:SGD\s*|S\$\s*)?(\d+(?:,\d{3})*(?:\.\d{1,2})?)(?!\d|[,.]\d|[万千百])"
_PRINCIPAL = re.compile(
    rf"(?:本金|贷款金额|房贷|车贷|贷款|借款|借|principal|loans?\s+of)\s*(?:为|是|[：:])?\s*{_SGD_AMOUNT}", re.I,
)
_UPFRONT_FEE = re.compile(
    rf"(?:一次性手续费|手续费|upfront fees?)\s*(?:为|是|[：:])?\s*{_SGD_AMOUNT}", re.I,
)
_NO_FEE = re.compile(r"免(?:一次性)?手续费|(?:没有|无)(?:一次性)?手续费|\bno upfront fees?\b", re.I)
_BUDGET = re.compile(rf"(?:月供预算|月供上限|每月最多还|monthly budget)\s*(?:为|是|[：:])?\s*{_SGD_AMOUNT}", re.I)
_BUDGET_MENTION = re.compile(r"预算|上限|at most|budget|最多还", re.I)
_TERM = re.compile(r"(?<![\d.\-])(\d{1,3})\s*(期|个月|年(?!利率)|months?\b|years?\b)", re.I)


def is_loan_calculation_request(question: str, awaiting_details: bool = False) -> bool:
    if _OTHER_TASK.search(question) or mentions_other_bank(question):
        return False
    return bool(
        (_LOAN.search(question) and _CALCULATE.search(question))
        or (awaiting_details and (_PLAN_DETAILS.search(question) or bare_amount_followup(question)
                                  or loan_types_mentioned(question)))
    )


def quoted_loan_rates(question: str) -> set[Decimal]:
    """Extract explicitly supplied percentages for checking Agent tool inputs."""
    normalized = question.translate(str.maketrans("０１２３４５６７８９．", "0123456789."))
    rates = {Decimal(rate) for rate in _RATE.findall(normalized)}
    if re.search(r"免息|零利率|\b(?:interest[- ]free|zero interest)\b", normalized, re.I):
        rates.add(Decimal(0))
    return rates


def loan_types_mentioned(question: str) -> set:
    return {loan_type for loan_type, pattern in _TYPE_PATTERNS.items() if pattern.search(question)}


class LoanPlan(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    name: str = Field(min_length=1, max_length=80)
    loan_type: LoanType
    annual_rate_percent: Decimal = Field(ge=0, le=100, decimal_places=6,
                                         description="Fixed nominal annual rate on the outstanding balance, as a percentage.")
    term_months: int = Field(ge=1, le=600)
    upfront_fee: Amount | None = Field(
        description="One-time fees paid upfront in SGD. Use null if undecided or unknown, and 0 only when explicitly fee-free."
    )
    repayment: Repayment = "equal_payment"


class LoanComparison(BaseModel):
    model_config = ConfigDict(extra="forbid")

    principal: Amount = Field(gt=0, description="Same principal in SGD for every option.")
    plans: list[LoanPlan] = Field(min_length=1, max_length=20)
    monthly_budget: Amount | None = Field(default=None, description="Maximum monthly repayment in SGD.")


def explicit_single_loan(question: str, loan_type: LoanType) -> LoanComparison | None:
    """Parse one loan with enough information to calculate its monthly payment."""
    question = question.translate(str.maketrans("０１２３４５６７８９．，", "0123456789.,"))
    principals = _PRINCIPAL.findall(question)
    fees = _UPFRONT_FEE.findall(question)
    if fees and _NO_FEE.search(question):
        return None
    if not fees and _NO_FEE.search(question):
        fees = ["0"]
    budgets = _BUDGET.findall(question)
    terms = _TERM.findall(question)
    quoted_rates = _RATE.findall(question)
    if len(fees) > 1 or not all(len(values) == 1 for values in (principals, terms, quoted_rates)):
        return None
    if len(budgets) > 1 or (_BUDGET_MENTION.search(question) and not budgets):
        return None
    term, unit = terms[0]
    months = int(term) * (12 if unit.lower() in ("年", "year", "years") else 1)
    equal_principal = re.search(r"等额本金|\bequal principal\b", question, re.I)
    try:
        return LoanComparison(
            principal=Decimal(principals[0].replace(",", "")),
            monthly_budget=Decimal(budgets[0].replace(",", "")) if budgets else None,
            plans=[LoanPlan(
                name="贷款方案", loan_type=loan_type, annual_rate_percent=quoted_rates[0],
                term_months=months, upfront_fee=fees[0].replace(",", "") if fees else None,
                repayment="equal_principal" if equal_principal else "equal_payment",
            )],
        )
    except ValidationError:
        return None


def _money(amount: Decimal) -> Decimal:
    return amount.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def compare_loan_plans(comparison: LoanComparison) -> dict:
    """Estimate payments and identify the lowest-cost plan within the monthly budget."""
    principal = comparison.principal
    budget = comparison.monthly_budget
    rows = []
    with localcontext() as ctx:
        ctx.prec = 40
        for plan in comparison.plans:
            months = plan.term_months
            rate = plan.annual_rate_percent / 1200
            if plan.repayment == "equal_principal":
                monthly_principal = principal / months
                first_payment = monthly_principal + principal * rate
                last_payment = monthly_principal * (1 + rate)
                interest = principal * rate * (months + 1) / 2
            else:
                payment = principal / months
                if rate:
                    payment = principal * rate / (1 - (1 + rate) ** -months)
                first_payment = last_payment = payment
                interest = max(Decimal(0), payment * months - principal)
            peak = _money(first_payment)
            cost = None if plan.upfront_fee is None else interest + plan.upfront_fee
            rows.append({
                **plan.model_dump(mode="json"),
                "first_month_payment": str(peak),
                "last_month_payment": str(_money(last_payment)),
                "peak_monthly_payment": str(peak),
                "total_interest": str(_money(interest)),
                "total_cost": None if cost is None else str(_money(cost)),
                "total_repayment": None if cost is None else str(_money(principal + cost)),
                "within_budget": None if budget is None else peak <= budget,
            })

    eligible = [index for index, row in enumerate(rows) if row["within_budget"] is not False]
    recommended = None
    if not eligible:
        reason = "no_plan_within_budget"
    elif len(rows) == 1:
        reason = "single_plan_estimate"
    elif any(rows[index]["total_cost"] is None for index in eligible):
        reason = "unknown_upfront_fees"
    else:
        recommended = min(eligible, key=lambda index: (
            Decimal(rows[index]["total_cost"]), Decimal(rows[index]["peak_monthly_payment"]),
        ))
        reason = "lowest_total_cost_within_budget" if budget is not None else "lowest_total_cost"
    return {
        "currency": "SGD",
        "principal": str(_money(principal)),
        "monthly_budget": None if budget is None else str(_money(budget)),
        "plans": rows,
        "recommended_plan_index": recommended,
        "recommendation_reason": reason,
    }


@tool(args_schema=LoanComparison, return_direct=True)
def calculate_loan_plans(principal: Decimal, plans: list[LoanPlan], monthly_budget: Decimal | None = None) -> dict:
    """Compare user-entered fixed-rate home, car or personal loan scenarios in SGD.

    Require a quoted nominal annual rate and month count. Use null for unknown upfront fees.
    The recommended_plan_index is zero-based and is null when there is no comparable recommendation.
    """
    return compare_loan_plans(LoanComparison(principal=principal, plans=plans, monthly_budget=monthly_budget))


class CatalogLoanRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    loan_type: LoanType
    principal: Amount = Field(gt=0, description="Amount the customer wants to borrow, in SGD.")
    monthly_budget: Amount | None = Field(default=None, description="Customer's monthly repayment cap, in SGD.")
    repayment: Repayment = "equal_payment"


@tool(args_schema=CatalogLoanRequest, return_direct=True)
def recommend_catalog_loan(loan_type: LoanType, principal: Decimal, monthly_budget: Decimal | None = None,
                          repayment: Repayment = "equal_payment") -> dict:
    """Look up bank catalog loan plans, calculate them and recommend the lowest-cost option within budget.

    Use this by default for loan estimates. The customer only needs to give a loan type and principal;
    the monthly budget is optional. The catalog supplies terms, rates and fees.
    """
    try:
        rows = run_cypher("""
            MATCH (p:BankProduct {product_id: $loan_type})-[:OFFERS_LOAN_PLAN]->(q:BankLoanPlan)
            WHERE q.plan_id IN p.loan_plan_ids
            RETURN p.name_zh AS name_zh, p.name_en AS name_en,
                   q.plan_id AS plan_id, q.term_months AS term_months,
                   q.annual_rate_percent AS annual_rate_percent, q.upfront_fee AS upfront_fee
            ORDER BY q.term_months, q.plan_id
            """, {"loan_type": loan_type})
    except Neo4jError:
        return {"status": "catalog_unavailable"}
    if not rows:
        return {"status": "no_catalog_plans"}
    plans = [LoanPlan(name=row["plan_id"], loan_type=loan_type, repayment=repayment,
                      term_months=row["term_months"], annual_rate_percent=row["annual_rate_percent"],
                      upfront_fee=row["upfront_fee"]) for row in rows]
    comparison = compare_loan_plans(LoanComparison(principal=principal, plans=plans, monthly_budget=monthly_budget))
    return {**comparison, "loan_type": loan_type,
            "catalog_product": {"zh": rows[0]["name_zh"], "en": rows[0]["name_en"]}}


_FOLLOWUP_START = r"(?:(?:那(?:如果)?|如果|what if)\s*)?"
_FOLLOWUP_CHANGE = r"(?:(?:是|改为|改成|调到|换成|换为|to|is|were|=|:|：)\s*)?"
_FOLLOWUP_END = r"\s*(?:呢|吗|的话)?[?？。.!！]*"
_IMPLICIT_AMOUNT_FOLLOWUP = re.compile(
    rf"(?:那(?:如果)?|如果|改成|换成|what if)\s*{_FOLLOWUP_CHANGE}{_SGD_AMOUNT}{_FOLLOWUP_END}", re.I,
)
_BARE_AMOUNT_FOLLOWUP = re.compile(rf"{_SGD_AMOUNT}{_FOLLOWUP_END}", re.I)
_FOLLOWUP_NUMBER = (
    ("monthly_budget", rf"(?:月供预算|月供上限|预算|monthly budget|budget)\s*{_FOLLOWUP_CHANGE}{_SGD_AMOUNT}"),
    ("principal", rf"(?:贷款金额|本金|借款|借|principal)\s*{_FOLLOWUP_CHANGE}{_SGD_AMOUNT}"),
    ("upfront_fee", rf"(?:一次性手续费|手续费|upfront fees?)\s*{_FOLLOWUP_CHANGE}{_SGD_AMOUNT}"),
    ("annual_rate_percent", rf"(?:固定年利率|年利率|利率|annual rate|rate)\s*{_FOLLOWUP_CHANGE}(\d+(?:\.\d+)?)\s*(?:%|％|percent)"),
)


def implicit_amount_followup(question: str) -> re.Match | None:
    normalized = question.strip().translate(str.maketrans("０１２３４５６７８９．，", "0123456789.,"))
    return _IMPLICIT_AMOUNT_FOLLOWUP.fullmatch(normalized)


def bare_amount_followup(question: str) -> re.Match | None:
    normalized = question.strip().translate(str.maketrans("０１２３４５６７８９．，", "0123456789.,"))
    return _BARE_AMOUNT_FOLLOWUP.fullmatch(normalized)


def loan_followup_arguments(question: str, context: dict) -> tuple[dict, str] | None:
    """Apply a short, single-field change to the latest successful loan estimate."""
    tool_name = context.get("tool")
    if tool_name not in (calculate_loan_plans.name, recommend_catalog_loan.name):
        return None
    question = question.strip().translate(str.maketrans("０１２３４５６７８９．，", "0123456789.,"))
    args = deepcopy(context["args"])
    catalog = tool_name == recommend_catalog_loan.name
    single = not catalog and len(args["plans"]) == 1
    for field, pattern in _FOLLOWUP_NUMBER:
        match = re.fullmatch(_FOLLOWUP_START + pattern + _FOLLOWUP_END, question, re.I)
        if match:
            value = match[1].replace(",", "")
            break
    else:
        amount = implicit_amount_followup(question) or bare_amount_followup(question)
        term = re.fullmatch(
            rf"{_FOLLOWUP_START}(?:期限\s*)?{_FOLLOWUP_CHANGE}(\d{{1,3}})\s*(期|个月|年|months?|years?){_FOLLOWUP_END}",
            question, re.I,
        )
        repayment = re.fullmatch(
            rf"{_FOLLOWUP_START}{_FOLLOWUP_CHANGE}(等额本金|等额本息|equal principal|equal payment){_FOLLOWUP_END}",
            question, re.I,
        )
        if amount:
            field, value = "principal", amount[1].replace(",", "")
        elif term:
            field = "term_months"
            value = int(term[1]) * (12 if term[2].lower() in ("年", "year", "years") else 1)
        elif repayment:
            field = "repayment"
            value = "equal_principal" if repayment[1].lower() in ("等额本金", "equal principal") else "equal_payment"
        else:
            return None
    if field in ("principal", "monthly_budget") or (catalog and field == "repayment"):
        args[field] = value
    elif single:
        args["plans"][0][field] = value
    else:
        return None
    schema = CatalogLoanRequest if catalog else LoanComparison
    try:
        return schema.model_validate(args).model_dump(mode="json"), field
    except ValidationError:
        return None
