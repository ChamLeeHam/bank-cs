# 查账户之前先验证 OTP
# 在项目根目录运行测试：uv run python -m src.nodes.verify_otp
from langgraph.types import interrupt

from src.nodes.stub_nodes import pick
from src.schema.base import BaseNode, StateUpdate
from src.schema.exceptions import MySQLError
from src.schema.state import GraphState
from src.utils.mysql_utils import query_one

MAX_TRIES = 3


class VerifyOtpNode(BaseNode):
    """
    1. 这个会话已经验证过了，直接过
    2. 从 MySQL 查出客户的验证码（演示用的 customers.demo_otp）
    3. 暂停，等客户输入，最多试 3 次
    """
    name = "verify_otp"

    def process(self, state: GraphState) -> StateUpdate:
        if state.get("otp_verified") and state.get("otp_customer_id") == state.get("customer_id"):
            return {}
        lang = state.get("lang", "en")

        # 注意：恢复的时候这个节点会从头再跑一遍，所以 interrupt 之前只能读数据，不能写数据
        try:
            row = query_one("SELECT demo_otp FROM customers WHERE customer_id = %s", (state.get("customer_id"),))
        except MySQLError as e:
            self.logger.error(f"查询验证码失败：{e}")
            return {"otp_verified": False,
                    "answer": pick(lang, "Sorry, our system is busy. Please try again later.",
                                   "抱歉，系统繁忙，请稍后再试。")}
        if row is None:
            return {"otp_verified": False,
                    "answer": pick(lang, "Sorry, I can't find your customer record.", "抱歉，没有找到您的客户信息。")}

        for attempt in range(MAX_TRIES):
            if attempt == 0:
                message = {"en": "This is a demo. Please enter the 6-digit demo verification code.",
                           "zh": "这是演示流程，请输入 6 位演示验证码。"}
            else:
                left = MAX_TRIES - attempt
                message = {"en": f"The OTP is incorrect. Please try again ({left} tries left).",
                           "zh": f"验证码不正确，请重新输入（还可以试 {left} 次）。"}
            # 同一个节点里可以有多个 interrupt，恢复的时候按顺序拿到客户每次输入的值
            code = interrupt({"type": "otp", "message_en": message["en"], "message_zh": message["zh"]})
            if str(code).strip() == row["demo_otp"]:
                self.log_step(self.name, f"验证通过，第 {attempt + 1} 次")
                return {"otp_verified": True, "otp_customer_id": state.get("customer_id")}

        self.log_step(self.name, "验证失败，次数用完")
        return {"otp_verified": False,
                "answer": pick(lang, "The OTP is incorrect too many times, so I can't access your account. "
                                     "Please contact us by phone.",
                               "验证码错误次数太多，无法查询账户信息，请致电客服。")}


if __name__ == "__main__":
    # 单独测这个节点：建一个只有 verify_otp 的小图
    from langgraph.checkpoint.memory import InMemorySaver
    from langgraph.graph import END, StateGraph
    from langgraph.types import Command

    from src.schema.state import create_default_state

    workflow = StateGraph(GraphState)
    workflow.add_node("verify_otp", VerifyOtpNode())
    workflow.set_entry_point("verify_otp")
    workflow.add_edge("verify_otp", END)
    app = workflow.compile(checkpointer=InMemorySaver())

    def run(thread, graph_input):
        config = {"configurable": {"thread_id": thread}}
        result = app.invoke(graph_input, config)
        if "__interrupt__" in result:
            return "暂停：" + result["__interrupt__"][0].value["message_zh"]
        return f"otp_verified={result.get('otp_verified')}  {result.get('answer', '')}"

    # 1. 输错一次，再输对
    state = create_default_state(session_id="t1", customer_id="C001", question="查余额", lang="zh")
    print(run("t1", state))
    print("输入 000000 ->", run("t1", Command(resume="000000")))
    print("输入 123456 ->", run("t1", Command(resume="123456")))

    # 2. 连续输错 3 次
    state = create_default_state(session_id="t2", customer_id="C002", question="balance", lang="zh")
    print(run("t2", state))
    for code in ["111111", "222222", "333333"]:
        print(f"输入 {code} ->", run("t2", Command(resume=code)))

    # 3. 客户号不存在
    state = create_default_state(session_id="t3", customer_id="C999", question="balance", lang="zh")
    print(run("t3", state))
