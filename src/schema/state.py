"""
GraphState：主图各节点之间传递的数据契约（与《需求文档》第四章的表格一一对应）

运行自测（在项目根目录）：
    uv run python -m src.schema.state
"""
import copy
from typing import Annotated, Any, Literal, TypedDict

from langchain_core.messages import AnyMessage
from langgraph.graph.message import add_messages

from src.schema.exceptions import ValidationError

# ============== 固定取值的类型 ==============
Lang = Literal["en", "zh", "mixed"]
RouterBackend = Literal["finetuned", "llm", "keyword"]
Decision = Literal["answer", "clarify", "handoff", "refuse"]
Route = Literal["rag", "account", "handoff", "refuse", "ask_back"]
Priority = Literal["normal", "urgent"]


class GraphState(TypedDict, total=False):
    """
    记录工作流完整的状态数据
    total=False 表示字段都是可选的（节点只需返回自己修改的字段）

    除 messages 外，其余字段都没有 reducer：节点返回的值直接覆盖旧值。
    主图是串行执行的（没有并行分支），所以直接覆盖是安全的。
    """
    # ============== 会话标识 ==============
    session_id: str  # 会话 ID，同时作为 checkpointer 的 thread_id【跨轮保留】
    customer_id: str  # 登录客户 ID，由系统注入，模型不能生成或修改【跨轮保留】

    # ============== 输入 ==============
    question: str  # 本轮原始问题，只有 input_guard 读取【每轮重置】
    messages: Annotated[list[AnyMessage], add_messages]  # 对话历史（只存脱敏后的文本），add_messages 追加而不是覆盖【跨轮保留】

    # ============== 护栏（input_guard 写入） ==============
    lang: Lang  # 识别出的语言，决定回复语言【每轮重置】
    masked_question: str  # 脱敏后的问题，input_guard 之后的节点只使用这个字段【每轮重置】
    blocked: bool  # 是否被拦截（如提示注入）；拦截是正常业务结果，不是异常【每轮重置】

    # ============== 意图（intent_router 写入） ==============
    intent: str  # 意图标签；追问后下一轮需要沿用【跨轮保留】
    intent_score: float  # 意图置信度 0~1【每轮重置】
    router_backend: RouterBackend  # 本轮使用的路由模型【每轮重置】

    # ============== 实体 ==============
    entities: dict[str, str]  # entity_extraction 写入；追问后与新抽取的实体合并【跨轮保留】
    missing_slots: list[str]  # gate 写入：当前意图缺少的必填信息【每轮重置】

    # ============== 分流（gate 写入） ==============
    decision: Decision | None  # 置信度分流结果【每轮重置】
    route: Route | None  # 实际进入的分支【每轮重置】

    # ============== 检索（rag_answer 写入） ==============
    search_query: str  # 术语图谱扩展后的检索语句【每轮重置】
    sources: list[dict[str, Any]]  # 引用来源：文档、章节、片段【每轮重置】

    # ============== 账户 ==============
    otp_verified: bool  # 只能由 verify_otp 根据系统校验结果写入，模型不能修改；本会话验证一次即可【跨轮保留】
    tool_calls: list[dict[str, Any]]  # account_agent 写入：工具名、参数、结果（审计用）【每轮重置】

    # ============== 工单（handoff 写入） ==============
    ticket_id: int | None  # 工单号【跨轮保留】
    priority: Priority  # 盗刷等为 urgent【每轮重置】

    # ============== 输出 ==============
    answer: str  # 最终回复【每轮重置】
    trace: list[str]  # 执行路径（节点名列表），由 BaseNode 追加，用于审计与前端展示【每轮重置】
    error: str | None  # 节点异常信息，由 BaseNode 写入【每轮重置】


# ============== 默认值 ==============
# 跨轮保留的字段（对应表格中“跨轮保留 = 是”）：只在会话第一轮初始化，之后由 checkpointer 保留
CROSS_TURN_DEFAULTS: dict[str, Any] = {
    "session_id": "",
    "customer_id": "",
    "messages": [],
    "intent": "",
    "entities": {},
    "otp_verified": False,
    "ticket_id": None,
}

# 每轮开始时要重置的字段及其初始值（对应表格中“跨轮保留 = 否”）
PER_TURN_DEFAULTS: dict[str, Any] = {
    "question": "",
    "lang": "en",
    "masked_question": "",
    "blocked": False,
    "intent_score": 0.0,
    "router_backend": "keyword",
    "missing_slots": [],
    "decision": None,
    "route": None,
    "search_query": "",
    "sources": [],
    "tool_calls": [],
    "priority": "normal",
    "answer": "",
    "trace": [],
    "error": None,
}
PER_TURN_FIELDS = tuple(PER_TURN_DEFAULTS)
CROSS_TURN_FIELDS = tuple(CROSS_TURN_DEFAULTS)

