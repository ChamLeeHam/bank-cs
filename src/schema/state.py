# GraphState：主图里各个节点之间传递的数据
# 在项目根目录运行测试：uv run python -m src.schema.state
import copy
from typing import Annotated, Any, Literal, TypedDict

from langchain_core.messages import AnyMessage
from langgraph.graph.message import add_messages

from src.schema.exceptions import ValidationError

# 几个字段的取值范围
Lang = Literal["en", "zh", "mixed"]
RouterBackend = Literal["finetuned", "llm", "keyword"]
Decision = Literal["answer", "clarify", "handoff", "refuse"]
Route = Literal["rag", "account", "handoff", "refuse", "ask_back"]
Priority = Literal["normal", "urgent"]


class GraphState(TypedDict, total=False):
    """
    工作流的状态数据。total=False，节点只需要返回自己改了的字段
    除了 messages，其他字段都是直接覆盖旧值（主图是串行的，没有并行分支，所以没问题）
    注释里的 [跨轮] 表示多轮对话中会保留，[每轮] 表示每一轮开始时重置
    """
    # 会话
    session_id: str  # 会话 ID，也是 checkpointer 的 thread_id [跨轮]
    customer_id: str  # 登录的客户 ID，系统传进来的，模型不能改 [跨轮]

    # 输入
    question: str  # 这一轮的原始问题，只有 input_guard 会读 [每轮]
    messages: Annotated[list[AnyMessage], add_messages]  # 对话历史，只存脱敏后的文本，追加不覆盖 [跨轮]

    # input_guard 写入
    lang: Lang  # 客户用的语言，决定用什么语言回复 [每轮]
    masked_question: str  # 脱敏后的问题，后面的节点都用这个 [每轮]
    blocked: bool  # 是否被拦截，比如提示注入 [每轮]
    human_requested: bool  # 客户是否要求转人工 [每轮]

    # intent_router 写入
    intent: str  # 一级意图，决定走哪个分支，追问后下一轮还要用 [跨轮]
    sub_intent: str  # 二级意图，比如 pin_blocked [每轮]
    intent_score: float  # 一级意图的置信度 [每轮]
    router_backend: RouterBackend  # 这一轮是模型判断的还是规则判断的 [每轮]

    # 实体
    entities: dict[str, str]  # 抽到的实体，和上一轮的合并 [跨轮]
    missing_slots: list[str]  # 还缺哪些必填信息 [每轮]

    # gate 写入
    clarify_count: int  # 连续追问了几次，追问加 1，走别的分支清零 [跨轮]
    decision: Decision | None  # 分流的结果 [每轮]
    route: Route | None  # 走哪个分支 [每轮]

    # rag_answer 写入
    search_query: str  # 重写后用来检索的问题 [每轮]
    sources: list[dict[str, Any]]  # 回答引用的来源 [每轮]

    # 账户
    otp_verified: bool  # 这个会话是否已经验证过 OTP，只能由 verify_otp 写 [跨轮]
    tool_calls: list[dict[str, Any]]  # 账户智能体调用了哪些工具，用来审计 [每轮]

    # handoff 写入
    ticket_id: int | None  # 工单号 [跨轮]
    priority: Priority  # 盗刷是 urgent [每轮]

    # 输出
    answer: str  # 最后的回复 [每轮]
    trace: list[str]  # 经过了哪些节点 [每轮]
    error: str | None  # 节点出错的信息 [每轮]


# 跨轮保留的字段，只在会话第一轮初始化，后面由 checkpointer 保存
CROSS_TURN_DEFAULTS: dict[str, Any] = {
    "session_id": "",
    "customer_id": "",
    "messages": [],
    "intent": "",
    "entities": {},
    "otp_verified": False,
    "ticket_id": None,
    "clarify_count": 0,
}

# 每一轮开始时要重置的字段
PER_TURN_DEFAULTS: dict[str, Any] = {
    "question": "",
    "lang": "en",
    "masked_question": "",
    "blocked": False,
    "human_requested": False,
    "sub_intent": "",
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
    """返回默认状态的副本，用 deepcopy 免得改到全局的默认值"""
    return copy.deepcopy(GRAPH_DEFAULT_STATE)


def create_default_state(**overrides) -> GraphState:
    """
    创建完整的默认状态，再覆盖一些字段，测试单个节点或者会话第一轮的时候用
    后面的轮次不要用这个，会把 otp_verified、intent 这些跨轮的字段清掉，要用 create_turn_input()
    """
    unknown = set(overrides) - set(GRAPH_DEFAULT_STATE)
    if unknown:  # 字段名写错了就报错
        raise ValidationError(f"GraphState 中不存在的字段: {sorted(unknown)}", "create_default_state")
    state = get_default_state()
    state.update(overrides)
    return state


def create_turn_input(session_id: str, customer_id: str, question: str) -> GraphState:
    """
    每一轮传给 graph.invoke() 的输入
    传进去的值会覆盖 checkpointer 里的旧值，所以这里只放每轮要重置的字段，
    跨轮的字段（intent、entities、otp_verified、messages 等）不放，保持上一轮的值
    原始问题也不放进 messages，由 input_guard 脱敏后再加进去
    """
    question = (question or "").strip()
    if not question:
        raise ValidationError("用户问题为空", "create_turn_input")
    if not session_id or not customer_id:
        raise ValidationError("缺少 session_id 或 customer_id", "create_turn_input")

    turn: GraphState = copy.deepcopy(PER_TURN_DEFAULTS)  # deepcopy，每轮的 list 不要共用一个
    turn.update(session_id=session_id, customer_id=customer_id, question=question)
    return turn


if __name__ == "__main__":
    from pprint import pprint

    # 1. 每个字段都要分好是每轮重置还是跨轮保留
    all_fields = set(GraphState.__annotations__)
    per_turn, cross_turn = set(PER_TURN_FIELDS), set(CROSS_TURN_FIELDS)
    assert per_turn | cross_turn == all_fields, f"没有分类的字段: {all_fields - per_turn - cross_turn}"
    assert not per_turn & cross_turn, f"重复分类的字段: {per_turn & cross_turn}"
    print(f"GraphState 共 {len(all_fields)} 个字段：每轮重置 {len(per_turn)} 个，跨轮保留 {len(cross_turn)} 个")

    # 2. 每轮的输入里没有跨轮字段，list 不共用
    t1 = create_turn_input("web-001", "C001", "  我想 check 一下我的 remittance  ")
    t2 = create_turn_input("web-001", "C001", "How much is the TT fee?")
    assert "messages" not in t1 and "intent" not in t1 and "otp_verified" not in t1
    assert t1["trace"] is not t2["trace"]
    pprint(t1, sort_dicts=False)

    # 3. 空问题会报错
    try:
        create_turn_input("web-001", "C001", "   ")
    except ValidationError as e:
        print("空问题被拒绝 ->", e)

    # 4. 默认状态 + 覆盖；字段名写错会报错
    s = create_default_state(session_id="test-001", customer_id="C001", question="TT 手续费多少？")
    s["trace"].append("input_guard")
    assert GRAPH_DEFAULT_STATE["trace"] == [] and set(s) == all_fields
    print("create_default_state ->", {k: s[k] for k in ("session_id", "question", "otp_verified", "trace")})
    try:
        create_default_state(sesion_id="typo")
    except ValidationError as e:
        print("拼错的字段被拒绝 ->", e)
