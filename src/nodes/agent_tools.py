# Bank Agent 的工具：知识库检索、产品资料
# 这两个工具只返回公开的资料（FAQ、产品图谱），可以发给 DeepSeek
# 账户的工具在后面加，账户数据不会发给 DeepSeek
# 在项目根目录运行测试：uv run python -m src.nodes.agent_tools
import hashlib
import logging
from typing import Literal

from langchain.tools import tool

from src.nodes.rag_answer import RagAnswerNode
from src.schema.exceptions import BankAssistantError, Neo4jError
from src.utils.neo4j_utils import find_products, get_product_attr, get_product_attrs

logger = logging.getLogger(__name__)

# 检索直接用 rag_answer 节点里的 retrieve()，不用再写一遍；只创建一次
_rag = RagAnswerNode()


def faq_source_id(doc, section):
    """同一章节多次检索时使用相同的来源编号。"""
    value = f"{doc}\n{section}".encode("utf-8")
    return hashlib.sha256(value).hexdigest()[:12]


# 1. 知识库检索
# content 发给 DeepSeek，artifact（来源）留在本地，后面 output_guard 用它加"来源"
@tool(response_format="content_and_artifact")
def search_faq(query: str):
    """在狮城银行的 FAQ 知识库里检索。问手续费、转账、银行卡、账户服务这类问题时用。
    query 要写成一个完整的问题（结合上下文，不要写"那这个呢"），中文英文都可以。"""
    try:
        # history 传空的：Agent 已经把问题写完整了，不用再重写一次
        _, docs, _ = _rag.retrieve(query, [])
    except BankAssistantError as e:
        logger.error(f"知识库检索失败：{e}")
        return "知识库暂时无法使用。", []

    if not docs:
        return "知识库里没有找到相关内容。不要自己编答案，告诉客户没有这方面的资料，可以转人工。", []

    parts = []
    for i, d in enumerate(docs, 1):
        body = d["text"].split("\n", 1)[-1]  # 第一行是章节标题，后面才是正文
        source_id = faq_source_id(d["doc"], d["section"])
        parts.append(f"【{i}】[FAQ:{source_id}]《{d['title']}》{d['section']}\n{body}")
    content = "\n\n".join(parts)
    sources = [{"doc": d["doc"], "title": d["title"], "section": d["section"], "rerank_score": d["rerank_score"]}
               for d in docs]
    return content, sources


# 能查的属性，和 Neo4j 里 BankAttr 的 attr_key 一样
# 写成 Literal，DeepSeek 只能从这几个里面选，不会编一个不存在的
ATTRIBUTES = Literal["fee", "arrival_time", "daily_limit", "annual_fee", "fx_fee", "late_fee",
                     "replacement_fee", "close_fee", "close_time", "currencies"]


# 2. 产品资料（术语图谱）
# artifact 是查到资料的产品 ID，查到了才在回答后面加"来源：产品资料"
@tool(response_format="content_and_artifact")
def get_product_info(product: str, attribute: ATTRIBUTES | None = None):
    """查狮城银行某个产品的资料：转账（PayNow、FAST、GIRO、海外电汇 TT）、银行卡（Visa 借记卡、银联卡、
    Mastercard 信用卡）、账户（储蓄账户、多币种账户）。
    product 用客户的说法就行，比如"电汇"、"汇钱回家"、"credit card"。
    attribute 是要查的那一项；不确定就不传，会返回这个产品的所有资料。
    比 search_faq 快，数字也准，问的是具体产品的手续费、限额、到账时间、年费这些，先用这个。"""
    try:
        products = find_products(product)
        if not products:
            return f"产品资料里没有找到“{product}”，可以用 search_faq 查。", []

        lines, found = [], []
        for p in products:
            if attribute:
                row = get_product_attr(p["product_id"], attribute)
                rows = [row] if row else []
                if not rows:
                    lines.append(f"{p['name_zh']} / {p['name_en']}：没有 {attribute} 这一项的资料")
            else:
                rows = get_product_attrs(p["product_id"])
            for r in rows:
                lines.append(f"{r['product_zh']} / {r['product_en']} - {r['name_zh']} / {r['name_en']}："
                             f"{r['value_zh']} / {r['value_en']}")
            if rows:
                found.append(p["product_id"])
        return "\n".join(lines), found
    except Neo4jError as e:
        logger.warning(f"术语图谱查不了：{e}")
        return "产品资料暂时查不到，可以用 search_faq 查。", []


if __name__ == "__main__":
    # 1. 像 Agent 那样调用：传一个 tool_call，返回 ToolMessage，content 给 DeepSeek，artifact 留在本地
    msg = search_faq.invoke({"name": "search_faq", "args": {"query": "PayNow 转账要手续费吗？"},
                             "id": "test-1", "type": "tool_call"})
    print("===== search_faq content\n", msg.content[:500])
    print("===== search_faq artifact\n", msg.artifact)

    print("\n===== 知识库里没有的：", search_faq.invoke({"query": "定期存款利率是多少？"}))

    # 2. 产品资料
    for args in [{"product": "汇钱回家", "attribute": "arrival_time"},
                 {"product": "credit card", "attribute": "annual_fee"},
                 {"product": "银联卡", "attribute": "annual_fee"},  # 银联卡没有年费这一项
                 {"product": "电汇"},                               # 不传 attribute，返回所有资料
                 {"product": "home loan"}]:                         # 图谱里没有
        print(f"\n===== get_product_info {args}\n{get_product_info.invoke(args)}")

    # 3. 模型看到的工具说明和参数：attribute 是一个固定的列表
    print("\n", get_product_info.tool_call_schema.model_json_schema())

    # 4. attribute 不在列表里会被拒绝
    try:
        get_product_info.invoke({"product": "电汇", "attribute": "interest_rate"})
    except Exception as e:
        print("\n编一个不存在的 attribute 会被拒绝：", type(e).__name__)
