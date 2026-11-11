"""
知识库问答节点（《需求文档》F2 / P4），流程结构参考课程 03-rag：

    问题重写 -> 混合检索（BGE-M3 稠密 + BM25）─┐
                                              ├-> 加权 RRF 融合 -> bge-reranker 精排 + 断崖截断 -> 生成回答 -> 数字核对 + 引用
            HyDE 检索（问题 + 假设答案）───────┘

  1. 问题重写（src/utils/rewrite_query_utils.py）：结合最近的对话，把"那 FAST 呢？"改成完整问题；第一轮不调用
  2. 两路检索（src/utils/milvus_utils.py、hyde_utils.py）：每路都是 Milvus 混合检索（稠密向量 + BM25，Milvus 内部 RRF）
  3. 加权 RRF（src/utils/rrf_utils.py）：问题检索权重 1.0，HyDE 检索权重 0.7
  4. 精排（src/utils/bge_rerank_utils.py）：bge-reranker-base 打分，断崖算法保留 1–3 个章节；
     最高分低于 BANK_RERANK_MIN_SCORE 时视为知识库中没有答案：不生成、不列来源，建议转人工
  5. 生成：只能根据保留的章节回答。RAG_GENERATOR=deepseek（默认，只发送脱敏后的问题 / 对话和公开 FAQ）或 local
     DeepSeek 失败时改用本地 Qwen，再失败返回资料原文
  6. 核对与引用：回答中每个数字都必须在资料或问题中出现过；来源由代码追加，不依赖模型

单独测试（在项目根目录）：
    uv run python -m src.nodes.rag_answer
"""
import re
import time

from src.schema.base import BaseNode, StateUpdate
from src.schema.exceptions import BankAssistantError
from src.schema.state import GraphState
from src.utils.history_utils import format_history, history_of
from src.utils.rrf_utils import weighted_rrf

_NUMBER = re.compile(r"\d+(?:[.,]\d+)*")
_CJK = re.compile(r"[一-鿿]")
_LIST_MARKER = re.compile(r"^\s*(?:\d+[.、)）]|\[\d+\])\s*", re.MULTILINE)  # "1. " "2、" "[1]" 这类编号不算数字

SYSTEM_PROMPT = {
    "zh": ("你是狮城银行的客服助手。只能根据<资料>回答客户的问题：\n"
           "- 如果问题涉及条件（年龄、金额、限额、时间），先把客户的情况和资料中的条件逐一比较，再给出结论\n"
           "- 资料中没有的信息，直接说不确定，并建议客户转人工客服，不要猜测或编造\n"
           "- 金额、费用、天数必须与资料完全一致\n"
           "- 用简体中文回答，2 到 4 句话，直接回答问题，不要重复问题\n"
           "- 不要提供投资建议，不要提及资料编号"),
    "en": ("You are Lion City Bank's customer service assistant. Answer only from the <documents>:\n"
           "- If the question involves a condition (age, amount, limit, time), compare the customer's situation with "
           "the condition in the documents first, then give the conclusion\n"
           "- If the documents do not contain the answer, say you are not sure and suggest a human agent; never guess\n"
           "- Amounts, fees and days must match the documents exactly\n"
           "- Answer in English in 2 to 4 sentences, directly, without repeating the question\n"
           "- Do not give investment advice and do not mention document numbers"),
}
NOT_FOUND = {"zh": "抱歉，我们的资料中没有这个问题的答案。如需进一步帮助，可以回复“转人工”联系人工客服。",
             "en": "Sorry, I couldn't find the answer to this in our documents. If you need more help, ask for a human agent."}
KB_DOWN = {"zh": "抱歉，知识库暂时无法使用，请稍后再试，或回复“转人工”联系人工客服。",
           "en": "Sorry, the knowledge base is unavailable right now. Please try again later or ask for a human agent."}


def numbers_in(text: str) -> set[str]:
    return {n.replace(",", "") for n in _NUMBER.findall(_LIST_MARKER.sub("", text))}


def split_languages(body: str) -> dict[str, str]:
    """知识库每个章节是"中文段落 + 英文段落"，按是否含汉字分开"""
    paras = [p.strip() for p in body.split("\n\n") if p.strip()]
    return {"zh": "\n".join(p for p in paras if _CJK.search(p)),
            "en": "\n".join(p for p in paras if not _CJK.search(p))}


def source_line(hits: list[dict], lang: str) -> str:
    names = []
    for h in hits:
        zh_title, _, en_title = h["title"].partition(" / ")
        zh_sec, _, en_sec = h["section"].partition(" / ")
        names.append(f"《{zh_title}》{zh_sec}" if lang != "en" else f"{en_title or zh_title} – {en_sec or zh_sec}")
    return ("来源：" + "；".join(names)) if lang != "en" else ("Source: " + "; ".join(names))


