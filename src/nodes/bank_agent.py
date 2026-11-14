# Bank Agent：DeepSeek 根据客户的问题自己决定调哪些工具、调几次
# 外面的 LangGraph 还是安全和规则这一层：脱敏、意图、转人工、拒答都在 Agent 前面做完
# 出错（比如 DeepSeek 连不上）就退回原来的 rag_answer
# 在项目根目录运行测试：uv run python -m src.nodes.bank_agent
import re
import time

from langchain.agents import create_agent
from langchain_core.messages import AIMessage, ToolMessage

from src.nodes.agent_tools import faq_source_id, get_product_info, search_faq
from src.nodes.rag_answer import RagAnswerNode
from src.schema.base import BaseNode, StateUpdate
from src.schema.state import GraphState
from src.utils.llm_util import get_llm_client
from src.utils.response_utils import _has_invented_numbers, is_pin_unblock_question

TOOLS = [search_faq, get_product_info]
MAX_HISTORY = 6  # 最近几条消息给 Agent
GRAPH_SOURCE = {"doc": "neo4j", "title": "术语图谱 / Product graph", "section": "", "rerank_score": None}

SYSTEM_PROMPT = """你是狮城银行（Lion City Bank）的 AI 客服。
1. 只根据工具查到的资料回答，资料里没有的不要自己编，特别是数字
2. 问具体产品的手续费、限额、到账时间、年费，先用 get_product_info；其他问题用 search_faq
3. 只查询客户实际问的内容；先用一次检索，资料足够回答就停止，不要自行增加费用、时间或其他问题
   只有客户问了多件事，或者已有资料不足以回答客户的问题，才继续调用工具
4. 客户用英文就用英文回答，中文或者中英混杂就用中文回答
5. 只回答客户问的内容，不要加入无关的情况；回答简短清楚，不用写"根据资料"这种话，不用自己写来源名称
6. 使用 FAQ 资料回答时，在回答末尾原样写出实际使用的每个章节编号，例如 [FAQ:123456abcdef]；不要写没有用到的编号
7. 不提供投资建议"""

FAQ_CITATION = re.compile(r"\[FAQ:([0-9a-f]{12})\]", re.IGNORECASE)

bank_agent = None


# 1. 获取 Agent
def get_bank_agent():
    global bank_agent
    if bank_agent is None:
        bank_agent = create_agent(model=get_llm_client(), tools=TOOLS, system_prompt=SYSTEM_PROMPT)
    return bank_agent


