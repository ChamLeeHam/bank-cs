# 知识库问答节点（《需求文档》F2 / P4），流程参考课程 03-rag 的 invoke_rag：
#   问题重写 -> 混合检索 + HyDE 检索 -> RRF 融合 -> reranker 精排 -> 生成回答
# 每一步都在 src/utils/ 中，接口与课程一致：process(...) 返回字典
#   rewrite_query_utils  -> {"rewritten_query"}      search_embedding_utils -> {"embedding_chunks"}
#   hyde_utils           -> {"embedding_chunks"}      rrf_utils              -> {"rrf_chunks"}
#   reranker_utils       -> {"reranker_chunks"}       response_utils         -> {"answer"}
# 本节点额外做：知识库中没有答案时不生成、不列来源；回答由代码追加引用来源
import time

from src.schema.base import BaseNode, StateUpdate
from src.schema.exceptions import BankAssistantError
from src.schema.state import GraphState
from src.utils import (hyde_utils, reranker_utils, response_utils, rewrite_query_utils, rrf_utils,
                       search_embedding_utils)
from src.utils.history_utils import history_of

NOT_FOUND = {"zh": "抱歉，我们的资料中没有这个问题的答案。如需进一步帮助，可以回复“转人工”联系人工客服。",
             "en": "Sorry, I couldn't find the answer to this in our documents. If you need more help, ask for a human agent."}
KB_DOWN = {"zh": "抱歉，知识库暂时无法使用，请稍后再试，或回复“转人工”联系人工客服。",
           "en": "Sorry, the knowledge base is unavailable right now. Please try again later or ask for a human agent."}


class RagAnswerNode(BaseNode):
    name = "rag_answer"

    def __init__(self):
        super().__init__()
        self.last_run: dict = {}  # 最近一次的中间结果（重写后的问题、各路命中、精排分数），评估和调试用

    def process(self, state: GraphState) -> StateUpdate:
        self.log_step(self.name, "知识库问答节点开始执行")
        lang = "en" if state.get("lang") == "en" else "zh"  # 中英混杂用中文回答
        return self.answer(state.get("masked_question", ""), history_of(state), lang)

    def answer(self, question: str, history: list[tuple[str, str]], lang: str) -> StateUpdate:
        # 1~4. 检索：问题重写 -> 两路检索 -> RRF -> 精排
        try:
            rewritten_query, docs, info = self.retrieve(question, history)
        except BankAssistantError as e:  # Milvus / 向量模型不可用：不编答案，建议转人工
            self.logger.error(f"知识库不可用：{e}")
            return {"answer": KB_DOWN[lang], "sources": [], "search_query": question, "error": str(e)}

        # 知识库中没有答案：不生成、不列来源
        if not docs:
            self.last_run = {**info, "generator": "none"}
            self.log_step(self.name, "知识库中没有相关资料")
            return {"answer": NOT_FOUND[lang], "sources": [], "search_query": rewritten_query}

        # 5. 生成回答（失败或数字核对不通过时，改用最相关章节的原文）
        start = time.perf_counter()
        response = response_utils.process(rewritten_query, history, docs, lang)
        answer = response["answer"] or self._source_text(docs[0], lang)
        self.last_run = {**info, "generator": response["generator"], "t_generate": time.perf_counter() - start}
        self.log_step(self.name, f"5. 回答完成，生成方式：{response['generator']}")

        # 6. 追加引用来源
        sources = [{k: d.get(k) for k in ("doc", "title", "section", "rerank_score")} for d in docs]
        return {"answer": f"{answer}\n\n{self._source_line(docs, lang)}", "sources": sources,
                "search_query": rewritten_query}

    def retrieve(self, question: str, history: list[tuple[str, str]]) -> tuple[str, list[dict], dict]:
        """返回 (重写后的问题, 精排后保留的章节, 中间结果)"""
        cfg, info = self.config, {}

        # 1. 问题重写（指代消解）
        start = time.perf_counter()
        rewritten_query = rewrite_query_utils.process(question, history)["rewritten_query"] if cfg.rag_rewrite \
            else question
        info.update(rewritten=rewritten_query, t_rewrite=time.perf_counter() - start)
        self.log_step(self.name, "1. 问题重写完成")

        # 2. 双路检索：混合检索 + HyDE 检索
        start = time.perf_counter()
        embedding_chunks = search_embedding_utils.process(rewritten_query)["embedding_chunks"]
        hyde_result = hyde_utils.process(rewritten_query) if cfg.rag_hyde else {"hyde_doc": None, "embedding_chunks": []}
        hyde_chunks = hyde_result["embedding_chunks"]
        self.log_step(self.name, f"2. 检索完成：混合检索 {len(embedding_chunks)} 条，HyDE 检索 {len(hyde_chunks)} 条")

        # 3. RRF 融合
        rrf_chunks = rrf_utils.process(embedding_chunks, hyde_chunks)["rrf_chunks"]
        info.update(hyde=hyde_result["hyde_doc"], direct=embedding_chunks, fused=rrf_chunks,
                    t_search=time.perf_counter() - start)

        # 4. reranker 精排 + 断崖截断；最高分太低视为知识库中没有答案
        start = time.perf_counter()
        reranker_result = reranker_utils.process(rewritten_query, rrf_chunks)
        docs = reranker_result["reranker_chunks"]
        best_score = docs[0]["rerank_score"] if docs else None
        if best_score is not None and best_score < cfg.rerank_min_score:
            docs = []
        info.update(reranked=reranker_result["sorted_chunks"], kept=docs, best_score=best_score,
                    t_rerank=time.perf_counter() - start)
        self.log_step(self.name, f"4. 精排完成，保留 {len(docs)} 个章节，最高分 {best_score}")
        return rewritten_query, docs, info

    @staticmethod
    def _source_text(doc: dict, lang: str) -> str:
        body = doc["text"].split("\n", 1)[-1]
        return response_utils.split_languages(body)[lang] or body

    @staticmethod
    def _source_line(docs: list[dict], lang: str) -> str:
        names = []
        for d in docs:
            zh_title, _, en_title = d["title"].partition(" / ")
            zh_sec, _, en_sec = d["section"].partition(" / ")
            names.append(f"《{zh_title}》{zh_sec}" if lang != "en" else f"{en_title or zh_title} – {en_sec or zh_sec}")
        return ("来源：" + "；".join(names)) if lang != "en" else ("Source: " + "; ".join(names))


if __name__ == "__main__":
    import logging

    from src.schema.base import setup_logging

    setup_logging(logging.WARNING)
    node = RagAnswerNode()
    history: list[tuple[str, str]] = []
    for q, lang in [("How much is the overseas TT fee?", "en"),
                    ("那 FAST 呢？", "zh"),  # 依赖上一轮：问题重写
                    ("钱汇去中国要等多久", "zh"),
                    ("Can I open an account if I'm 15?", "en"),
                    ("定期存款利率是多少？", "zh")]:  # 知识库中没有
        start = time.perf_counter()
        r = node.answer(q, history, lang)
        run = node.last_run
        print(f"\n=== {q}  ->  重写：{run.get('rewritten')}  ({(time.perf_counter() - start) * 1000:.0f} ms, "
              f"{run.get('generator')})")
        print(f"    精排：{[(c['section'].split(' / ')[0], c['rerank_score']) for c in run.get('reranked', [])[:4]]}")
        print(f"    保留：{len(run.get('kept', []))} 个章节\n{r['answer']}")
        history += [("客户", q), ("客服", r["answer"].split("\n\n")[0])]
