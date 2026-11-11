# 对话历史
# 历史保存在 state["messages"] 里（已经脱敏），这里转成 [(角色, 文本), ...] 给问题重写和生成回答用
from langchain_core.messages import AIMessage, HumanMessage

SOURCE_MARKERS = ("\n\n来源：", "\n\nSource:")


def history_of(state):
    """本轮之前的对话。最后一条是本轮的问题，不要"""
    history = []
    for message in list(state.get("messages") or [])[:-1]:
        if isinstance(message, HumanMessage):
            history.append(("客户", str(message.content)))
        elif isinstance(message, AIMessage):
            history.append(("客服", strip_sources(str(message.content))))
    return history


def strip_sources(answer):
    """去掉回答最后的来源"""
    for marker in SOURCE_MARKERS:
        answer = answer.split(marker)[0]
    return answer


def format_history(history, max_turns=4, max_chars=1000):
    """最近几条对话拼成文本"""
    lines = [f"{role}：{text}" for role, text in history[-max_turns:]]
    return "\n".join(lines)[-max_chars:]


if __name__ == "__main__":
    mock_state = {"messages": [HumanMessage("海外电汇手续费多少？"),
                               AIMessage("每笔 SGD 10。\n\n来源：《费用与汇率》海外电汇（TT）手续费"),
                               HumanMessage("那 FAST 呢？")]}
    history = history_of(mock_state)
    print(history)
    print(format_history(history))