class BankAgentNode(BaseNode):
    name = "bank_agent"

    def __init__(self):
        super().__init__()
        self.fallback = RagAnswerNode()
        self.last_run = {}  # 这一次调了哪些工具，测试和评估用

    def process(self, state: GraphState) -> StateUpdate:
        lang = "en" if state.get("lang") == "en" else "zh"
        if is_pin_unblock_question(state.get("masked_question", "")):
            return self.fallback.process(state)
        # state["messages"] 里已经有这一轮的问题了（input_guard 加的），而且是脱敏过的
        messages = (state.get("messages") or [])[-MAX_HISTORY:]
        try:
            # recursion_limit：Agent 最多走这么多步，防止一直调工具停不下来
            result = get_bank_agent().invoke({"messages": messages}, config={"recursion_limit": 12})
        except Exception as e:
            self.logger.error(f"Agent 出错，用原来的 RAG 回答：{e}")
            self.last_run = {"fallback": "error"}
            return self.fallback.process(state)

        # 只看这一轮新加的消息（前面的是历史）
        new_messages = result["messages"][len(messages):]
        ai_messages = [m for m in new_messages if isinstance(m, AIMessage)]
        tool_results = [m for m in new_messages if isinstance(m, ToolMessage)]
        answer = str(ai_messages[-1].content).strip() if ai_messages else ""
        tool_calls = [{"tool": c["name"], "args": c["args"]} for m in ai_messages for c in m.tool_calls]

        # search_faq 的 artifact 是 FAQ 章节；get_product_info 的是查到资料的产品，查到了才加"产品资料"
        faq_sources = []
        seen = set()
        for m in tool_results:
            if m.name == "search_faq" and m.artifact:
                for source in m.artifact:
                    key = (source["doc"], source["title"], source["section"])
                    if key not in seen:
                        seen.add(key)
                        faq_sources.append(source)
        cited = {source_id.lower() for source_id in FAQ_CITATION.findall(answer)}
        answer = FAQ_CITATION.sub("", answer).strip()
        selected = [s for s in faq_sources if faq_source_id(s["doc"], s["section"]) in cited]
        used_graph = any(m.name == "get_product_info" and m.artifact for m in tool_results)
        if faq_sources and not selected and not used_graph:
            self.logger.warning("Agent 没有标明有效的 FAQ 来源，用原来的 RAG 回答")
            self.last_run = {"tool_calls": tool_calls, "fallback": "sources"}
            return self.fallback.process(state)
        faq_sources = selected
        sources = faq_sources + ([GRAPH_SOURCE] if used_graph else [])

        # 没有回答，或者回答里的数字不在工具查到的资料里，就用原来的 RAG（和 response_utils 里的检查一样）
        docs = [{"text": str(m.content)} for m in tool_results]
        if not answer or _has_invented_numbers(answer, docs, state.get("masked_question", "")):
            self.logger.warning("Agent 没有回答，或者回答里有资料中没有的数字，用原来的 RAG 回答")
            self.last_run = {"tool_calls": tool_calls, "fallback": "numbers"}
            return self.fallback.process(state)

        self.last_run = {"tool_calls": tool_calls, "sources": sources}
        source_line = self._source_line(faq_sources, used_graph, lang)
        if source_line:
            answer = f"{answer}\n\n{source_line}"
        return {"answer": answer, "sources": sources, "tool_calls": tool_calls}

    @staticmethod
    def _source_line(faq_sources, used_graph, lang):
        """FAQ 的来源和原来的 RAG 写法一样，图谱的写"产品资料" """
        parts = []
        if faq_sources:
            line = RagAnswerNode._source_line(faq_sources, lang)
            parts.append(line.split("：", 1)[-1] if lang != "en" else line.split(": ", 1)[-1])
        if used_graph:
            parts.append("product data (knowledge graph)" if lang == "en" else "产品资料（术语图谱）")
        if not parts:
            return ""
        return ("Source: " + "; ".join(parts)) if lang == "en" else ("来源：" + "；".join(parts))


if __name__ == "__main__":
    import logging

    from langchain_core.messages import HumanMessage

    from src.schema.base import setup_logging
    from src.schema.state import create_default_state

    setup_logging(logging.WARNING)
    node = BankAgentNode()
    tests = [
        ("电汇手续费多少？", "zh"),                           # 应该用 get_product_info
        ("Is PayNow free? How long does a TT take?", "en"),  # 两件事，可能调两次工具
        ("我的 PIN 被 lock 了怎么办", "zh"),                  # 应该用 search_faq
        ("Can I open an account if I'm 15?", "en"),          # search_faq
        ("定期存款利率是多少？", "zh"),                        # 知识库里没有，不能编
    ]
    for q, lang in tests:
        state = create_default_state(masked_question=q, lang=lang, messages=[HumanMessage(q)])
        start = time.perf_counter()
        agent = node.process(state)
        t_agent = time.perf_counter() - start
        start = time.perf_counter()
        rag = node.fallback.process(state)
        t_rag = time.perf_counter() - start
        print(f"\n===== {q}")
        calls = [(c["tool"], c["args"]) for c in node.last_run.get("tool_calls", [])]
        print(f"[Agent {t_agent:.1f}s] 工具：{calls}  {node.last_run.get('fallback', '')}")
        print(agent["answer"])
        print(f"[原来的 RAG {t_rag:.1f}s]")
        print(rag["answer"])
