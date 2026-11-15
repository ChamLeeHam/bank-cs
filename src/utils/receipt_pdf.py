"""Generate a single-transaction, Chinese-language demo receipt in memory."""

from datetime import datetime
from decimal import Decimal
from io import BytesIO
from pathlib import Path
from zoneinfo import ZoneInfo
from xml.sax.saxutils import escape

from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER, TA_LEFT, TA_RIGHT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle


INK = colors.HexColor("#261d21")
MUTED = colors.HexColor("#6e5e63")
RED = colors.HexColor("#781633")
LINE = colors.HexColor("#e9dedf")
PALE = colors.HexColor("#fbf5f6")
GREEN = colors.HexColor("#286044")
AMBER = colors.HexColor("#875318")

STATUS = {
    "completed": "已完成", "processing": "处理中", "pending_review": "审核中",
    "failed": "未成功", "returned": "已退回", "posted": "已入账",
    "pending": "处理中", "reversed": "已撤销",
}
TYPES = {
    "Overseas TT": "海外电汇", "FAST": "FAST 即时转账", "PayNow": "PayNow 转账",
    "GIRO": "GIRO 自动转账",
}
COUNTRIES = {"SG": "新加坡", "CN": "中国", "MY": "马来西亚", "US": "美国", "GB": "英国", "AU": "澳大利亚"}


def _register_fonts():
    if "LCB-CJK" in pdfmetrics.getRegisteredFontNames():
        return
    candidates = [
        Path("C:/Windows/Fonts/msyh.ttc"),
        Path("C:/Windows/Fonts/simhei.ttf"),
    ]
    font_path = next((path for path in candidates if path.exists()), None)
    if font_path is None:
        raise RuntimeError("找不到可用于生成中文回单的系统字体。")
    if font_path.suffix.lower() == ".ttc":
        pdfmetrics.registerFont(TTFont("LCB-CJK", str(font_path), subfontIndex=0))
    else:
        pdfmetrics.registerFont(TTFont("LCB-CJK", str(font_path)))


def _p(text, style):
    return Paragraph(escape(str(text or "—")), style)


def _masked_account(value):
    if not value:
        return "未记录"
    value = str(value)
    if "*" in value:
        return value
    digits = "".join(char for char in value if char.isalnum())
    return f"**** {digits[-4:]}" if digits else "未记录"


def _money(amount, currency):
    return f"{currency} {Decimal(str(amount)):.2f}"


def _date(value):
    if not value:
        return "未记录"
    if isinstance(value, datetime):
        return value.strftime("%d %b %Y, %H:%M")
    return str(value).replace("T", " ")[:16]


