"""
Day 1 占位节点（stub）：用最简单的规则跑通主图的连线
后续每个节点会被替换为 src/nodes/ 下独立文件中的真实实现：
    intent_router     -> Day 3 微调模型（LLaMA Factory）
    entity_extraction -> Day 3 实体抽取模型
    rag_answer        -> Day 4 Milvus 混合检索 + 术语图谱
    account_agent     -> Day 5 工具调用智能体（MySQL）
    handoff           -> Day 5 写入 tickets 表 + n8n 通知
"""
import re

from langchain_core.messages import AIMessage, HumanMessage
from langgraph.types import interrupt

from src.schema.base import BaseNode, StateUpdate
from src.schema.state import GraphState

# ============== 小工具 ==============
_CJK = re.compile(r"[一-鿿]")
_LATIN = re.compile(r"[A-Za-z]{2,}")
_CARD_NO = re.compile(r"\b(?:\d[ -]?){12,18}\d\b")  # 12–19 位卡号
_NRIC = re.compile(r"\b[STFGM]\d{7}[A-Z]\b", re.IGNORECASE)  # 新加坡身份证号
_INJECTION = re.compile(r"ignore (all |the )?previous instructions|忽略(之前|以上)的?(所有)?指令|system prompt|系统提示词", re.IGNORECASE)


def detect_lang(text: str) -> str:
    has_zh, has_en = bool(_CJK.search(text)), bool(_LATIN.search(text))
    return "mixed" if has_zh and has_en else "zh" if has_zh else "en"


def pick(lang: str, en: str, zh: str) -> str:
    """按客户语言选择回复：中文和中英混杂都用中文回复"""
    return en if lang == "en" else zh


# 意图 -> 分支（占位，Day 1 Task 3 完成意图设计后替换为你自己的表）
INTENT_ROUTES = {
    "card_lost_freeze": "account",
    "transfer_status": "account",
    "balance": "account",
    "fees_rates": "rag",
    "faq": "rag",
    "complaint": "handoff",
    "fraud_report": "handoff",
    "out_of_scope": "refuse",
    "greeting": "refuse",
}

# 关键词路由（占位），顺序即优先级：盗刷优先于挂失
KEYWORD_RULES = [
    ("fraud_report", r"盗刷|didn'?t make|not mine|unauthori[sz]ed|fraud|没(有)?(做|消费)过"),
    ("card_lost_freeze", r"lost|丢|不见|freeze|冻结|stolen"),
    ("complaint", r"投诉|complain|太差|terrible|manager|人工"),
    ("transfer_status", r"remittance|transfer|汇款|转账|到了没|到账"),
    ("balance", r"balance|余额|还有多少钱"),
    ("fees_rates", r"fee|手续费|收费|rate|汇率|charge"),
    ("out_of_scope", r"stock|股票|基金|crypto|比特币|invest|投资|天气|weather"),
    ("greeting", r"^(hi|hello|hey|你好|您好|哈喽)[!！.。\s]*$"),
]


# ============== 节点 ==============
class InputGuardNode(BaseNode):
    """语言识别、隐私脱敏、拦截提示注入"""
    name = "input_guard"

    def process(self, state: GraphState) -> StateUpdate:
        question = state.get("question", "")
        masked = _CARD_NO.sub(lambda m: "**** " + re.sub(r"\D", "", m.group())[-4:], question)
        masked = _NRIC.sub("[NRIC]", masked)
        return {
            "lang": detect_lang(question),
            "masked_question": masked,
            "blocked": bool(_INJECTION.search(question)),
            "messages": [HumanMessage(masked)],  # 对话历史只保存脱敏后的文本
        }


class IntentRouterNode(BaseNode):
    """占位：关键词匹配 = 0.9 置信度，匹配不到 = 0.5（触发追问）"""
    name = "intent_router"

    def process(self, state: GraphState) -> StateUpdate:
        text = state.get("masked_question", "")
        for intent, pattern in KEYWORD_RULES:
            if re.search(pattern, text, re.IGNORECASE):
                return {"intent": intent, "intent_score": 0.9, "router_backend": "keyword"}
        return {"intent": "faq", "intent_score": 0.5, "router_backend": "keyword"}


