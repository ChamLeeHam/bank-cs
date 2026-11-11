# 会话历史记录（参考课程 03-rag/utils/history_utils.py）
# 课程用一个按 session_id 保存的内存列表管理历史；本项目的历史由 LangGraph checkpointer 保存在 state["messages"] 中
# （input_guard 只写入脱敏后的文本），这里负责把它转换成问题重写和回答生成需要的格式
from langchain_core.messages import AIMessage, HumanMessage

SOURCE_MARKERS = ("\n\n来源：", "\n\nSource:")  # RAG 回答末尾的引用，放进历史时去掉


def history_of(state) -> list[tuple[str, str]]:
    """
    获取本轮之前的对话 [(角色, 脱敏文本), ...]，角色为 客户 / 客服
    input_guard 已把本轮问题追加到 messages 最后，所以去掉最后一条
    """
    history = []
    for message in list(state.get("messages") or [])[:-1]:
        if isinstance(message, HumanMessage):
            history.append(("客户", str(message.content)))
        elif isinstance(message, AIMessage):
            history.append(("客服", strip_sources(str(message.content))))
    return history


def strip_sources(answer: str) -> str:
    """去掉回答末尾的引用来源"""
    for marker in SOURCE_MARKERS:
        answer = answer.split(marker)[0]
    return answer


def format_history(history: list[tuple[str, str]], max_turns: int = 4, max_chars: int = 1000) -> str:
    """最近几条对话拼成文本，并限制长度（课程 response.py 的 _format_history）"""
    return "\n".join(f"{role}：{text}" for role, text in history[-max_turns:])[-max_chars:]


if __name__ == "__main__":
    mock_state = {"messages": [HumanMessage("海外电汇手续费多少？"),
                               AIMessage("每笔 SGD 10。\n\n来源：《费用与汇率》海外电汇（TT）手续费"),
                               HumanMessage("那 FAST 呢？")]}
    history = history_of(mock_state)
    print(history)
    print(format_history(history))
