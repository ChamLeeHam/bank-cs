# 知识库问答节点
# 流程：问题重写 -> 混合检索 + HyDE 检索 -> RRF 合并 -> reranker 精排 -> 生成回答
# 每一步都在 src/utils 里，这个节点只负责按顺序调用
import time

from src.schema.base import BaseNode, StateUpdate
from src.schema.exceptions import BankAssistantError
from src.schema.state import GraphState
from src.utils import (hyde_utils, reranker_utils, response_utils, rewrite_query_utils, rrf_utils,
                       search_embedding_utils)
from src.utils.history_utils import history_of

NOT_FOUND = {
    "zh": "抱歉，我们的资料中没有这个问题的答案。如需进一步帮助，可以回复“转人工”联系人工客服。",
    "en": "Sorry, I couldn't find the answer to this in our documents. If you need more help, ask for a human agent.",
}
KB_DOWN = {
    "zh": "抱歉，知识库暂时无法使用，请稍后再试，或回复“转人工”联系人工客服。",
    "en": "Sorry, the knowledge base is unavailable right now. Please try again later or ask for a human agent.",
}


class RagAnswerNode(BaseNode):
    name = "rag_answer"

    def __init__(self):
        super().__init__()
        self.last_run = {}  # 上一次的中间结果，评估脚本会用到

    def process(self, state: GraphState) -> StateUpdate:
        self.log_step(self.name, "开始执行知识库问答")
        lang = "en" if state.get("lang") == "en" else "zh"  # 中英混杂的用中文回答
        return self.answer(state.get("masked_question", ""), history_of(state), lang)

    def answer(self, question, history, lang):
        # 1~4. 检索
        try:
            rewritten_query, docs, info = self.retrieve(question, history)
        except BankAssistantError as e:
            self.logger.error(f"知识库不可用：{e}")
            return {"answer": KB_DOWN[lang], "sources": [], "search_query": question, "error": str(e)}

        # 没找到相关资料，就不生成回答了
        if not docs:
            self.last_run = {**info, "generator": "none"}
            return {"answer": NOT_FOUND[lang], "sources": [], "search_query": rewritten_query}

        # 5. 生成回答，失败的话直接用资料原文
        start = time.perf_counter()
        response = response_utils.process(rewritten_query, history, docs, lang)
        answer = response["answer"] or self._source_text(docs[0], lang)
        self.last_run = {**info, "generator": response["generator"], "t_generate": time.perf_counter() - start}
        self.log_step(self.name, f"5. 回答生成完成：{response['generator']}")

        # 6. 加上来源
        sources = [{"doc": d["doc"], "title": d["title"], "section": d["section"], "rerank_score": d["rerank_score"]}
                   for d in docs]
        answer = f"{answer}\n\n{self._source_line(docs, lang)}"
        return {"answer": answer, "sources": sources, "search_query": rewritten_query}

    def retrieve(self, question, history):
        """返回 (重写后的问题, 保留下来的块, 中间结果)"""
        cfg = self.config
        info = {}

        # 1. 问题重写
        start = time.perf_counter()
        if cfg.rag_rewrite:
            rewritten_query = rewrite_query_utils.process(question, history)["rewritten_query"]
        else:
            rewritten_query = question
        info["rewritten"] = rewritten_query
        info["t_rewrite"] = time.perf_counter() - start
        self.log_step(self.name, "1. 问题重写完成")

        # 2. 两路检索
        start = time.perf_counter()
        embedding_chunks = search_embedding_utils.process(rewritten_query)["embedding_chunks"]
        if cfg.rag_hyde:
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
        self.log_step(self.name, f"4. 精排完成，保留 {len(docs)} 个，最高分 {best_score}")
        return rewritten_query, docs, info

    @staticmethod
    def _source_text(doc, lang):
        body = doc["text"].split("\n", 1)[-1]
        return response_utils.split_languages(body)[lang] or body

    @staticmethod
    def _source_line(docs, lang):
        names = []
        for d in docs:
            zh_title, _, en_title = d["title"].partition(" / ")
            zh_sec, _, en_sec = d["section"].partition(" / ")
            if lang == "en":
                names.append(f"{en_title or zh_title} – {en_sec or zh_sec}")
            else:
                names.append(f"《{zh_title}》{zh_sec}")
        if lang == "en":
            return "Source: " + "; ".join(names)
        return "来源：" + "；".join(names)


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
