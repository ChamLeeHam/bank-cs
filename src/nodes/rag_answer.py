"""
知识库问答节点（《需求文档》F2 / P4）：Milvus 混合检索 + LLM 生成 + 引用来源 + 数字核对

流程：
  1. 检索：BGE-M3 稠密向量 + BM25，RRF 融合，取 top_k 个章节；与本轮二级意图对应的章节排在前面（本地完成）
  2. 生成：只能根据检索到的资料回答。RAG_GENERATOR 选择生成模型：
       deepseek（默认）：DeepSeek API。只发送脱敏后的问题和公开的 FAQ 片段，不发送客户 ID、账户数据和对话历史
       local：本地 Qwen2.5-1.5B-Instruct（与意图模型共用权重，关闭 LoRA），数据完全不出本机，但条件判断偶尔出错
     DeepSeek 失败时自动改用本地 Qwen
  3. 核对（防幻觉）：回答中出现的每个数字都必须在资料中出现过，否则改用资料原文回答
  4. 引用：来源由代码追加（不依赖模型自己写引用），格式"来源：《费用与汇率》海外电汇（TT）手续费"
  5. 降级：生成模型都不可用 -> 直接返回最相关章节的原文；知识库不可用 -> 道歉并建议转人工

单独测试（在项目根目录）：
    uv run python -m src.nodes.rag_answer
"""
import re
from src.schema.base import BaseNode, StateUpdate
from src.schema.config import get_config
from src.schema.exceptions import BankAssistantError, LLMError
from src.schema.state import GraphState

_NUMBER = re.compile(r"\d+(?:[.,]\d+)*")
_CJK = re.compile(r"[一-鿿]")

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


_LIST_MARKER = re.compile(r"^\s*(?:\d+[.、)）]|\[\d+\])\s*", re.MULTILINE)  # "1. " "2、" "[1]" 这类编号不算数字


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


class DeepSeekGenerator:
    """DeepSeek（OpenAI 兼容接口），与 Day 2 生成数据使用同一组配置"""

    def __init__(self):
        from langchain_openai import ChatOpenAI

        config = get_config()
        if not config.deepseek_api_key:
            raise LLMError("缺少 DEEPSEEK_API_KEY", "rag_answer")
        self.llm = ChatOpenAI(model=config.deepseek_model, api_key=config.deepseek_api_key,
                              base_url=config.deepseek_base_url, temperature=0.2, timeout=30, max_retries=1)

    def chat(self, messages: list[dict]) -> str:
        return str(self.llm.invoke(messages).content).strip()


class RagAnswerNode(BaseNode):
    name = "rag_answer"

    def __init__(self, generator=None):
        super().__init__()
        self.injected = generator  # 测试时可以注入生成器
        self.generators: list | None = None  # 按顺序尝试：DeepSeek（如果启用）-> 本地 Qwen
        self.generator_used = ""

    def _generators(self) -> list:
        if self.injected is not None:
            return [("injected", self.injected)]
        if self.generators is None:
            self.generators = []
            if self.config.rag_generator == "deepseek":
                try:
                    self.generators.append(("deepseek", DeepSeekGenerator()))
                except Exception as e:
                    self.logger.warning("DeepSeek 不可用，改用本地 Qwen：%s", e)
            try:
                from src.nodes.intent_router import IntentModel
                self.generators.append(("local", IntentModel.get()))
            except Exception as e:
                self.logger.warning("本地 Qwen 不可用：%s", e)
        return self.generators

    def _retrieve(self, query: str, sub_intent: str) -> list[dict]:
        from src.rag.embedder import BgeM3Embedder
        from src.rag.kb_store import get_client, hybrid_search

        vector = BgeM3Embedder.get().encode([query])[0]
        hits = hybrid_search(get_client(), query, vector, top_k=self.config.kb_top_k)
        # 二级意图对应的章节排到最前（稳定排序，其余保持 RRF 顺序）
        return sorted(hits, key=lambda h: sub_intent not in (h.get("sub_intents") or []))

    def _generate(self, question: str, docs: list[dict], lang: str) -> str | None:
        """依次尝试各个生成模型；全部失败返回 None（调用方改用资料原文）"""
        context = "\n\n".join(f"[{i}] {d['section']}\n{split_languages(d['text'])[lang] or d['text']}"
                              for i, d in enumerate(docs, 1))
        # 只发送脱敏后的问题（masked_question）和公开的 FAQ 片段
        messages = [{"role": "system", "content": SYSTEM_PROMPT[lang]},
                    {"role": "user", "content": f"<资料>\n{context}\n</资料>\n\n客户问题：{question}"}]
        for name, generator in self._generators():
            try:
                answer = generator.chat(messages)
                self.generator_used = name
                return answer
            except Exception as e:
                self.logger.warning("生成模型 %s 失败，尝试下一个：%s", name, e)
        self.generator_used = "source_text"
        return None

    def process(self, state: GraphState) -> StateUpdate:
        question = state.get("masked_question", "")
        lang = "en" if state.get("lang") == "en" else "zh"  # 中英混杂用中文回答

        try:
            hits = self._retrieve(question, state.get("sub_intent", ""))
        except BankAssistantError as e:  # Milvus / 向量模型不可用：不编答案，建议转人工
            self.logger.error("知识库不可用：%s", e)
            return {"answer": "抱歉，知识库暂时无法使用，请稍后再试，或回复“转人工”联系人工客服。" if lang == "zh"
                    else "Sorry, the knowledge base is unavailable right now. Please try again later or ask for a human agent.",
                    "sources": [], "search_query": question, "error": str(e)}

        sources = [{k: h[k] for k in ("doc", "title", "section", "score")} for h in hits]
        if not hits:
            return {"answer": "抱歉，我没有找到相关资料，建议您转人工客服。" if lang == "zh"
                    else "Sorry, I couldn't find this in our documents. You may want to ask a human agent.",
                    "sources": [], "search_query": question}

        answer = self._generate(question, hits, lang)
        source_text = "\n".join(h["text"] for h in hits)
        if answer:
            invented = numbers_in(answer) - numbers_in(source_text) - numbers_in(question)
            if invented:  # 回答里有资料中不存在的数字：可能是编造的，改用原文
                self.logger.warning("回答中有资料里没有的数字 %s，改用资料原文", sorted(invented))
                answer = None
                self.generator_used = "source_text"
        if not answer:
            best = hits[0]
            body = best["text"].split("\n", 1)[1] if "\n" in best["text"] else best["text"]
            answer = split_languages(body)[lang] or body

        return {"answer": f"{answer}\n\n{source_line(hits[:2], lang)}", "sources": sources, "search_query": question}


if __name__ == "__main__":
    import logging
    import time

    from src.schema.base import setup_logging

    setup_logging(logging.WARNING)
    node = RagAnswerNode()
    for q, lang, sub in [("How much is the overseas TT fee?", "en", "transfer_fee_charged"),
                         ("汇款到中国一般要几天？", "zh", "transfer_timing"),
                         ("我的 PIN 被 lock 了，怎么 unlock？", "mixed", "pin_blocked"),
                         ("Can I open an account if I'm 15?", "en", "age_limit"),
                         ("你们支持比特币吗？", "zh", "fiat_currency_support")]:
        start = time.perf_counter()
        r = node.process({"masked_question": q, "lang": lang, "sub_intent": sub})
        print(f"\n=== {q}  ({(time.perf_counter() - start) * 1000:.0f} ms)\n{r['answer']}")
