"""
所有节点共用的功能：
1、日志（开始、完成、用时，不记客户的原话）
2、配置信息
3、异常处理（统一包成 BankAssistantError，带上节点名）
4、记录执行路径（每个节点跑完把名字加到 state["trace"]）
"""
import logging
import time
from abc import ABC, abstractmethod
from typing import Any, Optional

from langgraph.errors import GraphBubbleUp

from src.schema.config import Config, get_config
from src.schema.exceptions import BankAssistantError, ValidationError
from src.schema.state import GraphState

# 节点只返回自己改了的字段，LangGraph 会合并到完整的状态里
StateUpdate = dict[str, Any]


class BaseNode(ABC):

    name: str = "base_node"  # 节点名，子类要改成自己的
    fail_soft: bool = False  # 出错了要不要继续往下走
    # False：重要的节点，出错就抛出去（比如 input_guard、account_agent）
    # True：不那么重要的节点，出错记下来，流程继续（比如 entity_extraction）

    def __init__(self, config: Optional[Config] = None) -> None:
        self.config = config or get_config()
        self.logger = logging.getLogger(f"bank.{self.name}")

    def __call__(self, state: GraphState) -> StateUpdate:
        session = state.get("session_id", "-")
        start = time.perf_counter()
        # 日志里只记会话 ID 和节点名，不记客户的原话
        self.logger.info(f"-----{self.name} 开始执行----- session={session}")
        try:
            result = self.process(state)
            result = self._check_result(result)
        except GraphBubbleUp:
            # interrupt() 是靠抛异常来暂停的，这里要原样抛出去，不能当成错误处理，不然 OTP 暂停就没用了
            self.logger.info(f"-----{self.name} 暂停，等待客户输入----- session={session}")
            raise
        except Exception as e:
            error = e if isinstance(e, BankAssistantError) else BankAssistantError(
                message=f"{type(e).__name__}: {e}", node_name=self.name, cause=e)
            if not error.node_name:
                error.node_name = self.name
            elapsed = self._elapsed_ms(start)
            if not self.fail_soft:
                self.logger.error(f"-----{self.name} 执行失败----- {elapsed} ms | {error}")
                raise error from e
            self.logger.warning(f"-----{self.name} 执行失败，降级继续----- {elapsed} ms | {error}")
            return {"error": str(error), "trace": self._trace(state, failed=True)}

        result["trace"] = self._trace(state)
        self.logger.info(f"-----{self.name} 执行完成----- {self._elapsed_ms(start)} ms")
        return result

    def _check_result(self, result: Any) -> StateUpdate:
        """检查 process() 的返回值"""
        if result is None:
            return {}
        if isinstance(result, BaseException):
            raise result  # 不小心写成了 return ValidationError(...)，就当成抛出
        if not isinstance(result, dict):
            raise ValidationError(f"process() 必须返回 dict，实际返回 {type(result).__name__}", self.name)
        return dict(result)

    def _trace(self, state: GraphState, failed: bool = False) -> list[str]:
        """把当前节点加到执行路径里"""
        return list(state.get("trace") or []) + [f"{self.name}!" if failed else self.name]

    @staticmethod
    def _elapsed_ms(start: float) -> int:
        return int((time.perf_counter() - start) * 1000)

    def log_step(self, step_name: str, message: str = "") -> None:
        """日志输出（message 中不要放客户原文、卡号等隐私信息）"""
        msg = f"{step_name}"
        if message:
            msg += f": {message}"
        self.logger.info(msg)

    # 要求每个节点自己来完成特定能力
    @abstractmethod
    def process(self, state: GraphState) -> StateUpdate:
        """每个子节点需要处理的特定业务逻辑，只返回本节点修改的字段"""
        ...


def setup_logging(level: int = logging.INFO) -> None:
    """配置日志的级别"""
    logging.basicConfig(
        level=level,
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )


# if __name__ == "__main__":
#     from langgraph.checkpoint.memory import InMemorySaver
#     from langgraph.graph import END, START, StateGraph
#     from langgraph.types import Command, interrupt
#
#     from src.schema.exceptions import LLMError
#     from src.schema.state import create_turn_input
#
#     setup_logging()
#
#     class EchoNode(BaseNode):
#         name = "echo_node"
#
#         def process(self, state):
#             return {"answer": f"收到：{state['question']}"}
#
#     class SoftFailNode(BaseNode):
#         name = "entity_extraction"
#         fail_soft = True
#
#         def process(self, state):
#             raise LLMError("实体抽取超时")
#
#     class OtpNode(BaseNode):
#         name = "verify_otp"
#
#         def process(self, state):
#             code = interrupt({"type": "otp", "message_zh": "请输入验证码"})
#             return {"otp_verified": code == "123456"}
#
#     class BadReturnNode(BaseNode):
#         name = "bad_node"
#
#         def process(self, state):
#             return ValidationError("用户问题为空")  # 课程中的写法错误：return 而不是 raise
#
#     builder = StateGraph(GraphState)
#     for node in (EchoNode(), SoftFailNode(), OtpNode()):
#         builder.add_node(node.name, node)
#     builder.add_edge(START, "echo_node")
#     builder.add_edge("echo_node", "entity_extraction")
#     builder.add_edge("entity_extraction", "verify_otp")
#     builder.add_edge("verify_otp", END)
#     graph = builder.compile(checkpointer=InMemorySaver())
#     config = {"configurable": {"thread_id": "test-base"}}
#
#     # 1. 正常节点 + 降级节点，然后在 OTP 处暂停
#     out = graph.invoke(create_turn_input("test-base", "C001", "查一下余额"), config)
#     assert "__interrupt__" in out, "interrupt 没有暂停流程"
#     print("暂停中 trace =", out["trace"], "| error =", out["error"])
#
#     # 2. 输入验证码后从暂停处继续
#     out = graph.invoke(Command(resume="123456"), config)
#     print("恢复后 trace =", out["trace"], "| otp_verified =", out["otp_verified"])
#     assert out["trace"] == ["echo_node", "entity_extraction!", "verify_otp"] and out["otp_verified"]
#
#     # 3. 关键节点出错：包装成 BankAssistantError 并带上节点名
#     try:
#         BadReturnNode()(create_turn_input("t", "C001", "x"))
#     except BankAssistantError as e:
#         print("关键节点报错 ->", e)
#     print("全部检查通过")
