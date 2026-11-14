# 账户相关的查询：余额、最近的转账、银行卡、收款人
# 所有查询都带 customer_id，而且 customer_id 只从登录的会话里来，不从客户的话里来
# 这里只读数据；冻结卡会改数据，放在后面加，还要先让客户确认
# 在项目根目录运行测试：uv run python -m src.utils.account_utils
import logging
import re
from datetime import datetime, timedelta, timezone
from decimal import Decimal

from src.utils.mysql_utils import execute, query

logger = logging.getLogger(__name__)


# 1. 客户的账户和余额
def get_accounts(customer_id):
    sql = """
        select account_id, account_type, currency, balance, status
        from accounts
        where customer_id = %s and status = 'active'
        order by account_type, account_id
    """
    return query(sql, (customer_id,))


def get_latest_card_transaction(customer_id):
    """只查当前客户本人银行卡的最近一笔交易。"""
    sql = """
        select t.txn_id, t.merchant, t.amount, t.currency, t.txn_time, t.status, c.last4
        from card_transactions t join cards c on c.card_id = t.card_id
        where c.customer_id = %s
        order by t.txn_time desc, t.txn_id desc limit 1
    """
    rows = query(sql, (customer_id,))
    return rows[0] if rows else None


# 2. 最近的转账，可以只看汇给某几个收款人的
def get_recent_transfers(customer_id, payee_ids=None, days=30, limit=5):
    sql = """
        select t.transfer_id, t.transfer_type, t.amount, t.currency, t.fee, t.status,
               t.status_note_zh, t.status_note_en, t.created_at, t.completed_at,
               p.nickname, p.full_name, p.bank_name, p.country
        from transfers t
        join payees p on t.payee_id = p.payee_id
        where t.customer_id = %s and t.created_at >= NOW() - INTERVAL %s DAY
    """
    params = [customer_id, days]
    if payee_ids:
        # in (...) 要几个值就写几个 %s，值本身还是用参数传，不拼到 SQL 里
        placeholders = ", ".join(["%s"] * len(payee_ids))
        sql += f" and t.payee_id in ({placeholders})"
        params += list(payee_ids)
    sql += " order by t.created_at desc limit %s"
    params.append(limit)
    return query(sql, params)


def transfer_filters(text, today=None):
    """识别明确的日期和金额；不使用上一轮留下的实体。"""
    text = text or ""
    today = today or datetime.now(timezone(timedelta(hours=8))).date()
    date = None
    m = re.search(r"(?<!\d)(\d{4})[-/年](\d{1,2})[-/月](\d{1,2})日?(?!\d)", text)
    if m:
        from datetime import date as date_type
        date = date_type(*map(int, m.groups()))
    elif re.search(r"前天|the day before yesterday", text, re.I):
        date = today - timedelta(days=2)
    elif re.search(r"昨天|yesterday", text, re.I):
        date = today - timedelta(days=1)
    elif re.search(r"今天|today", text, re.I):
        date = today
    elif re.search(r"上周|上星期|上个月|last week|last month|\d{1,2}月\d{1,2}[日号]?|"
                   r"(?<!\d)\d{1,2}[/-]\d{1,2}(?!\d)", text, re.I):
        raise ValueError("unsupported date")

    m = re.search(r"(?:(S\$|\$|SGD|USD|CNY)\s*([\d,]+(?:\.\d{1,2})?))|"
                  r"(?:([\d,]+(?:\.\d{1,2})?)\s*(新币|块|元|SGD|USD|CNY|dollars?))", text, re.I)
    amount = Decimal((m.group(2) or m.group(3)).replace(",", "")) if m else None
    unit = (m.group(1) or m.group(4)).upper() if m else ""
    currency = {"S$": "SGD", "新币": "SGD", "SGD": "SGD", "USD": "USD", "CNY": "CNY"}.get(unit)
    return {"date": date, "amount": amount, "currency": currency}