# 完整的默认状态
GRAPH_DEFAULT_STATE: GraphState = {**CROSS_TURN_DEFAULTS, **PER_TURN_DEFAULTS}


def get_default_state() -> GraphState:
    """
    获取默认状态副本
    :return: 状态副本（deepcopy，避免修改到全局的 GRAPH_DEFAULT_STATE）
    """
    return copy.deepcopy(GRAPH_DEFAULT_STATE)


def create_default_state(**overrides) -> GraphState:
    """
    创建完整的默认状态，并按需覆盖字段（用于单节点测试、会话第一轮）

    注意：不要在会话的后续轮次把它传给 graph.invoke()——
    它会把 otp_verified、intent、entities 等跨轮字段重置掉。后续轮次请用 create_turn_input()。

    Examples:
        >>> state = create_default_state(session_id="test-001", customer_id="C001", question="TT 手续费多少？")
    """
    unknown = set(overrides) - set(GRAPH_DEFAULT_STATE)
    if unknown:  # 防止字段名拼错（如 sesion_id）却悄悄生效
        raise ValidationError(f"GraphState 中不存在的字段: {sorted(unknown)}", "create_default_state")
    state = get_default_state()
    state.update(overrides)  # 更新拷贝过来的值
    return state


def create_turn_input(session_id: str, customer_id: str, question: str) -> GraphState:
    """
    创建每一轮 graph.invoke() 的输入

    传给 invoke 的值会覆盖 checkpointer 里保存的旧值，所以：
      - 每轮重置的字段在这里给出初始值，上一轮的 trace / sources / tool_calls 不会混入本轮
      - 跨轮保留的字段（intent、entities、otp_verified、messages ……）不放进来，保持上一轮的值
      - 原始问题不放进 messages：由 input_guard 脱敏后再追加，保证对话历史里没有卡号等隐私
      - 用 Command(resume=...) 恢复 interrupt 时不要调用本函数（仍是同一轮）

    Examples:
        >>> turn = create_turn_input("web-001", "C001", "我的卡不见了")
        >>> graph.invoke(turn, {"configurable": {"thread_id": "web-001"}})
    """
    question = (question or "").strip()
    if not question:
        raise ValidationError("用户问题为空", "create_turn_input")
    if not session_id or not customer_id:
        raise ValidationError("缺少 session_id 或 customer_id", "create_turn_input")

    turn: GraphState = copy.deepcopy(PER_TURN_DEFAULTS)  # deepcopy：避免各轮共享同一个 list 对象
    turn.update(session_id=session_id, customer_id=customer_id, question=question)
    return turn


if __name__ == "__main__":
    from pprint import pprint

    # 1. 字段完整性：每个字段必须且只能属于“每轮重置”或“跨轮保留”之一
    all_fields = set(GraphState.__annotations__)
    per_turn, cross_turn = set(PER_TURN_FIELDS), set(CROSS_TURN_FIELDS)
    assert per_turn | cross_turn == all_fields, f"未分类的字段: {all_fields - per_turn - cross_turn}"
    assert not per_turn & cross_turn, f"重复分类的字段: {per_turn & cross_turn}"
    print(f"GraphState 共 {len(all_fields)} 个字段：每轮重置 {len(per_turn)} 个，跨轮保留 {len(cross_turn)} 个")

    # 2. 每轮输入：不包含跨轮字段，list 不共享
    t1 = create_turn_input("web-001", "C001", "  我想 check 一下我的 remittance  ")
    t2 = create_turn_input("web-001", "C001", "How much is the TT fee?")
    assert "messages" not in t1 and "intent" not in t1 and "otp_verified" not in t1
    assert t1["trace"] is not t2["trace"]
    pprint(t1, sort_dicts=False)

    # 3. 空问题会抛出 ValidationError
    try:
        create_turn_input("web-001", "C001", "   ")
    except ValidationError as e:
        print("空问题被拒绝 ->", e)

    # 4. 课程写法：完整默认状态 + 覆盖；默认值不被修改；拼错的字段名会报错
    s = create_default_state(session_id="test-001", customer_id="C001", question="TT 手续费多少？")
    s["trace"].append("input_guard")
    assert GRAPH_DEFAULT_STATE["trace"] == [] and set(s) == all_fields
    print("create_default_state ->", {k: s[k] for k in ("session_id", "question", "otp_verified", "trace")})
    try:
        create_default_state(sesion_id="typo")
    except ValidationError as e:
        print("拼错的字段被拒绝 ->", e)
