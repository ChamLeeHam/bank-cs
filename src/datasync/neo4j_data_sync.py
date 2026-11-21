# 把 MySQL 里的产品数据迁移到 Neo4j，做成中英术语图谱
# 运行：python src/datasync/neo4j_data_sync.py
import argparse
import sys

import pymysql
from pathlib import Path

from neo4j import GraphDatabase
from pymysql.cursors import DictCursor

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(PROJECT_ROOT))
from src.schema.config import get_config
from src.utils.financial_products import load_financial_catalog

config = get_config()


def read_product_info(cursor):
    """查产品和分类"""
    sql = """
        select p.product_id, p.name_zh, p.name_en,c.category_id, c.name_zh as category_zh, c.name_en as category_en
        from product p
        left join product_category c on p.category_id = c.category_id
    """
    cursor.execute(sql)
    result = cursor.fetchall()
    return result

def read_product_attr(cursor):
    """查产品属性，比如手续费、到账时间"""
    sql = """
        select product_id, attr_key, name_zh, name_en, value_zh, value_en
        from product_attr
    """
    cursor.execute(sql)
    return cursor.fetchall()

def read_product_alias(cursor):
    """查客户的说法，比如 电汇、汇钱回家"""
    sql = """
        select product_id, alias
        from product_alias
    """
    cursor.execute(sql)
    return cursor.fetchall()

def clear_bank_graph(driver):
    """只删我们自己的节点，课程项目的 SKU、Attr 那些不要动"""
    driver.execute_query("""
        MATCH (n) WHERE n:BankCategory OR n:BankProduct OR n:BankAttr OR n:BankAlias
        DETACH DELETE n
    """, database_=config.neo4j_database)

def write_product_info(driver, products):
    """产品 -> 分类"""
    for product in products:
        cypher = """
            MERGE (c:BankCategory {category_id: $category_id})
            SET c.name_zh = $category_zh, c.name_en = $category_en
            MERGE (p:BankProduct {product_id: $product_id})
            SET p.name_zh = $name_zh, p.name_en = $name_en
            MERGE (p)-[:Belong]->(c)
        """
        driver.execute_query(cypher, parameters_=product, database_=config.neo4j_database)

def write_product_attr(driver, attrs):
    """产品 -> 属性。用 MATCH 找产品，所以要先写产品"""
    for attr in attrs:
        cypher = """
            MATCH (p:BankProduct {product_id: $product_id})
            MERGE (a:BankAttr {product_id: $product_id, attr_key: $attr_key})
            SET a.name_zh = $name_zh, a.name_en = $name_en, a.value_zh = $value_zh, a.value_en = $value_en
            MERGE (p)-[:Have]->(a)
        """
        driver.execute_query(cypher, parameters_=attr, database_=config.neo4j_database)

def write_product_alias(driver, aliases):
    """别名 -> 产品。别名存小写，查的时候也转小写"""
    for alias in aliases:
        cypher = """
            MATCH (p:BankProduct {product_id: $product_id})
            MERGE (al:BankAlias {name: toLower($alias)})
            MERGE (al)-[:Refers]->(p)
        """
        driver.execute_query(cypher, parameters_=alias, database_=config.neo4j_database)


def sync_financial_products(driver):
    """Add or refresh the public demo catalog without clearing existing nodes."""
    catalog = load_financial_catalog()
    rows = [
        {
            **product,
            "category_names": catalog["categories"][product["category"]],
            "use_case_names": catalog["use_cases"][product["use_case"]],
            "loan_plans": product.get("loan_plans", []),
            "attrs": [
                {"key": "overview", "zh": "产品说明", "en": "Overview",
                 "value_zh": product["overview_zh"], "value_en": product["overview_en"]},
                {"key": "key_terms", "zh": "条款核对", "en": "Terms to check",
                 "value_zh": product["terms_zh"], "value_en": product["terms_en"]},
                *product.get("attributes", ()),
            ],
        }
        for product in catalog["products"]
    ]
    driver.execute_query("""
        UNWIND $products AS item
        MERGE (p:BankProduct {product_id: item.id})
        SET p.name_zh = item.zh, p.name_en = item.en, p.catalog = 'retail_demo',
            p.loan_plan_ids = [plan IN item.loan_plans | plan.id]
        MERGE (c:BankCategory {category_id: item.category})
        SET c.name_zh = item.category_names.zh, c.name_en = item.category_names.en
        MERGE (p)-[:Belong]->(c)
        MERGE (u:BankUseCase {use_case_id: item.use_case})
        SET u.name_zh = item.use_case_names.zh, u.name_en = item.use_case_names.en
        MERGE (p)-[:SUITABLE_FOR]->(u)
        FOREACH (alias IN item.aliases |
            MERGE (al:BankAlias {name: toLower(alias)})
            MERGE (al)-[:Refers]->(p)
        )
        FOREACH (attr IN item.attrs |
            MERGE (a:BankAttr {product_id: item.id, attr_key: attr.key})
            SET a.name_zh = attr.zh, a.name_en = attr.en,
                a.value_zh = attr.value_zh, a.value_en = attr.value_en
            MERGE (p)-[:Have]->(a)
        )
        FOREACH (plan IN item.loan_plans |
            MERGE (q:BankLoanPlan {plan_id: plan.id})
            SET q.term_months = plan.term_months, q.annual_rate_percent = plan.annual_rate_percent,
                q.upfront_fee = plan.upfront_fee
            MERGE (p)-[:OFFERS_LOAN_PLAN]->(q)
        )
        """, products=rows, database_=config.neo4j_database)
    return len(rows)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Sync bank products to Neo4j")
    parser.add_argument("--financial-products-only", action="store_true", help="Add or refresh the demo financial catalog only")
    args = parser.parse_args()
    try:
        if args.financial_products_only:
            with GraphDatabase.driver(config.neo4j_uri, auth=(config.neo4j_user, config.neo4j_password)) as driver:
                print(f"金融产品目录更新完成，共 {sync_financial_products(driver)} 款。")
            sys.exit(0)
        # 1.从mysql中读取产品、属性、别名
        with pymysql.connect(
            host=config.mysql_host,
            port=config.mysql_port,
            user=config.mysql_user,
            password=config.mysql_password,
            database=config.mysql_database,
            charset=config.mysql_charset,
        ) as conn:
            with conn.cursor(DictCursor) as cursor:
                products = read_product_info(cursor)
                attrs = read_product_attr(cursor)
                aliases = read_product_alias(cursor)

        # 2.写进 neo4j: 先清空，再按产品 -> 属性 -> 别名
        with GraphDatabase.driver(uri=config.neo4j_uri, auth=(config.neo4j_user, config.neo4j_password)) as driver:
            clear_bank_graph(driver)
            write_product_info(driver, products)
            write_product_attr(driver, attrs)
            write_product_alias(driver, aliases)
            financial_products = sync_financial_products(driver)
            print(f"数据迁移成功！产品 {len(products)} 个，属性 {len(attrs)} 个，别名 {len(aliases)} 个")
            print(f"金融产品目录更新完成，共 {financial_products} 款。")
    except Exception as err:
        print(f"数据迁移失败：{type(err).__name__}: {err}")
        sys.exit(1)