def build_transaction_receipt(transaction, customer):
    """Return PDF bytes for a transaction already scoped to the verified customer."""
    _register_fonts()
    output = BytesIO()
    doc = SimpleDocTemplate(
        output, pagesize=A4, rightMargin=18 * mm, leftMargin=18 * mm,
        topMargin=19 * mm, bottomMargin=17 * mm,
        title=f"狮城银行电子交易回单 {transaction['transaction_id']}",
        author="狮城银行演示系统",
    )
    base = getSampleStyleSheet()
    styles = {
        "eyebrow": ParagraphStyle("eyebrow", parent=base["Normal"], fontName="LCB-CJK", fontSize=8, leading=12, textColor=MUTED),
        "title": ParagraphStyle("title", parent=base["Title"], fontName="LCB-CJK", fontSize=19, leading=25, alignment=TA_RIGHT, textColor=INK, spaceAfter=2),
        "heading": ParagraphStyle("heading", parent=base["Heading2"], fontName="LCB-CJK", fontSize=11, leading=15, textColor=INK, spaceBefore=4, spaceAfter=7),
        "label": ParagraphStyle("label", parent=base["Normal"], fontName="LCB-CJK", fontSize=8, leading=12, textColor=MUTED),
        "value": ParagraphStyle("value", parent=base["Normal"], fontName="LCB-CJK", fontSize=9, leading=14, textColor=INK, wordWrap="CJK"),
        "amount_label": ParagraphStyle("amount_label", parent=base["Normal"], fontName="LCB-CJK", fontSize=9, leading=14, alignment=TA_CENTER, textColor=MUTED),
        "amount": ParagraphStyle("amount", parent=base["Normal"], fontName="LCB-CJK", fontSize=26, leading=33, alignment=TA_CENTER, textColor=INK),
        "status": ParagraphStyle("status", parent=base["Normal"], fontName="LCB-CJK", fontSize=9, leading=14, alignment=TA_CENTER, textColor=GREEN),
        "right": ParagraphStyle("right", parent=base["Normal"], fontName="LCB-CJK", fontSize=9, leading=14, alignment=TA_RIGHT, textColor=INK, wordWrap="CJK"),
        "note": ParagraphStyle("note", parent=base["Normal"], fontName="LCB-CJK", fontSize=7.5, leading=11, textColor=MUTED, wordWrap="CJK"),
        "brand": ParagraphStyle("brand", parent=base["Normal"], fontName="LCB-CJK", fontSize=12, leading=17, textColor=RED),
        "receipt_title": ParagraphStyle("receipt_title", parent=base["Normal"], fontName="LCB-CJK", fontSize=16, leading=21, alignment=TA_RIGHT, textColor=INK),
    }

    def section(title):
        return [_p(title, styles["heading"])]

    def details(rows, widths=None):
        data = [[_p(label, styles["label"]), _p(value, styles["right"])] for label, value in rows]
        table = Table(data, colWidths=widths or [48 * mm, 116 * mm], hAlign="LEFT")
        table.setStyle(TableStyle([
            ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ("LEFTPADDING", (0, 0), (-1, -1), 8), ("RIGHTPADDING", (0, 0), (-1, -1), 8),
            ("TOPPADDING", (0, 0), (-1, -1), 5), ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
            ("LINEBELOW", (0, 0), (-1, -2), 0.45, LINE),
            ("BACKGROUND", (0, 0), (-1, -1), colors.white),
        ]))
        return table

    txn_id = transaction["transaction_id"]
    status_key = transaction["status"]
    status_label = STATUS.get(status_key, status_key)
    status_color = GREEN if status_key in {"completed", "posted"} else AMBER if status_key in {"processing", "pending", "pending_review"} else RED
    amount = _money(transaction["amount"], transaction["currency"])
    kind_label = "银行卡消费" if transaction["kind"] == "card" else TYPES.get(transaction["description"], transaction["description"])
    generated_at = datetime.now(ZoneInfo("Asia/Singapore")).strftime("%d %b %Y, %H:%M SGT")

    story = []
    brand_table = Table([
        [Paragraph("狮城银行<br/><font size='7'>LION CITY BANK</font>", styles["brand"]),
         Paragraph("电子交易回单<br/><font size='8' color='#6e5e63'>TRANSACTION RECEIPT</font>", styles["receipt_title"])],
    ], colWidths=[78 * mm, 86 * mm])
    brand_table.setStyle(TableStyle([
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("ALIGN", (1, 0), (1, 0), "RIGHT"),
        ("LINEBELOW", (0, 0), (-1, -1), 1.3, RED),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 12),
    ]))
    story += [brand_table, Spacer(1, 15)]

    hero = Table([
        [_p("交易金额", styles["amount_label"])],
        [_p(amount, styles["amount"])],
        [_p(f"{status_label}  ·  {kind_label}", ParagraphStyle("status_current", parent=styles["status"], textColor=status_color))],
        [_p(f"交易编号：{txn_id}", styles["amount_label"])],
    ], colWidths=[164 * mm])
    hero.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), PALE), ("BOX", (0, 0), (-1, -1), 0.6, LINE),
        ("LEFTPADDING", (0, 0), (-1, -1), 12), ("RIGHTPADDING", (0, 0), (-1, -1), 12),
        ("TOPPADDING", (0, 0), (-1, 0), 13), ("BOTTOMPADDING", (0, 0), (-1, 0), 3),
        ("TOPPADDING", (0, 1), (-1, 1), 0), ("BOTTOMPADDING", (0, 1), (-1, 1), 4),
        ("TOPPADDING", (0, 2), (-1, 2), 1), ("BOTTOMPADDING", (0, 2), (-1, 2), 8),
        ("TOPPADDING", (0, 3), (-1, 3), 5), ("BOTTOMPADDING", (0, 3), (-1, 3), 13),
    ]))
    story += [hero, Spacer(1, 12)]

    story += section("交易资料")
    txn_rows = [
        ("交易类型", kind_label),
        ("交易时间", _date(transaction.get("occurred_at"))),
        ("交易编号", txn_id),
        ("交易状态", status_label),
        ("交易金额", amount),
    ]
    if transaction["kind"] == "transfer":
        txn_rows.append(("本行手续费", _money(transaction.get("transfer_fee") or 0, "SGD")))
    story += [details(txn_rows), Spacer(1, 10)]

    story += section("账户与对方信息")
    if customer.get("name_zh") or customer.get("name_en"):
        holder = customer.get("name_zh") or customer.get("name_en")
    else:
        holder = customer.get("customer_id", "演示客户")
    party_rows = [("付款人", holder)]
    if transaction["kind"] == "transfer":
        if transaction.get("debit_account_id"):
            party_rows.append(("付款账户", f"{transaction.get('debit_account_type') or '账户'}　{_masked_account(transaction['debit_account_id'])}"))
        party_rows.extend([
            ("收款人", transaction.get("payee_name") or transaction.get("payee_nickname") or "未记录"),
            ("收款账号", transaction.get("payee_account_masked") or "未记录"),
            ("收款银行", transaction.get("payee_bank_name") or "未记录"),
            ("国家 / 地区", COUNTRIES.get(transaction.get("payee_country"), transaction.get("payee_country") or "未记录")),
        ])
    else:
        party_rows.extend([
            ("商户", transaction.get("description") or "未记录"),
            ("商户国家 / 地区", COUNTRIES.get(transaction.get("country"), transaction.get("country") or "未记录")),
            ("银行卡", f"{transaction.get('network') or ''} {transaction.get('card_type') or ''}卡　**** {transaction.get('card_last4') or '****'}".strip()),
        ])
        if transaction.get("debit_account_id"):
            party_rows.append(("关联账户", f"{transaction.get('debit_account_type') or '账户'}　{_masked_account(transaction['debit_account_id'])}"))
    story += [details(party_rows), Spacer(1, 13)]

    note = "此文件由狮城银行本地演示系统生成，所列内容来自演示账单记录，仅用于项目展示，不是实际银行凭证。请勿据此办理支付或争议申请。"
    story.append(_p(note, styles["note"]))

    def draw_footer(canvas, _doc):
        canvas.saveState()
        canvas.setStrokeColor(LINE)
        canvas.setLineWidth(0.6)
        canvas.line(18 * mm, 13 * mm, A4[0] - 18 * mm, 13 * mm)
        canvas.setFont("LCB-CJK", 7)
        canvas.setFillColor(MUTED)
        canvas.drawString(18 * mm, 8 * mm, f"生成时间：{generated_at}")
        canvas.drawRightString(A4[0] - 18 * mm, 8 * mm, f"狮城银行演示系统  ·  {txn_id}")
        canvas.restoreState()

    doc.build(story, onFirstPage=draw_footer, onLaterPages=draw_footer)
    return output.getvalue()