def find_transfers(customer_id, payee_ids=None, date=None, amount=None, currency=None, limit=51):
    """所有筛选都在数据库执行，避免只查最近几笔后漏掉匹配记录。"""
    sql = """
        select t.transfer_id, t.transfer_type, t.amount, t.currency, t.status,
               t.status_note_zh, t.status_note_en, t.created_at, p.nickname
        from transfers t join payees p on t.payee_id = p.payee_id and p.customer_id = t.customer_id
        where t.customer_id = %s
    """
    params = [customer_id]
    if payee_ids:
        sql += " and t.payee_id in (" + ", ".join(["%s"] * len(payee_ids)) + ")"
        params.extend(payee_ids)
    if date:
        sql += " and t.created_at >= %s and t.created_at < %s"
        params.extend([date, date + timedelta(days=1)])
    else:
        sql += " and t.created_at >= NOW() - INTERVAL 30 DAY"
    if amount is not None:
        sql += " and t.amount = %s"
        params.append(amount)
    if currency:
        sql += " and t.currency = %s"
        params.append(currency)
    sql += " order by t.created_at desc, t.transfer_id limit %s"
    params.append(limit)
    return query(sql, params)


def get_transfer_by_id(customer_id, transfer_id):
    """按客户和交易编号重新读取状态。"""
    sql = """
        select t.transfer_id, t.transfer_type, t.amount, t.currency, t.status,
               t.status_note_zh, t.status_note_en, t.created_at, p.nickname
        from transfers t join payees p on t.payee_id = p.payee_id and p.customer_id = t.customer_id
        where t.customer_id = %s and t.transfer_id = %s
    """
    rows = query(sql, (customer_id, transfer_id))
    return rows[0] if rows else None


# 3. 客户的银行卡
def get_cards(customer_id):
    sql = """
        select card_id, last4, card_type, network, status, expiry
        from cards
        where customer_id = %s
        order by card_type, card_id
    """
    return query(sql, (customer_id,))


def freeze_card(customer_id, last4):
    """只冻结该客户指定的可用卡；状态变化时不会覆盖其他状态。"""
    sql = """
        update cards set status = 'frozen', frozen_at = NOW()
        where customer_id = %s and last4 = %s and status = 'active'
    """
    return execute(sql, (customer_id, last4))["rowcount"] == 1


# 4. 客户说的"妈妈"、"landlord"是哪个收款人（实体链接）
# 收款人的昵称每个客户不一样，所以存在 MySQL 里按客户查，不放在术语图谱里
def find_payees(customer_id, text):
    """客户的话里提到了哪些收款人的昵称，返回这些收款人"""
    payees = query("select payee_id, nickname, full_name from payees where customer_id = %s", (customer_id,))
    text = (text or "").lower()
    found = []
    for p in payees:
        nickname = p["nickname"].lower()
        if nickname.isascii():
            # 英文昵称要整个词，"mum" 不能匹配 "minimum"（和 neo4j_utils.find_products 一样）
            pattern = rf"(?<![a-z]){re.escape(nickname)}(?![a-z])"
        else:
            pattern = re.escape(nickname)
        if re.search(pattern, text):
            found.append(p)
    return found


if __name__ == "__main__":
    for cid in ["C001", "C002", "C003"]:
        print(f"\n===== {cid}")
        for a in get_accounts(cid):
            print(f"  账户 {a['account_id']} {a['account_type']:<15} {a['currency']} {a['balance']}")
        for c in get_cards(cid):
            print(f"  卡   ****{c['last4']} {c['card_type']:<6} {c['network']:<10} {c['status']}")
        for t in get_recent_transfers(cid):
            print(f"  转账 {t['created_at']:%m-%d} {t['transfer_type']:<11} {t['currency']} {t['amount']:>8} "
                  f"-> {t['nickname']:<8} {t['status']}")

    mum = find_payees("C001", "我昨天给妈妈的 TT 到了没？")
    print("\nC001 说'妈妈'：", [p["nickname"] for p in mum])
    print("只看妈妈的转账：", [t["transfer_id"] for t in get_recent_transfers("C001", [p["payee_id"] for p in mum])])
    print("C002 说'mum'（大小写不一样）：", [p["nickname"] for p in find_payees("C002", "Did my transfer to Mum arrive?")])
    print("C002 说'妈妈'（C002 没有叫妈妈的收款人）：", find_payees("C002", "我给妈妈的钱"))
    print("C002 说'minimum'（不能当成 mum）：", find_payees("C002", "What is the minimum balance?"))
    print("C003 说'SP Group'（昵称里有空格）：", [p["nickname"] for p in find_payees("C003", "my GIRO to SP Group")])
    print("不存在的客户：", get_accounts("C999"))
