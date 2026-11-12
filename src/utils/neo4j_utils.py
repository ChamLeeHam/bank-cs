import re
from pathlib import Path
import logging
logger = logging.getLogger(__name__)
import sys

from neo4j import GraphDatabase

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from src.schema.config import get_config
from src.schema.exceptions import Neo4jError

neo4j_driver = None
alias_cache = None  # 别名 -> 产品，第一次用的时候从图谱里读出来


def get_neo4j_driver():
    global neo4j_driver
    if neo4j_driver is not None:
        return neo4j_driver

    config = get_config()

    try:
        driver = GraphDatabase.driver(
            uri=config.neo4j_uri,
            auth=(config.neo4j_user, config.neo4j_password)
        )
        driver.verify_connectivity()
        neo4j_driver = driver
        return neo4j_driver
    except Exception as e:
        logger.error(f"neo4j客户端创建失败：{e}")
        raise Neo4jError(f"无法连接 Neo4j：{config.neo4j_uri}", "neo4j_utils", e)


def run_cypher(cypher, parameters=None):
    """执行 Cypher，返回 records。查询出错（比如 Neo4j 中途停了）也统一变成 Neo4jError，调用的地方只要接这一种"""
    try:
        records, _, _ = get_neo4j_driver().execute_query(
            cypher,
            parameters_=parameters or {},
            database_=get_config().neo4j_database
        )
        return records
    except Neo4jError:
        raise
    except Exception as e:
        logger.error(f"Cypher 查询失败：{e}")
        raise Neo4jError("Neo4j 查询失败", "neo4j_utils", e)


def load_alias_cache():
    global alias_cache
    # 第一步: 已经读过了就直接返回
    if alias_cache is not None:
        return alias_cache
    # 第二步：执行 Neo4j Cypher 查询
    cypher = """
        MATCH (al: BankAlias) -[:Refers]->(p:BankProduct)
        RETURN al.name AS alias, p.product_id AS product_id, p.name_zh AS name_zh, p.name_en AS name_en
    """
    # 第三步：执行查询并获取 records
    records = run_cypher(cypher)
    # 第四步：将 Neo4j 数据转换为字典
    cache = {}
    for record in records:
        cache[record["alias"]] = {
            "product_id": record["product_id"],
            "name_zh": record["name_zh"],
            "name_en": record["name_en"]
        }
    alias_cache = cache  # 全部转换完了才存到全局变量
    """
    {
    "电汇": {
        "product_id": "overseas_tt",
        "name_zh": "海外电汇",
        "name_en": "Overseas TT"
        },
    "TT": {
        "product_id": "overseas_tt",
        "name_zh": "海外电汇",
        "name_en": "Overseas TT"
        }
    }
    """
    return alias_cache

def find_products(text):
    aliases = load_alias_cache()
    text = text.lower()                  # 别名存的是小写，问题也转小写
    used = [False] * len(text)           # 每个字一个标记：有没有被匹配过
    found = []
    # 长的别名先匹配，"多币种账户" 比 "多币种" 先
    for alias in sorted(aliases, key=len, reverse=True):
        if alias.isascii():
            # 英文要整个词，"tt" 不能匹配 "settle"
            pattern = rf"(?<![a-z]){re.escape(alias)}(?![a-z])"
        else:
            pattern = re.escape(alias)
        for m in re.finditer(pattern, text):
            if any(used[m.start():m.end()]):   # 这几个字已经被更长的别名用了
                continue
            used[m.start():m.end()] = [True] * (m.end() - m.start())
            product = aliases[alias]
            if product not in found:           # 同一个产品只放一次
                found.append(product)
    return found

# 问的是哪个属性。顺序有关系：具体的放前面
# "年费" 要先于 "手续费"，"外币交易费" 要先于 "手续费"，不然都会被当成 fee
ATTR_KEYWORDS = {
    "annual_fee": ["年费", "annual fee"],
    "fx_fee": ["外币", "海外刷卡", "foreign transaction", "foreign currency", "兑换"],
    "late_fee": ["逾期", "late payment", "late fee"],
    "replacement_fee": ["补卡", "replace"],
    "close_fee": ["销户", "close my account", "closing"],
    "daily_limit": ["限额", "最多能转", "limit"],
    "arrival_time": ["多久", "几天", "到账", "how long", "arrive", "when will"],
    "currencies": ["哪些货币", "什么货币", "currencies", "currency"],
    "fee": ["手续费", "收费", "多少钱", "要钱", "fee", "charge", "cost"],
}

def find_attr(text):
    text = text.lower()

    for attr_key, keywords in ATTR_KEYWORDS.items():
        if any(word in text for word in keywords):
            return attr_key
    return None

def get_product_attr(product_id, attr_key):
    cypher = """
        MATCH (p:BankProduct {product_id: $product_id})-[:Have]->(a:BankAttr {attr_key: $attr_key})
        RETURN p.name_zh AS product_zh, p.name_en AS product_en,
               a.name_zh AS name_zh, a.name_en AS name_en, a.value_zh AS value_zh, a.value_en AS value_en
    """
    # 1. 执行查询
    records = run_cypher(cypher, {"product_id": product_id, "attr_key": attr_key})
    # 2. 没查到（records 是空的）就返回 None
    if not records:
        return None
    # 3. 查到了就返回第一条，转成字典：dict(records[0])
    return dict(records[0])


def get_product_names(product_ids):
    """产品 ID 转成名字：[{"product_id", "name_zh", "name_en"}, ...]，用别名缓存里的数据，不用再查 Neo4j"""
    products = {}
    for product in load_alias_cache().values():
        products[product["product_id"]] = product  # 同一个产品有好几个别名，存一次就够了
    return [products[pid] for pid in product_ids if pid in products]

if __name__ == "__main__":
    # 1. 能不能连上 Neo4j
    d = get_neo4j_driver()
    assert d is not None, "没有存到全局变量"
    assert get_neo4j_driver() is d  # 第二次直接返回同一个
    print("1. 连接 Neo4j 成功")

    # 2. 别名缓存
    aliases = load_alias_cache()
    assert load_alias_cache() is aliases  # 第二次直接用缓存
    print(f"2. 别名 {len(aliases)} 个，电汇 -> {aliases['电汇']['name_zh']}")

    # 3. 从问题到答案：找产品 -> 找属性 -> 查图谱
    print("3. 问题测试：")
    questions = [
        "汇钱回家要几天？",                       # overseas_tt / arrival_time
        "How much is the wire transfer fee?",    # overseas_tt / fee
        "信用卡年费多少",                          # mastercard_credit / annual_fee
        "银联卡在中国刷卡外币交易费多少",            # unionpay_debit / fx_fee
        "我的多币种账户支持什么货币",               # 只有 multi_currency 一个
        "I want to settle my bill",              # 没有产品，tt 不能匹配 settle
        "PayNow 和 FAST 哪个限额高",              # 两个产品 / daily_limit
        "银联卡年费多少",                          # 银联卡没有年费这一项 -> None
    ]
    for q in questions:
        products = find_products(q)
        attr_key = find_attr(q)
        print(f"  {q}")
        print(f"      产品：{[p['product_id'] for p in products]}  属性：{attr_key}")
        for p in products:
            if attr_key:
                result = get_product_attr(p["product_id"], attr_key)
                if result:
                    print(f"      -> {result['product_zh']} {result['name_zh']}：{result['value_zh']}")
                else:
                    print(f"      -> {p['name_zh']} 没有这一项")



