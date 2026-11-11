# 还没做完的节点先用简单的规则代替，保证主图能跑通
# 已经换成正式版本的：intent_router（intent_router.py）、rag_answer（rag_answer.py）
# 还要做的：entity_extraction、account_agent、handoff 写工单
import re

from langchain_core.messages import AIMessage, HumanMessage
from langgraph.types import interrupt

from src.intents import INTENT_ROUTES, is_human_request
from src.schema.base import BaseNode, StateUpdate
from src.schema.state import GraphState

_CJK = re.compile(r"[一-鿿]")
_LATIN = re.compile(r"[A-Za-z]{2,}")
_CARD_NO = re.compile(r"\b(?:\d[ -]?){12,18}\d\b")  # 卡号，13~19 位
_NRIC = re.compile(r"\b[STFGM]\d{7}[A-Z]\b", re.IGNORECASE)  # 新加坡身份证号
_INJECTION = re.compile(r"ignore (all |the )?previous instructions|忽略(之前|以上)的?(所有)?指令|system prompt|系统提示词",
                        re.IGNORECASE)

# 关键词规则，模型用不了的时候用。按顺序匹配，排在前面的优先
KEYWORD_RULES = [
    ("fraud_report", r"盗刷|didn'?t make|not mine|unauthori[sz]ed|fraud|don'?t recogni[sz]e|没(有)?(做|消费)过"),
    ("card_lost_freeze", r"lost|丢|不见|freeze|冻结|stolen"),
    ("complaint", r"投诉|complain|太差|terrible|manager"),
    ("dispute_refund", r"charged twice|twice|refund|退款|扣了两次|重复扣|wrong amount"),
    ("transfer_howto", r"how long|how do i (send|transfer|receive)|多久|怎么(转账|汇款|收款)"),
    ("transfer_status", r"remittance|transfer|汇款|转账|到了没|到账"),
    ("balance_inquiry", r"balance|余额|还有多少钱"),
    ("fees_rates", r"fee|手续费|收费|rate|汇率|charge"),
    ("card_service", r"activate|激活|\bpin\b|密码锁|吞|swallow|card (arrive|delivery)|寄卡"),
    ("account_service", r"open an account|开户|verify|身份验证|address|地址|password|忘记密码|close my account|销户"),
    ("out_of_scope", r"stock|股票|基金|crypto|比特币|invest|投资|天气|weather"),
    ("greeting", r"^(hi|hello|hey|你好|您好|哈喽)[!！.。\s]*$"),
]


def detect_lang(text):
    has_zh = bool(_CJK.search(text))
    has_en = bool(_LATIN.search(text))
    if has_zh and has_en:
        return "mixed"
    return "zh" if has_zh else "en"


def pick(lang, en, zh):
    """英文问题用英文回复，中文和中英混杂都用中文"""
    return en if lang == "en" else zh


def last_user_messages(state, n=2):
    """最近 n 条客户消息"""
    texts = [str(m.content) for m in state.get("messages") or [] if isinstance(m, HumanMessage)]
    return texts[-n:]


def router_text(state):
    """
    上一轮是追问的话，客户这一轮可能只回了"第二笔"这种半句话，
    所以把上一条消息和这一条拼起来再去判断意图
    """
    current = state.get("masked_question", "")
    if state.get("clarify_count", 0) > 0:
        history = last_user_messages(state, 2)
        if len(history) == 2:
            return f"{history[0]} {current}"
    return current


class InputGuardNode(BaseNode):
    """识别语言、隐藏卡号和身份证号、拦截提示注入、判断是不是要转人工"""
    name = "input_guard"

    def process(self, state: GraphState) -> StateUpdate:
        question = state.get("question", "")
        # 卡号只保留后四位
        masked = _CARD_NO.sub(lambda m: "**** " + re.sub(r"\D", "", m.group())[-4:], question)
        masked = _NRIC.sub("[NRIC]", masked)
        return {
            "lang": detect_lang(question),
            "masked_question": masked,
            "blocked": bool(_INJECTION.search(question)),
            "human_requested": is_human_request(masked),
            "messages": [HumanMessage(masked)],  # 对话历史里只存脱敏后的文本
        }


class EntityExtractionNode(BaseNode):
    """先用正则抽卡号后四位和金额。出错也不影响主流程"""
    name = "entity_extraction"
    fail_soft = True

    def process(self, state: GraphState) -> StateUpdate:
        text = state.get("masked_question", "")
        found = {}

        m = re.search(r"(?:尾号|ending|\*{4}\s?)(\d{4})", text, re.IGNORECASE)
        if m:
            found["card_last4"] = m.group(1)

        m = re.search(r"(?:S?\$|SGD\s?)(\d+(?:\.\d+)?)|(\d+(?:\.\d+)?)\s?(?:块|新币|元|SGD|dollars?)", text, re.IGNORECASE)
        if m:
            found["amount"] = m.group(1) or m.group(2)

        # 和上一轮抽到的实体合并
        entities = dict(state.get("entities") or {})
        entities.update(found)
        return {"entities": entities}