class EntityExtractionNode(BaseNode):
    """占位：正则抽取卡号后四位和金额；可降级节点，失败不影响主流程"""
    name = "entity_extraction"
    fail_soft = True

    def process(self, state: GraphState) -> StateUpdate:
        text = state.get("masked_question", "")
        found: dict[str, str] = {}
        if m := re.search(r"(?:尾号|ending|\*{4}\s?)(\d{4})", text, re.IGNORECASE):
            found["card_last4"] = m.group(1)
        if m := re.search(r"(?:S?\$|SGD\s?)(\d+(?:\.\d+)?)|(\d+(?:\.\d+)?)\s?(?:块|新币|元|SGD|dollars?)", text, re.IGNORECASE):
            found["amount"] = m.group(1) or m.group(2)
        return {"entities": {**(state.get("entities") or {}), **found}}  # 与上一轮的实体合并


class GateNode(BaseNode):
    """置信度 + 必填信息检查，决定走哪个分支"""
    name = "gate"

    def process(self, state: GraphState) -> StateUpdate:
        # TODO: 在 config.py 中加入 intent_threshold / clarify_threshold 后去掉 getattr 的默认值
        high = getattr(self.config, "intent_threshold", 0.7)
        low = getattr(self.config, "clarify_threshold", 0.4)
        score = state.get("intent_score", 0.0)
        missing: list[str] = []  # TODO: Day 5 按意图的必填信息表检查（如冻结卡需要 card_last4）

        if score < low:
            return {"decision": "handoff", "route": "handoff", "missing_slots": missing}
        if score < high or missing:
            return {"decision": "clarify", "route": "ask_back", "missing_slots": missing}
        route = INTENT_ROUTES.get(state.get("intent", ""), "rag")
        decision = "refuse" if route == "refuse" else "handoff" if route == "handoff" else "answer"
        return {"decision": decision, "route": route, "missing_slots": missing}


class AskBackNode(BaseNode):
    name = "ask_back"

    def process(self, state: GraphState) -> StateUpdate:
        lang = state.get("lang", "en")
        return {"answer": pick(lang,
                               "Sorry, could you tell me a bit more? For example: card, transfer, fees or account balance.",
                               "不好意思，能再具体说明一下吗？例如：银行卡、转账汇款、手续费或账户余额。")}


class RagAnswerNode(BaseNode):
    name = "rag_answer"

    def process(self, state: GraphState) -> StateUpdate:
        return {"answer": f"[RAG 待实现] intent={state.get('intent')}", "sources": [],
                "search_query": state.get("masked_question", "")}


class VerifyOtpNode(BaseNode):
    """账户类问题先验证身份；本会话验证过一次就不再重复"""
    name = "verify_otp"

    def process(self, state: GraphState) -> StateUpdate:
        if state.get("otp_verified"):
            return {}
        # interrupt 之前不要有任何副作用：恢复时本节点会从头重新执行
        code = interrupt({"type": "otp",
                          "message_en": "For your security, please enter the 6-digit OTP sent to your phone.",
                          "message_zh": "为了您的账户安全，请输入发送到您手机的 6 位验证码。"})
        verified = str(code).strip() == "123456"  # TODO: Day 5 改为查询 MySQL customers.demo_otp
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
                "ticket_id": None,  # TODO: Day 5 写入 MySQL tickets 表并通知 n8n
                "answer": pick(lang, "I've passed this to a human agent. They will contact you shortly.",
                               "已为您转接人工客服，稍后会有专人联系您。")}


class RefuseNode(BaseNode):
    name = "refuse"

    def process(self, state: GraphState) -> StateUpdate:
        lang = state.get("lang", "en")
        if state.get("intent") == "greeting":
            return {"answer": pick(lang, "Hello! I'm the Lion City Bank assistant. How can I help you today?",
                                   "您好！我是狮城银行智能客服，请问有什么可以帮您？")}
        return {"answer": pick(lang,
                               "Sorry, I can't help with that. I can't give investment advice, but I can help with cards, transfers, fees and your account.",
                               "抱歉，这个问题我无法回答，也不能提供投资建议。我可以帮您处理银行卡、转账、手续费和账户相关的问题。")}


class OutputGuardNode(BaseNode):
    """输出检查（Day 5 补充：再次脱敏、合规检查），并把回复写入对话历史"""
    name = "output_guard"

    def process(self, state: GraphState) -> StateUpdate:
        lang = state.get("lang", "en")
        answer = state.get("answer", "")
        if state.get("blocked"):
            answer = pick(lang, "Sorry, I can't process that request.", "抱歉，无法处理该请求。")
        answer = _CARD_NO.sub("****", answer)
        return {"answer": answer, "messages": [AIMessage(answer)]}
