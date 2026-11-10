"""
定义各个节点中都存在的一些共性能力（参考课程 base.py）
1、日志的输出（开始 / 完成 / 耗时，不记录客户原文）
2、配置信息
3、异常处理（统一包装为 BankAssistantError，并标注节点名）
4、执行路径记录（每个节点执行后把自己的名字追加到 state["trace"]）

运行自测（在项目根目录）：
    uv run python -m src.schema.base
"""
import logging
import time
from abc import ABC, abstractmethod
from typing import Any, Optional

from langgraph.errors import GraphBubbleUp

from src.schema.config import Config, get_config
from src.schema.exceptions import BankAssistantError, ValidationError
from src.schema.state import GraphState

# 节点返回的是“部分状态更新”：只包含本节点修改过的字段，LangGraph 负责合并到完整状态
StateUpdate = dict[str, Any]


class BaseNode(ABC):

    name: str = "base_node"  # 节点名称，子类必须覆盖（也是 trace 中记录的名字）
    fail_soft: bool = False  # 出错时是否降级继续执行：
    #   False（默认）：关键节点，出错直接抛出，由服务层返回兜底回复（如 input_guard、account_agent）
    #   True：可降级节点，出错时记录 error 并继续后面的流程（如 entity_extraction、术语扩展）

    def __init__(self, config: Optional[Config] = None) -> None:
        self.config = config or get_config()  # 配置对象
        self.logger = logging.getLogger(f"bank.{self.name}")  # 日志对象，统一以 bank. 开头，方便单独调级别

    def __call__(self, state: GraphState) -> StateUpdate:
        session = state.get("session_id", "-")
        start = time.perf_counter()
        # 注意：日志只记录会话 ID 和节点名，不记录 question 等客户原文（避免隐私进入日志）
        self.logger.info(f"-----{self.name} 开始执行----- session={session}")
        try:
            result = self.process(state)
            result = self._check_result(result)
        except GraphBubbleUp:
            # interrupt() 通过抛出 GraphInterrupt 来暂停流程（等待 OTP / 客户确认）。
            # 它是 Exception 的子类，必须原样抛出，不能被当成错误包装，否则人机协同会失效。
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
        """检查 process() 的返回值，防止异常对象这类错误悄悄进入状态"""
        if result is None:
            return {}
        if isinstance(result, BaseException):
            raise result  # 写成了 return ValidationError(...)：按抛出处理
        if not isinstance(result, dict):
            raise ValidationError(f"process() 必须返回 dict，实际返回 {type(result).__name__}", self.name)
        return dict(result)  # 复制一份，避免修改到节点内部的对象

    def _trace(self, state: GraphState, failed: bool = False) -> list[str]:
        """在执行路径中追加当前节点（主图串行执行，直接覆盖 trace 是安全的）"""
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