class GateNode(BaseNode):
    """
    决定走哪个分支：
    1. 客户要求转人工，转人工
    2. 置信度太低，转人工
    3. 置信度一般或者缺信息，追问；已经连续追问 2 次了就转人工
    4. 其他情况按意图走对应的分支
    """
    name = "gate"
    MAX_CLARIFY = 2

    def process(self, state: GraphState) -> StateUpdate:
        high = self.config.intent_threshold
        low = self.config.clarify_threshold
        score = state.get("intent_score", 0.0)
        clarify_count = state.get("clarify_count", 0)
        missing = []  # TODO: 按意图检查必填信息，比如冻结卡需要卡号后四位

        if state.get("human_requested") or score < low:
            return self._result("handoff", "handoff", missing, clarify_count)

        if score < high or missing or state.get("intent") == "unknown":
            if clarify_count >= self.MAX_CLARIFY:
                return self._result("handoff", "handoff", missing, clarify_count)
            return self._result("clarify", "ask_back", missing, clarify_count)

        route = INTENT_ROUTES.get(state.get("intent", ""), "rag")
        if route == "refuse":
            decision = "refuse"
        elif route == "handoff":
            decision = "handoff"
        else:
            decision = "answer"
        return self._result(decision, route, missing, clarify_count)

    @staticmethod
    def _result(decision, route, missing, clarify_count):
        # 追问的话次数加 1，走其他分支就清零
        count = clarify_count + 1 if route == "ask_back" else 0
        return {"decision": decision, "route": route, "missing_slots": missing, "clarify_count": count}


class AskBackNode(BaseNode):
    name = "ask_back"

    def process(self, state: GraphState) -> StateUpdate:
        lang = state.get("lang", "en")
        return {"answer": pick(lang,
                               "Sorry, could you tell me a bit more? For example: card, transfer, fees or account balance.",
                               "不好意思，能再具体说明一下吗？例如：银行卡、转账汇款、手续费或账户余额。")}


class VerifyOtpNode(BaseNode):
    """查账户之前先验证 OTP，同一个会话验证过一次就不用再验"""
    name = "verify_otp"

    def process(self, state: GraphState) -> StateUpdate:
        if state.get("otp_verified"):
            return {}
        # 注意 interrupt 之前不能有副作用，因为恢复的时候这个节点会从头再跑一遍
        code = interrupt({"type": "otp",
                          "message_en": "For your security, please enter the 6-digit OTP sent to your phone.",
                          "message_zh": "为了您的账户安全，请输入发送到您手机的 6 位验证码。"})
        verified = str(code).strip() == "123456"  # TODO: 改成查 MySQL 里的 customers.demo_otp
        if verified:
            return {"otp_verified": True}
        lang = state.get("lang", "en")
        return {"otp_verified": False,
                "answer": pick(lang, "The OTP is incorrect, so I can't access your account.",
                               "验证码不正确，无法查询账户信息。")}


class AccountAgentNode(BaseNode):
    name = "account_agent"

    def process(self, state: GraphState) -> StateUpdate:
        return {"answer": f"[账户智能体待实现] intent={state.get('intent')} entities={state.get('entities')}",
                "tool_calls": []}


class HandoffNode(BaseNode):
    name = "handoff"

    def process(self, state: GraphState) -> StateUpdate:
        urgent = state.get("intent") == "fraud_report"
        lang = state.get("lang", "en")
        return {"priority": "urgent" if urgent else "normal",
                "ticket_id": None,  # TODO: 写入 MySQL 的 tickets 表，再通知 n8n
                "answer": pick(lang, "I've passed this to a human agent. They will contact you shortly.",
                               "已为您转接人工客服，稍后会有专人联系您。")}


class RefuseNode(BaseNode):
    name = "refuse"

    def process(self, state: GraphState) -> StateUpdate:
        lang = state.get("lang", "en")
        if state.get("intent") == "greeting":
            # 欢迎语里说明是 AI 客服
            return {"answer": pick(lang, "Hello! I'm Lion City Bank's AI assistant. How can I help you today?",
                                   "您好！我是狮城银行 AI 客服，请问有什么可以帮您？")}
        return {"answer": pick(lang,
                               "Sorry, I can't help with that. I can't give investment advice, but I can help with cards, transfers, fees and your account.",
                               "抱歉，这个问题我无法回答，也不能提供投资建议。我可以帮您处理银行卡、转账、手续费和账户相关的问题。")}


class OutputGuardNode(BaseNode):
    """回复前再检查一次，把回复写进对话历史"""
    name = "output_guard"

    def process(self, state: GraphState) -> StateUpdate:
        lang = state.get("lang", "en")
        answer = state.get("answer", "")
        if state.get("blocked"):
            answer = pick(lang, "Sorry, I can't process that request.", "抱歉，无法处理该请求。")
        answer = _CARD_NO.sub("****", answer)
        return {"answer": answer, "messages": [AIMessage(answer)]}
