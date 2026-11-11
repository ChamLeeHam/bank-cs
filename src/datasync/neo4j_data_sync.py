# 把 MySQL 里的产品数据迁移到 Neo4j，做成中英术语图谱
# 运行：python src/datasync/neo4j_data_sync.py
import sys

import pymysql
from pathlib import Path

from neo4j import GraphDatabase
from pymysql.cursors import DictCursor

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(PROJECT_ROOT))
from src.schema.config import get_config

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


if __name__ == "__main__":
    try:
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
            print(f"数据迁移成功！产品 {len(products)} 个，属性 {len(attrs)} 个，别名 {len(aliases)} 个")
    except Exception as err:
        print(f"数据迁移失败：{type(err).__name__}: {err}")
