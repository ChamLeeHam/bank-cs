# 知识库问答节点
# 实体抽取找到了产品和属性（比如"电汇手续费"），先直接用术语图谱回答
# 其他问题走 RAG：问题重写 -> 混合检索 + HyDE 检索 -> RRF 合并 -> reranker 精排 -> 生成回答
# 每一步都在 src/utils 里，这个节点只负责按顺序调用
# 在项目根目录运行测试：uv run python -m src.nodes.rag_answer
import time
import re

from src.schema.base import BaseNode, StateUpdate
from src.schema.exceptions import BankAssistantError, Neo4jError
from src.schema.state import GraphState
from src.utils import (hyde_utils, reranker_utils, response_utils, rewrite_query_utils, rrf_utils,
                       search_embedding_utils)
from src.utils.fraud_context import fraud_dispute_comparison_question, unfamiliar_merchant_name_question
from src.utils.history_utils import (annual_fee_followup, fraud_general_followup, history_of, replacement_fee_stolen_followup,
                                    needs_context_rewrite, statement_fee_followup, stolen_card_clarification)
from src.utils.neo4j_utils import get_product_attr, get_product_names
from src.utils.reference_bank_utils import is_reference_policy_question, resolve_reference_bank
from src.utils.scope_utils import unsupported_scope_reason
from src.utils.query_expansion_utils import expand_card_delivery_query

NOT_FOUND = {
    "zh": "抱歉，我们的资料中没有这个问题的答案。如需进一步帮助，可以回复“转人工”联系人工客服。",
    "en": "Sorry, I couldn't find the answer to this in our documents. If you need more help, ask for a human agent.",
}
KB_DOWN = {
    "zh": "抱歉，知识库暂时无法使用，请稍后再试，或回复“转人工”联系人工客服。",
    "en": "Sorry, the knowledge base is unavailable right now. Please try again later or ask for a human agent.",
}
_TT_FEE_WITH_STATUS = re.compile(
    r"(?:海外电汇|电汇|\bTT\b|telegraphic transfer).{0,80}(?:手续费|fee).{0,80}(?:处理中|审核中|待处理|processing|under review|pending)|"
    r"(?:处理中|审核中|待处理|processing|under review|pending).{0,80}(?:海外电汇|电汇|\bTT\b|telegraphic transfer).{0,80}(?:手续费|fee)",
    re.IGNORECASE,
)


