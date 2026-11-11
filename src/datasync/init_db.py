"""Create (or reset) the mock bank database from schema.sql + seed.sql.

    python src/datasync/init_db.py            # recreate tables and load demo data
    python src/datasync/init_db.py --check    # only print row counts

Reads MYSQL_* settings from the project's .env file.
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

import pymysql
from dotenv import load_dotenv
from pymysql.constants import CLIENT

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent.parent
TABLES = ["customers", "accounts", "cards", "payees", "transfers", "card_transactions", "tickets", "turn_log", "product_category", "product", "product_attr", "product_alias"]


def connect(database: str | None = None) -> pymysql.connections.Connection:
    load_dotenv(ROOT / ".env")
    return pymysql.connect(
        host=os.getenv("MYSQL_HOST", "127.0.0.1"),
        port=int(os.getenv("MYSQL_PORT", "3306")),
        user=os.getenv("MYSQL_USER", "root"),
        password=os.getenv("MYSQL_PASSWORD", ""),
        database=database,
        charset=os.getenv("MYSQL_CHARSET", "utf8mb4"),
        client_flag=CLIENT.MULTI_STATEMENTS,
        autocommit=True,
    )


def run_sql_file(conn, path: Path) -> None:
    with conn.cursor() as cur:
        cur.execute(path.read_text(encoding="utf-8"))
        while cur.nextset():  # consume the result of every statement in the file
            pass
    print(f"ran {path.name}")


def print_counts(conn, db: str) -> None:
    with conn.cursor() as cur:
        cur.execute(f"USE `{db}`")
        for t in TABLES:
            cur.execute(f"SELECT COUNT(*) FROM `{t}`")
            print(f"  {t:<18} {cur.fetchone()[0]:>4} rows")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true", help="only print row counts")
    args = ap.parse_args()
    load_dotenv(ROOT / ".env")
    db = os.getenv("MYSQL_DATABASE", "lioncity_bank")

    try:
        conn = connect()
    except pymysql.err.OperationalError as e:
        sys.exit(f"Cannot connect to MySQL: {e}")
    with conn:
        if not args.check:
            run_sql_file(conn, HERE / "schema.sql")
            run_sql_file(conn, HERE / "seed.sql")
        print(f"database `{db}`:")
        print_counts(conn, db)


if __name__ == "__main__":
    main()
