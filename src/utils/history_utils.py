"""
对话历史工具（参考课程 03-rag/utils/history_utils.py）

课程用一个按会话保存的列表管理历史；本项目的历史由 LangGraph checkpointer 保存在 state["messages"] 中
（input_guard 只写入脱敏后的文本），这里负责把它转换成问题重写和回答生成需要的格式。
"""
from langchain_core.messages import AIMessage, HumanMessage

from src.schema.state import GraphState

SOURCE_MARKERS = ("\n\n来源：", "\n\nSource:")  # RAG 回答末尾的引用，放进历史时去掉


def strip_sources(answer: str) -> str:
    for marker in SOURCE_MARKERS:
        answer = answer.split(marker)[0]
    return answer


def history_of(state: GraphState) -> list[tuple[str, str]]:
    """
    本轮之前的对话 [(角色, 脱敏文本), ...]，角色为 客户 / 客服
    input_guard 已把本轮问题追加到 messages 最后，所以去掉最后一条
    """
    out = []
    for m in list(state.get("messages") or [])[:-1]:
        if isinstance(m, HumanMessage):
            out.append(("客户", str(m.content)))
        elif isinstance(m, AIMessage):
            out.append(("客服", strip_sources(str(m.content))))
    return out


def format_history(history: list[tuple[str, str]], max_turns: int = 4, max_chars: int = 1000) -> str:
    """最近几条对话拼成文本，限制长度（课程 response.py 的 _format_history）"""
    return "\n".join(f"{role}：{text}" for role, text in history[-max_turns:])[-max_chars:]