class RagAnswerNode(BaseNode):
    name = "rag_answer"

    def __init__(self):
        super().__init__()
        self.last_run = {}  # 上一次的中间结果，评估脚本会用到

    def process(self, state: GraphState) -> StateUpdate:
        self.log_step(self.name, "开始执行知识库问答")
        lang = "en" if state.get("lang") == "en" else "zh"  # 中英混杂的用中文回答
        question = state.get("resolved_question") or state.get("masked_question", "")
        history = history_of(state)
        reference_bank = resolve_reference_bank(question, history)
        if not is_reference_policy_question(question, reference_bank):
            reference_bank = None
        if fraud_dispute_comparison_question(question):
            question = "盗刷（非本人授权交易）与本人消费后的商户退款争议有什么区别？"
        elif unfamiliar_merchant_name_question(question):
            question = "账单上的陌生商户名称、小额验证扣款或预授权通常是什么原因？"
        elif fraud_general_followup(question, history, state.get("fraud_confirmation_pending")):
            question = "银行卡被盗刷后的一般处理步骤是什么？"
        matched, fee = statement_fee_followup(question, history)
        if matched and not fee:
            return {"answer": "抱歉，我无法确定您指的是哪一项费用，请告诉我费用名称。", "sources": [], "search_query": ""}
        if matched:
            question = f"{fee}是什么？"
        question = annual_fee_followup(question, history) or question
        replacement_question = replacement_fee_stolen_followup(question, history)
        question = replacement_question or question
        if stolen_card_clarification(question, history) == "info":
            question = "银行卡被盗后如何处理？"
        scope_rejected = unsupported_scope_reason(question)
        if scope_rejected and not reference_bank:
            self.last_run = {"generator": "none", "scope_rejected": scope_rejected}
            return {"answer": NOT_FOUND[lang], "sources": [], "search_query": question}

        entities = state.get("entities") or {}

        # 1. 产品和属性都清楚，直接用术语图谱回答
        graph_result = None if reference_bank or _TT_FEE_WITH_STATUS.search(question) else self._answer_from_graph(entities, lang)
        if graph_result:
            self.last_run = {"generator": "graph"}
            self.log_step(self.name, "用术语图谱回答")
            return graph_result

        # 2. 其他情况走 RAG，找到了产品就把产品名加到检索里
        result = self.answer(question, history, lang, entities.get("products"), state.get("sub_intent"), reference_bank)
        if replacement_question and not reference_bank:
            result["answer"] += "\n\n如果您的卡已被盗，请尽快冻结卡片；如需协助冻结，请告诉我。"
        return result

    def _answer_from_graph(self, entities, lang):
        """产品和属性都清楚的话，直接用术语图谱里的值回答；有一个查不到就返回 None，走正常的 RAG"""
        product_ids, attr = entities.get("products"), entities.get("attr")
        if not product_ids or not attr:
            return None
        try:
            results = [get_product_attr(pid, attr) for pid in product_ids]
        except Neo4jError as e:
            self.logger.warning(f"术语图谱查不了，用知识库回答：{e}")
            return None
        if not all(results):
            return None  # 比如"银联卡年费"：银联卡没有年费这一项，交给 RAG

        # 一个产品一行，两个以上产品（PayNow 和 FAST）每行前面加 "- "
        if lang == "en":
            lines = [f"{r['name_en']} for {r['product_en']}: {r['value_en']}" for r in results]
            source = "Source: product data (knowledge graph)"
        else:
            # 产品名是英文结尾的（PayNow）中间加个空格
            lines = [f"{r['product_zh']}{' ' if r['product_zh'][-1].isascii() else ''}的{r['name_zh']}：{r['value_zh']}"
                     for r in results]
            source = "来源：产品资料（术语图谱）"
        if len(lines) > 1:
            lines = [f"- {line}" for line in lines]
        answer = "\n".join(lines) + f"\n\n{source}"

        sources = [{"doc": "neo4j", "title": "术语图谱 / Product graph", "section": r["product_zh"], "rerank_score": None}
                   for r in results]
        return {"answer": answer, "sources": sources, "search_query": ""}

    def answer(self, question, history, lang, products=None, sub_intent=None, reference_bank=None):
        reference_bank = reference_bank or resolve_reference_bank(question, history)
        if not is_reference_policy_question(question, reference_bank):
            reference_bank = None
        # 1~4. 检索
        try:
            rewritten_query, docs, info = self.retrieve(question, history, products, reference_bank)
        except BankAssistantError as e:
            self.logger.error(f"知识库不可用：{e}")
            return {"answer": KB_DOWN[lang], "sources": [], "search_query": question, "error": str(e)}

        # 没找到相关资料，就不生成回答了
        if not docs:
            self.last_run = {**info, "generator": "none"}
            return {"answer": NOT_FOUND[lang], "sources": [], "search_query": rewritten_query}

        if response_utils.is_pin_unblock_question(question):
            passage = response_utils.pin_unblock_source(docs, lang)
            if not passage:
                self.last_run = {**info, "generator": "none"}
                return {"answer": NOT_FOUND[lang], "sources": [], "search_query": rewritten_query}
            answer, doc = passage
            self.last_run = {**info, "generator": "source_text"}
            source = {"doc": doc["doc"], "title": doc["title"], "section": doc["section"],
                      "rerank_score": doc["rerank_score"]}
            source["label"] = self._source_label(doc, lang)
            return {"answer": answer, "sources": [source],
                    "search_query": rewritten_query}

        if sub_intent == "extra_charge_on_statement":
            result = response_utils.statement_charge_answer(docs, lang)
            if result:
                answer, doc = result
                self.last_run = {**info, "generator": "grounded_template"}
                source = {"doc": doc["doc"], "title": doc["title"], "section": doc["section"],
                          "rerank_score": doc["rerank_score"]}
                source["label"] = self._source_label(doc, lang)
                return {"answer": answer, "sources": [source],
                        "search_query": rewritten_query}

        # 5. 生成回答，失败的话直接用资料原文
        start = time.perf_counter()
        response = response_utils.process(rewritten_query, history, docs, lang, reference_bank=reference_bank)
        answer = response["answer"] or (NOT_FOUND[lang] if reference_bank else self._source_text(docs[0], lang))
        if not response["answer"]:
            docs = docs[:1] if not reference_bank else []  # 不直接向客户回显原始参考银行文档
        self.last_run = {**info, "generator": response["generator"], "t_generate": time.perf_counter() - start,
                         "generation_calls": response.get("generation_attempts", 0),
                         "citations_valid": response.get("citations_valid"),
                         "used_sources": response.get("used_sources", [])}
        self.log_step(self.name, f"5. 回答生成完成：{response['generator']}")

        # 6. 只展示生成结果标注为实际依据的来源。
        if response.get("citations_valid"):
            used_indices = response.get("used_sources", [])
        else:
            used_indices = list(range(1, len(docs) + 1))
        if not response["answer"]:
            used_indices = list(range(1, len(docs) + 1))
        used_docs = [docs[index - 1] for index in used_indices if 1 <= index <= len(docs)]
        sources = [{"doc": d["doc"], "title": d["title"], "section": d["section"],
                    "rerank_score": d["rerank_score"], "label": self._source_label(d, lang, reference_bank)}
                   for d in used_docs]
        if reference_bank:
            for source in sources:
                source.update(adapted_from=reference_bank["key"], demo_policy=True)
        return {"answer": answer, "sources": sources, "search_query": rewritten_query}

    def retrieve(self, question, history, products=None, reference_bank=None, strategy=None):
        """检索入口。adaptive 先直检，证据不足才重写/HyDE；legacy 保留旧流程供 A/B 对比。"""
        adaptive = self.config.rag_adaptive_retrieval if strategy is None else strategy == "adaptive"
        if adaptive:
            return self._retrieve_adaptive(question, history, products, reference_bank)
        return self._retrieve_legacy(question, history, products, reference_bank)

    def _retrieve_legacy(self, question, history, products=None, reference_bank=None):
        """返回 (重写后的问题, 保留下来的块, 中间结果)；products 是实体抽取找到的产品 ID"""
        cfg = self.config
        reference_bank = reference_bank or resolve_reference_bank(question, history)
        if not is_reference_policy_question(question, reference_bank):
            reference_bank = None
        info = {}

        # 知识库未覆盖的业务类型直接拒答，避免被相邻账户资料误召回。
        scope_rejected = unsupported_scope_reason(question)
        if scope_rejected and not reference_bank:
            info.update({"rewritten": question, "search_query": question,
                         "scope_rejected": scope_rejected, "best_score": None})
            return question, [], info

        # 1. 问题重写
        start = time.perf_counter()
        if cfg.rag_rewrite:
            rewritten_query = rewrite_query_utils.process(question, history)["rewritten_query"]
        else:
            rewritten_query = question
        info["rewritten"] = rewritten_query
        info["t_rewrite"] = time.perf_counter() - start
        self.log_step(self.name, "1. 问题重写完成")

        # 实体抽取找到了产品，把正式的产品名加到检索的问题里（"汇钱回家" -> 加上 "海外电汇 Overseas TT"）
        # 只用在混合检索；HyDE 和精排还是用客户原来的问题
        search_query = rewritten_query
        if products:
            try:
                names = " ".join(f"{p['name_zh']} {p['name_en']}" for p in get_product_names(products))
                search_query = f"{rewritten_query} {names}"
            except Neo4jError as e:
                self.logger.warning(f"取不到产品名，检索不加产品名：{e}")
        info["search_query"] = search_query

        # 2. 两路检索
        start = time.perf_counter()
        if reference_bank:
            embedding_chunks = search_embedding_utils.process(
                search_query, collection_name=cfg.kb_collection, doc_prefix=reference_bank["doc_prefix"]
            )["embedding_chunks"]
        else:
            embedding_chunks = search_embedding_utils.process(search_query)["embedding_chunks"]
        if not reference_bank and cfg.rag_hyde and not response_utils.is_pin_unblock_question(question):
            hyde_result = hyde_utils.process(rewritten_query)
        else:
            hyde_result = {"hyde_doc": None, "embedding_chunks": []}
        hyde_chunks = hyde_result["embedding_chunks"]
        self.log_step(self.name, f"2. 检索完成：混合检索 {len(embedding_chunks)} 条，HyDE {len(hyde_chunks)} 条")

        # 3. RRF 合并
        rrf_chunks = rrf_utils.process(embedding_chunks, hyde_chunks)["rrf_chunks"]
        info["hyde"] = hyde_result["hyde_doc"]
        info["direct"] = embedding_chunks
        info["fused"] = rrf_chunks
        info["t_search"] = time.perf_counter() - start

        # 4. 精排；最高分太低说明知识库里没有答案
        start = time.perf_counter()
        reranker_result = reranker_utils.process(rewritten_query, rrf_chunks)
        docs = reranker_result["reranker_chunks"]
        best_score = docs[0]["rerank_score"] if docs else None
        if best_score is not None and best_score < cfg.rerank_min_score:
            docs = []
        info["reranked"] = reranker_result["sorted_chunks"]
        info["kept"] = docs
        info["best_score"] = best_score
        info["t_rerank"] = time.perf_counter() - start
        info["used_rewrite"] = rewritten_query != question
        info["used_hyde"] = bool(hyde_result["hyde_doc"])
        self.log_step(self.name, f"4. 精排完成，保留 {len(docs)} 个，最高分 {best_score}")
        return rewritten_query, docs, info

    def _retrieve_adaptive(self, question, history, products=None, reference_bank=None):
        """减少普通问答的模型调用；必要时按上下文重写，再以 HyDE 兜底。"""
        cfg = self.config
        reference_bank = reference_bank or resolve_reference_bank(question, history)
        if not is_reference_policy_question(question, reference_bank):
            reference_bank = None
        scope_rejected = unsupported_scope_reason(question)
        if scope_rejected and not reference_bank:
            return question, [], {"rewritten": question, "search_query": question,
                                  "scope_rejected": scope_rejected, "best_score": None,
                                  "used_rewrite": False, "used_hyde": False}

        info = {"rewritten": question, "used_rewrite": False, "used_hyde": False,
                "used_query_expansion": False}
        search_query = question
        if products:
            try:
                names = " ".join(f"{p['name_zh']} {p['name_en']}" for p in get_product_names(products))
                search_query = f"{question} {names}"
            except Neo4jError as e:
                self.logger.warning(f"取不到产品名，检索不加产品名：{e}")
        info["search_query"] = search_query

        def search(query):
            if reference_bank:
                return search_embedding_utils.process(
                    query, collection_name=cfg.kb_collection, doc_prefix=reference_bank["doc_prefix"]
                )["embedding_chunks"]
            return search_embedding_utils.process(query)["embedding_chunks"]

        def rank(query, chunks):
            result = reranker_utils.process(query, chunks)
            sorted_docs = result["sorted_chunks"]
            docs = result["reranker_chunks"]
            best = sorted_docs[0]["rerank_score"] if sorted_docs else None
            if best is not None and best < cfg.rerank_min_score:
                docs = []
            return docs, sorted_docs, best

        t_search = t_rerank = t_rewrite = 0.0
        started = time.perf_counter()
        direct_chunks = search(search_query)
        t_search += time.perf_counter() - started
        started = time.perf_counter()
        docs, sorted_docs, best_score = rank(question, direct_chunks)
        t_rerank += time.perf_counter() - started
        info.update({"direct": direct_chunks, "fused": direct_chunks, "reranked": sorted_docs,
                     "kept": docs, "best_score": best_score})

        def enough(score, found):
            return bool(found) and score is not None and score >= cfg.rag_direct_min_score

        context_dependent = needs_context_rewrite(question, history)
        if not reference_bank and context_dependent and cfg.rag_rewrite:
            started = time.perf_counter()
            rewritten = rewrite_query_utils.process(question, history)["rewritten_query"]
            t_rewrite += time.perf_counter() - started
            if rewritten and rewritten != question:
                info["used_rewrite"] = True
                info["rewritten"] = rewritten
                info["search_query"] = rewritten
                started = time.perf_counter()
                rewritten_chunks = search(rewritten)
                t_search += time.perf_counter() - started
                started = time.perf_counter()
                rewritten_docs, rewritten_sorted, rewritten_score = rank(rewritten, rewritten_chunks)
                t_rerank += time.perf_counter() - started
                if rewritten_score is not None and (context_dependent or best_score is None or rewritten_score > best_score):
                    docs, sorted_docs, best_score = rewritten_docs, rewritten_sorted, rewritten_score
                    direct_chunks = rewritten_chunks
                    info.update({"direct": rewritten_chunks, "fused": rewritten_chunks})
                info.update({"reranked": sorted_docs, "kept": docs, "best_score": best_score})

        # The FAQ section exists, but colloquial "new card has not arrived" phrasing can
        # miss it in the first-pass candidates. Add the KB's canonical delivery terms
        # before spending another LLM call on HyDE.
        expansion_query = expand_card_delivery_query(info["rewritten"])
        if not reference_bank and expansion_query and not enough(best_score, docs):
            started = time.perf_counter()
            expanded_chunks = search(expansion_query)
            t_search += time.perf_counter() - started
            started = time.perf_counter()
            expanded_docs, expanded_sorted, expanded_score = rank(expansion_query, expanded_chunks)
            t_rerank += time.perf_counter() - started
            if expanded_score is not None and (best_score is None or expanded_score > best_score):
                docs, sorted_docs, best_score = expanded_docs, expanded_sorted, expanded_score
                direct_chunks = expanded_chunks
                info.update({"direct": expanded_chunks, "fused": expanded_chunks,
                             "reranked": sorted_docs, "kept": docs, "best_score": best_score,
                             "search_query": expansion_query, "expanded_query": expansion_query,
                             "used_query_expansion": True})

        if (not enough(best_score, docs) and not reference_bank and cfg.rag_hyde
                and not response_utils.is_pin_unblock_question(question)):
            hyde_query = info["rewritten"]
            started = time.perf_counter()
            hyde_result = hyde_utils.process(hyde_query)
            t_search += time.perf_counter() - started
            info["used_hyde"] = bool(hyde_result["hyde_doc"])
            info["hyde"] = hyde_result["hyde_doc"]
            hyde_chunks = hyde_result["embedding_chunks"]
            if hyde_chunks:
                fused = rrf_utils.process(direct_chunks, hyde_chunks)["rrf_chunks"]
                started = time.perf_counter()
                hyde_docs, hyde_sorted, hyde_score = rank(hyde_query, fused)
                t_rerank += time.perf_counter() - started
                if hyde_score is not None and (best_score is None or hyde_score > best_score):
                    docs, sorted_docs, best_score = hyde_docs, hyde_sorted, hyde_score
                info.update({"hyde_direct": hyde_chunks, "fused": fused, "reranked": sorted_docs,
                             "kept": docs, "best_score": best_score})

        info["t_rewrite"] = t_rewrite
        info["t_search"] = t_search
        info["t_rerank"] = t_rerank
        info["search_query"] = info.get("search_query", search_query)
        self.log_step(self.name, f"自适应检索完成：重写={info['used_rewrite']}，扩展={info['used_query_expansion']}，"
                                  f"HyDE={info['used_hyde']}，保留 {len(docs)} 个，最高分 {best_score}")
        return info["rewritten"], docs, info

    @staticmethod
    def _source_text(doc, lang):
        body = doc["text"].split("\n", 1)[-1]
        return response_utils.split_languages(body)[lang] or body

    @staticmethod
    def _source_label(doc, lang, reference_bank=None):
        line = RagAnswerNode._source_line([doc], lang, reference_bank)
        return line.removeprefix("来源：").removeprefix("Source:").strip()

    @staticmethod
    def _source_line(docs, lang, reference_bank=None):
        names = []
        for d in docs:
            zh_title, _, en_title = d["title"].partition(" / ")
            zh_sec, _, en_sec = d["section"].partition(" / ")
            if lang == "en":
                names.append(f"{en_title or zh_title} – {en_sec or zh_sec}")
            else:
                names.append(f"《{zh_title}》{zh_sec}")
        if reference_bank:
            categories = {
                "bofa": ("账户费用与收费标准", "Account fees and charges"),
                "maybank": ("银行卡交易争议处理", "Card transaction disputes"),
                "standard_chartered": ("银行卡服务与交易条款", "Card services and transaction terms"),
                "citi": ("银行卡交易争议处理", "Card transaction disputes"),
                "deutsche": ("财富管理服务说明", "Wealth management service information"),
                "ubs": ("银行卡安全与交易争议", "Card security and transaction disputes"),
                "dbs": ("电子支付与账户服务", "E-payments and account services"),
                "uob": ("银行卡失卡与争议处理", "Lost-card and transaction dispute handling"),
            }
            category_zh, category_en = categories.get(reference_bank["key"], ("客户服务", "Customer service"))
            return (f"Source: Lion City Bank knowledge base – {category_en}." if lang == "en" else
                    f"来源：狮城银行知识库 · {category_zh}")
        if lang == "en":
            return "Source: " + "; ".join(names)
        return "来源：" + "；".join(names)

    @staticmethod
    def _reference_source_url(doc):
        match = re.search(r"^Official source:\s*(\S+)", doc.get("text", ""), re.MULTILINE)
        return match.group(1) if match else None