class RagAnswerNode(BaseNode):
    name = "rag_answer"

    def __init__(self, generator=None):
        super().__init__()
        self.injected = generator  # 测试时可以注入生成器
        self.generators: list | None = None  # 按顺序尝试：DeepSeek（如果启用）-> 本地 Qwen
        self.last_run: dict = {}  # 最近一次的中间结果（重写后的问题、各路命中、精排分数、用时），评估和调试用

    # ---------- 1~4：检索 ----------
    def retrieve(self, question: str, history: list[tuple[str, str]]) -> tuple[str, list[dict], dict]:
        """问题重写 -> 两路检索 -> RRF -> 精排 + 断崖截断；返回 (重写后的问题, 保留的章节, 中间结果)"""
        from src.utils.bge_m3_embedding_utils import generate_dense_embedding
        from src.utils.bge_rerank_utils import cliff_cutoff, rerank
        from src.utils.hyde_utils import hyde_document
        from src.utils.milvus_utils import get_milvus_client, hybrid_search
        from src.utils.rewrite_query_utils import rewrite_query

        cfg, info, t = self.config, {}, time.perf_counter()
        query = rewrite_query(question, history) if cfg.rag_rewrite else question
        info["rewritten"], info["t_rewrite"] = query, time.perf_counter() - t

        client = get_milvus_client()
        t = time.perf_counter()
        direct = hybrid_search(client, query, generate_dense_embedding([query])[0], top_k=cfg.rag_candidates)
        ranked = [(direct, 1.0)]
        hyde = hyde_document(query) if cfg.rag_hyde else None
        if hyde:
            hyde_query = f"{query}\n{hyde}"
            ranked.append((hybrid_search(client, hyde_query, generate_dense_embedding([hyde_query])[0],
                                         top_k=cfg.rag_candidates), cfg.hyde_weight))
        fused = weighted_rrf(ranked, max_result=cfg.rag_candidates)
        info.update(hyde=hyde, direct=direct, fused=fused, t_search=time.perf_counter() - t)

        t = time.perf_counter()
        reranked = rerank(query, fused)
        kept = cliff_cutoff(reranked)
        best = kept[0]["rerank_score"] if kept else None
        if best is not None and best < cfg.rerank_min_score:  # 最相关的章节分数也很低：知识库中没有答案
            kept = []
        info.update(reranked=reranked, kept=kept, best_score=best, t_rerank=time.perf_counter() - t)
        return query, kept, info

    # ---------- 5：生成 ----------
    def _generators(self) -> list:
        if self.injected is not None:
            return [("injected", self.injected)]
        if self.generators is None:
            self.generators = []
            if self.config.rag_generator == "deepseek":
                try:
                    from src.utils.llm_util import DeepSeekGenerator
                    self.generators.append(("deepseek", DeepSeekGenerator()))
                except Exception as e:
                    self.logger.warning("DeepSeek 不可用，改用本地 Qwen：%s", e)
            try:
                from src.nodes.intent_router import IntentModel
                self.generators.append(("local", IntentModel.get()))
            except Exception as e:
                self.logger.warning("本地 Qwen 不可用：%s", e)
        return self.generators

    def _generate(self, question: str, docs: list[dict], history: list[tuple[str, str]], lang: str) -> tuple[str | None, str]:
        context = "\n\n".join(f"[{i}] {d['section']}\n{split_languages(d['text'])[lang] or d['text']}"
                              for i, d in enumerate(docs, 1))
        recent = format_history(history)  # 课程做法：带上最近的对话
        user = f"<资料>\n{context}\n</资料>\n\n" + (f"<历史对话>\n{recent}\n</历史对话>\n\n" if recent else "") + \
               f"客户问题：{question}"
        messages = [{"role": "system", "content": SYSTEM_PROMPT[lang]}, {"role": "user", "content": user}]
        for name, generator in self._generators():
            try:
                return generator.chat(messages), name
            except Exception as e:
                self.logger.warning("生成模型 %s 失败，尝试下一个：%s", name, e)
        return None, "source_text"

    def answer(self, question: str, history: list[tuple[str, str]], lang: str) -> StateUpdate:
        try:
            query, kept, info = self.retrieve(question, history)
        except BankAssistantError as e:  # Milvus / 向量模型不可用：不编答案，建议转人工
            self.logger.error("知识库不可用：%s", e)
            return {"answer": KB_DOWN[lang], "sources": [], "search_query": question, "error": str(e)}

        sources = [{k: h.get(k) for k in ("doc", "title", "section", "rerank_score")} for h in kept]
        if not kept:
            self.last_run = {**info, "generator": "none"}
            return {"answer": NOT_FOUND[lang], "sources": [], "search_query": query}

        t = time.perf_counter()
        answer, generator = self._generate(query, kept, history, lang)
        if answer:
            invented = numbers_in(answer) - numbers_in("\n".join(h["text"] for h in kept)) - numbers_in(question)
            if invented:  # 回答里有资料中不存在的数字：可能是编造的，改用原文
                self.logger.warning("回答中有资料里没有的数字 %s，改用资料原文", sorted(invented))
                answer, generator = None, "source_text"
        if not answer:
            body = kept[0]["text"].split("\n", 1)[-1]
            answer = split_languages(body)[lang] or body
        self.last_run = {**info, "generator": generator, "t_generate": time.perf_counter() - t}
        return {"answer": f"{answer}\n\n{source_line(kept, lang)}", "sources": sources, "search_query": query}

    def process(self, state: GraphState) -> StateUpdate:
        lang = "en" if state.get("lang") == "en" else "zh"  # 中英混杂用中文回答
        return self.answer(state.get("masked_question", ""), history_of(state), lang)


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
