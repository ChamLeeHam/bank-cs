"""Public descriptions and product comparisons for the demo bank."""

import re
from functools import cache

import yaml

from src.intents import mentions_other_bank
from src.schema.config import ROOT_DIR
from src.utils.neo4j_utils import run_cypher


@cache
def load_financial_catalog():
    return yaml.safe_load((ROOT_DIR / "data" / "financial_products.yaml").read_text(encoding="utf-8"))


_INFO = re.compile(
    r"介绍|了解|说明|是什么|有什么|有哪些|区别|对比|比较|利率|利息|条款|费用|期限|条件|"
    r"怎么申请|如何申请|提前支取|提前还款|起存|首付|支持|提供|年费|手续费|费率|返现|币种|差别|怎么比|"
    r"\b(?:about|what|which|compare|difference|details|offer|rates?|interest|fees?|terms?|"
    r"eligibility|requirements?|how (?:do|can) i apply|how long|minimum|early withdrawal|maturity)\b",
    re.I,
)
_LIST = re.compile(
    r"哪些|有哪些|有什么|什么.{0,8}(?:产品|选项|贷款|存款)|产品目录|产品列表|支持.{0,6}产品|有吗|"
    r"\b(?:what (?:kinds? of |types? of )?(?:loans?|deposits?|products)|"
    r"which (?:loans?|deposits?|products|accounts?)|list|options|products|do you offer)\b", re.I,
)
_PERSONAL = re.compile(
    r"(?:帮我|给我|替我|直接).{0,10}(?:申请|办理|开通|查询|查一下|提前还款|支取)|"
    r"(?:我的|本人|我那笔).{0,12}(?:申请|贷款|定存|存款).{0,14}(?:进度|余额|批了|到期日|合同|利率|放款)|"
    r"(?:能贷|能借|批给我)多少|适合我|推荐|投资建议|基金|股票|"
    r"\b(?:apply for|open|withdraw|check|submit)\b.{0,30}\b(?:for me|on my behalf|my application)\b|"
    r"\bmy\b.{0,25}\b(?:loan|mortgage|deposit|application)\b.{0,20}\b(?:progress|balance|status|approved|maturity date|rate)\b|"
    r"\b(?:should i choose|recommend|investments?|mutual funds?|stocks?)\b",
    re.I,
)
_DEPOSITS = re.compile(r"存款|储蓄产品|\b(?:deposit|savings) products?\b|\bdeposits\b", re.I)
_LOANS = re.compile(r"贷款|融资|\b(?:loans?|lending|financing)\b", re.I)
_CARDS = re.compile(
    r"(?:哪些|什么)(?:种|类型的?)?(?:银行卡|信用卡|借记卡|卡)|(?:银行卡|信用卡|借记卡)(?:产品|种类|有哪些)|"
    r"\b(?:what|which|types of) (?:(?:kinds|types) of )?(?:(?:credit|debit|bank) )?cards\b",
    re.I,
)
_ACCOUNTS = re.compile(r"账户产品|账户种类|(?:哪些|什么)账户|\b(?:what|which|types of) accounts\b", re.I)
_TRANSFERS = re.compile(r"转账产品|汇款产品|\b(?:transfer|remittance) products\b", re.I)
_ALL_PRODUCTS = re.compile(r"金融产品|零售银行产品|(?:哪些|什么)产品[？?。！!]*$|\b(?:financial|retail banking) products\b|\bwhat products do you offer\b", re.I)
_UNSUPPORTED = re.compile(
    r"学生贷款|助学贷款|企业贷款|商业贷款|企业账户|公司账户|"
    r"\b(?:hdb|(?:student|business|corporate|commercial) loans?|(?:business|corporate) accounts?)\b",
    re.I,
)
_USE_CASES = {
    "term_saving": re.compile(r"计划存钱|想存.{0,8}钱|攒钱|\b(?:saving for a goal|plan to save)\b", re.I),
    "home_purchase": re.compile(r"买房|购房|\b(?:buying|buy|purchase) (?:a |my )?(?:home|property)\b", re.I),
    "vehicle_purchase": re.compile(r"买车|购车|\b(?:buying|buy|purchase) (?:a |my )?(?:car|vehicle)\b", re.I),
    "personal_expenses": re.compile(r"个人支出|个人消费|\bpersonal expenses\b", re.I),
    "emergency_saving": re.compile(
        r"应急(?:钱|资金|储蓄)|备用金|随时(?:取|支取)|用钱时.{0,6}(?:取|支取)|"
        r"\b(?:emergency (?:savings|funds?)|instant access|withdraw (?:money )?(?:at any time|anytime))\b", re.I,
    ),
    "foreign_currency_saving": re.compile(r"外币储蓄|存外币|外币存款|\bforeign currency (?:savings|deposits)\b", re.I),
    "daily_spend": re.compile(r"日常消费|\beveryday spending\b", re.I),
    "travel_spend": re.compile(r"(?:境外|出国|海外|旅行)消费|\b(?:travel|overseas) spending\b", re.I),
    "local_transfer": re.compile(r"本地转账|\blocal (?:SGD )?transfers?\b", re.I),
    "recurring_bills": re.compile(r"账单代扣|\brecurring bills\b", re.I),
    "international_transfer": re.compile(r"跨境汇款|\binternational transfers?\b", re.I),
}


