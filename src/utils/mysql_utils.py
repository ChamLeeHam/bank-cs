# MySQL 的操作：连接数据库、查询、写入
# 在项目根目录运行测试：uv run python -m src.utils.mysql_utils
import logging

import pymysql
from pymysql.cursors import DictCursor

from src.schema.config import get_config
from src.schema.exceptions import MySQLError

logger = logging.getLogger(__name__)


# 1. 获取连接
# 每次用完就关掉。本地数据库连起来很快，就不用连接池了，也不用担心连接超时断掉
def get_mysql_connection():
    config = get_config()
    try:
        return pymysql.connect(
            host=config.mysql_host,
            port=config.mysql_port,
            user=config.mysql_user,
            password=config.mysql_password,
            database=config.mysql_database,
            charset=config.mysql_charset,
            cursorclass=DictCursor,  # 查出来每一行是字典，不是元组
            autocommit=True,
            connect_timeout=5,
        )
    except Exception as e:
        logger.error(f"MySQL连接失败：{e}")
        raise MySQLError(f"无法连接 MySQL：{config.mysql_host}:{config.mysql_port}", "mysql_utils", e)


# 2. 查询，返回字典列表
# 参数一定要用 %s 传进去，不要自己拼字符串，防止 SQL 注入
def query(sql, params=None):
    try:
        with get_mysql_connection() as conn:
            with conn.cursor() as cursor:
                cursor.execute(sql, params)
                return list(cursor.fetchall())
    except MySQLError:
        raise
    except Exception as e:
        logger.error(f"MySQL查询失败：{e}")
        raise MySQLError("查询失败", "mysql_utils", e)


# 3. 只查一行，没有就返回 None
def query_one(sql, params=None):
    rows = query(sql, params)
    return rows[0] if rows else None


# 4. 写入（insert / update），返回影响的行数和自增 id
def execute(sql, params=None):
    try:
        with get_mysql_connection() as conn:
            with conn.cursor() as cursor:
                rowcount = cursor.execute(sql, params)
                return {"rowcount": rowcount, "lastrowid": cursor.lastrowid}
    except MySQLError:
        raise
    except Exception as e:
        logger.error(f"MySQL写入失败：{e}")
        raise MySQLError("写入失败", "mysql_utils", e)


if __name__ == "__main__":
    # 1. 能不能连上
    print(query_one("SELECT VERSION() AS version, DATABASE() AS db"))

    # 2. 查 C001 的账户
    for row in query("SELECT account_id, account_type, currency, balance FROM accounts WHERE customer_id = %s",
                     ("C001",)):
        print(" ", row)

    # 3. 查不到的时候返回 None
    print("不存在的客户：", query_one("SELECT * FROM customers WHERE customer_id = %s", ("C999",)))

    # 4. 参数里带引号也没关系，不会被当成 SQL
    print("注入测试：", query("SELECT customer_id FROM customers WHERE customer_id = %s", ("C001' OR '1'='1",)))