if __name__ == "__main__":
    import logging

    from src.schema.base import setup_logging

    setup_logging(logging.WARNING)
    node = RagAnswerNode()
    history = []
    tests = [("How much is the overseas TT fee?", "en"),
             ("那 FAST 呢？", "zh"),
             ("钱汇去中国要等多久", "zh"),
             ("Can I open an account if I'm 15?", "en"),
             ("定期存款利率是多少？", "zh")]
    for q, lang in tests:
        start = time.perf_counter()
        r = node.answer(q, history, lang)
        run = node.last_run
        ms = (time.perf_counter() - start) * 1000
        print(f"\n=== {q} -> 重写：{run.get('rewritten')} ({ms:.0f} ms, {run.get('generator')})")
        print("    精排：", [(c["section"].split(" / ")[0], c["rerank_score"]) for c in run.get("reranked", [])[:4]])
        print(f"    保留：{len(run.get('kept', []))} 个\n{r['answer']}")
        history += [("客户", q), ("客服", r["answer"].split("\n\n")[0])]

    # 2. 实体抽取找到了产品和属性，走 process()，和主图里一样
    from src.schema.state import create_default_state

    print("\n===== 术语图谱")
    for q, lang, entities in [
        ("汇钱回家要几天？", "zh", {"products": ["overseas_tt"], "attr": "arrival_time"}),
        ("What's the annual fee for the credit card?", "en", {"products": ["mastercard_credit"], "attr": "annual_fee"}),
        ("PayNow 和 FAST 哪个限额高", "zh", {"products": ["paynow", "fast"], "attr": "daily_limit"}),
        ("银联卡年费多少", "zh", {"products": ["unionpay_debit"], "attr": "annual_fee"}),  # 图谱里没有，走 RAG
        ("汇钱回家安全吗？", "zh", {"products": ["overseas_tt"]}),  # 没有属性，走 RAG，检索加上产品名
    ]:
        start = time.perf_counter()
        r = node.process(create_default_state(masked_question=q, lang=lang, entities=entities))
        ms = (time.perf_counter() - start) * 1000
        run = node.last_run
        print(f"\n=== {q} ({ms:.0f} ms, {run.get('generator')})")
        if run.get("search_query"):
            print(f"    检索用的问题：{run['search_query']}")
        print(r["answer"])