@cache
def _product_patterns():
    return {
        product["id"]: re.compile(
            "|".join((rf"\b{re.escape(alias)}s?\b" if alias.isascii() else re.escape(alias))
                     for alias in sorted(product["aliases"], key=len, reverse=True)),
            re.I,
        )
        for product in load_financial_catalog()["products"]
    }


def _named_products(question):
    patterns = _product_patterns()
    matches = [(match.start(), match.end(), key) for key, pattern in patterns.items()
               for match in pattern.finditer(question)]
    selected = set()
    used_positions = set()
    for start, end, key in sorted(matches, key=lambda match: match[0] - match[1]):
        positions = range(start, end)
        if used_positions.isdisjoint(positions):
            used_positions.update(positions)
            selected.add(key)
    return [key for key in patterns if key in selected]


def financial_product_query(question):
    """Return a scoped catalog filter for public product questions."""
    if _PERSONAL.search(question) or _UNSUPPORTED.search(question) or mentions_other_bank(question):
        return None
    products = _named_products(question)
    catalog_products = {p["id"] for p in load_financial_catalog()["products"] if not p.get("existing")}
    if catalog_products.intersection(products) and (_INFO.search(question) or any(
        pattern.fullmatch(question.strip().rstrip("?？。")) for pattern in _product_patterns().values()
    )):
        return {"product_ids": products}
    if not _LIST.search(question):
        return None
    categories = [key for key, pattern in (
        ("deposits", _DEPOSITS), ("retail_loans", _LOANS), ("card", _CARDS),
        ("account", _ACCOUNTS), ("transfer", _TRANSFERS),
    ) if pattern.search(question)]
    all_products = bool(_ALL_PRODUCTS.search(question))
    uses_by_category = {
        "term_saving": ("deposits",), "emergency_saving": ("account", "deposits"),
        "foreign_currency_saving": ("account", "deposits"), "home_purchase": ("retail_loans",),
        "vehicle_purchase": ("retail_loans",), "personal_expenses": ("retail_loans",),
        "daily_spend": ("card",), "travel_spend": ("card",),
        "local_transfer": ("transfer",), "recurring_bills": ("transfer",),
        "international_transfer": ("transfer",),
    }
    uses = [key for key, pattern in _USE_CASES.items()
            if pattern.search(question) and (all_products or set(categories).intersection(uses_by_category[key])
                                             or key in ("term_saving", "emergency_saving"))]
    if len(uses) == 1:
        return {"use_case": uses[0]}
    if uses:
        return {"use_cases": uses}
    if all_products or len(categories) > 1:
        return {}
    if categories == ["card"]:
        credit = re.search(r"信用卡|\bcredit cards?\b", question, re.I)
        debit = re.search(r"借记卡|\bdebit cards?\b", question, re.I)
        if credit and not debit:
            return {"category": "card", "use_cases": ["daily_spend", "travel_spend"]}
        if debit and not credit:
            return {"category": "card", "use_case": "debit_payments"}
    if categories:
        return {"category": categories[0]}
    return None


def read_financial_products(filters):
    return run_cypher("""
        MATCH (p:BankProduct)-[:Belong]->(c:BankCategory)
        MATCH (p)-[:SUITABLE_FOR]->(u:BankUseCase)
        MATCH (p)-[:Have]->(o:BankAttr {attr_key: 'overview'})
        MATCH (p)-[:Have]->(t:BankAttr {attr_key: 'key_terms'})
        WHERE p.catalog = 'retail_demo'
          AND ($product_ids IS NULL OR p.product_id IN $product_ids)
          AND ($category IS NULL OR c.category_id = $category)
          AND ($use_case IS NULL OR u.use_case_id = $use_case)
          AND ($use_cases IS NULL OR u.use_case_id IN $use_cases)
        OPTIONAL MATCH (p)-[:Have]->(annual:BankAttr {attr_key: 'annual_fee'})
        OPTIONAL MATCH (p)-[:Have]->(fx:BankAttr {attr_key: 'fx_fee'})
        OPTIONAL MATCH (p)-[:Have]->(withdrawal:BankAttr {attr_key: 'on_demand_withdrawal'})
        RETURN p.name_zh AS name_zh, p.name_en AS name_en,
               c.name_zh AS category_zh, c.name_en AS category_en,
               u.name_zh AS use_zh, u.name_en AS use_en,
               o.value_zh AS overview_zh, o.value_en AS overview_en,
               t.value_zh AS terms_zh, t.value_en AS terms_en,
               annual.value_zh AS annual_zh, annual.value_en AS annual_en,
               fx.name_zh AS fx_name_zh, fx.name_en AS fx_name_en,
               fx.value_zh AS fx_zh, fx.value_en AS fx_en,
               withdrawal.value_zh AS withdrawal_zh, withdrawal.value_en AS withdrawal_en
        ORDER BY c.category_id, u.use_case_id, p.name_zh
        """, {"product_ids": None, "category": None, "use_case": None, "use_cases": None, **filters})
