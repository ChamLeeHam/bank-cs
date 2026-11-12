# 还没做完的节点先用简单的规则代替，保证主图能跑通
# 已经换成正式版本的：intent_router.py、rag_answer.py、verify_otp.py，entity_extraction 用上了术语图谱
# 还要做的：account_agent、handoff 写工单
# 在项目根目录运行测试：uv run python -m src.nodes.stub_nodes
import re

from langchain_core.messages import AIMessage, HumanMessage

from src.intents import INTENT_ROUTES, is_human_request, mentions_other_bank
from src.schema.base import BaseNode, StateUpdate
from src.schema.exceptions import MySQLError, Neo4jError
from src.schema.state import GraphState
from src.utils.neo4j_utils import find_attr, find_products
from src.utils.ticket_utils import create_ticket
from src.utils.transfer_context import transfer_ordinal, transfer_other

_CJK = re.compile(r"[一-鿿]")
_LATIN = re.compile(r"[A-Za-z]{2,}")
_CARD_NO = re.compile(r"\b(?:\d[ -]?){12,18}\d\b")  # 卡号，13~19 位
_NRIC = re.compile(r"\b[STFGM]\d{7}[A-Z]\b", re.IGNORECASE)  # 新加坡身份证号
_EMAIL = re.compile(r"\b[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}\b")
_PHONE = re.compile(r"(?<!\d)(?:\+?65[ -]?)?[689]\d{3}[ -]?\d{4}(?!\d)")
_ACCOUNT_ID = re.compile(r"\b(?:SA|CA|MC)-\d{4}-\d{4}\b", re.IGNORECASE)
_ACCOUNT_NUMBER = re.compile(r"((?:account(?:\s*(?:number|no\.?))?|账号|账户号码)\s*[:：#]?\s*)([\d -]{8,20})", re.IGNORECASE)
_PRIVATE_QUESTION = re.compile(r"(?:我|本人|我的).{0,35}(?:余额|转账|汇款|转了|汇了|转给|汇给|给.{0,15}(?:钱|转|汇))|"
                               r"\b(?:my|mine)\b.{0,50}\b(?:balance|transfer|remittance|transaction|account number)\b", re.IGNORECASE)
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
        masked = _EMAIL.sub("[EMAIL]", masked)
        masked = _ACCOUNT_ID.sub("[ACCOUNT]", masked)
        masked = _ACCOUNT_NUMBER.sub(lambda m: m.group(1) + "[ACCOUNT]", masked)
        masked = _PHONE.sub("[PHONE]", masked)
        result = {
            "lang": detect_lang(question),
            "masked_question": masked,
            "private_input": masked != question or bool(_PRIVATE_QUESTION.search(question)),
            "blocked": bool(_INJECTION.search(question)),
            "human_requested": is_human_request(masked),
            "messages": [HumanMessage(masked)],  # 对话历史里只存脱敏后的文本
        }
        if transfer_ordinal(masked) is None and not transfer_other(masked):
            result.update(transfer_candidates=[], transfer_customer_id="")
        return result


class EntityExtractionNode(BaseNode):
    """用正则抽卡号后四位和金额，用术语图谱找产品和属性。出错也不影响主流程"""
    name = "entity_extraction"
    fail_soft = True

    def process(self, state: GraphState) -> StateUpdate:
        text = state.get("masked_question", "")
        found = {}

        # 1. 卡号后四位："尾号 7730"、"ending 7730"、"**** 7730"
        m = re.search(r"(?:尾号|ending|\*{4})\s*(\d{4})", text, re.IGNORECASE)
        if m:
            found["card_last4"] = m.group(1)

        # 2. 金额："SGD 500"、"$500"、"500 块"
        m = re.search(r"(?:S?\$|SGD\s?)(\d+(?:\.\d+)?)|(\d+(?:\.\d+)?)\s?(?:块|新币|元|SGD|dollars?)", text, re.IGNORECASE)
        if m:
            found["amount"] = m.group(1) or m.group(2)

        # 3. 从术语图谱里找客户说的是哪个产品、问的是哪个属性
        # Neo4j 连不上的话只跳过这一步，前面抽到的卡号和金额还在
        try:
            products = [p["product_id"] for p in find_products(text)]
            if products:
                found["products"] = products
            attr = find_attr(text)
            if attr:
                found["attr"] = attr
        except Neo4jError as e:
            self.logger.warning(f"术语图谱查不了，这一轮先不用：{e}")

        # 4. 和上一轮抽到的实体合并：卡号留着，产品和属性只算这一轮的，先把上一轮的去掉
        entities = dict(state.get("entities") or {})
        entities.pop("products", None)
        entities.pop("attr", None)
        entities.update(found)
        return {"entities": entities}


class GateNode(BaseNode):
    """
    决定走哪个分支：
    1. 客户要求转人工，转人工
    2. 置信度太低，转人工
    3. 模型说是别的银行，但是没提别的银行、术语图谱又找到了本行的产品，按知识库问题回答
    4. 置信度一般或者缺信息，追问；已经连续追问 2 次了就转人工
       不过知识库类的意图，图谱已经找到了产品和属性，问题就够清楚了，直接回答
    5. 其他情况按意图走对应的分支
    """
    name = "gate"
    MAX_CLARIFY = 2

    def process(self, state: GraphState) -> StateUpdate:
        high = self.config.intent_threshold
        low = self.config.clarify_threshold
        intent = state.get("intent", "")
        score = state.get("intent_score", 0.0)
        clarify_count = state.get("clarify_count", 0)
        entities = state.get("entities") or {}
        missing = []  # TODO: 按意图检查必填信息，比如冻结卡需要卡号后四位

        if state.get("human_requested"):
            return self._result("handoff", "handoff", missing, clarify_count)

        if transfer_ordinal(state.get("masked_question", "")) is not None or transfer_other(state.get("masked_question", "")):
            if state.get("transfer_candidates") and state.get("transfer_customer_id") == state.get("customer_id"):
                return {**self._result("answer", "account", missing, clarify_count), "intent": "transfer_status"}
            return self._result("clarify", "ask_back", missing, clarify_count)

        if score < low:
            return self._result("handoff", "handoff", missing, clarify_count)

        # 训练数据里没有问本行信用卡年费的句子，模型会把它当成别的银行（见 intents.py 的 OTHER_BANK_RE）
        if (intent == "out_of_scope" and state.get("sub_intent") == "other_company" and entities.get("products")
                and not mentions_other_bank(state.get("masked_question", ""))):
            self.log_step(self.name, "模型说是别的银行，但图谱找到了本行的产品，按知识库问题回答")
            return self._result("answer", "rag", missing, clarify_count)

        # 训练数据里也没有问转账限额的句子，这种问题模型的置信度会偏低
        graph_hit = entities.get("products") and entities.get("attr")
        if score < high and graph_hit and INTENT_ROUTES.get(intent) == "rag":
            self.log_step(self.name, "置信度一般，但图谱找到了产品和属性，直接回答")
            return self._result("answer", "rag", missing, clarify_count)

        if score < high or missing or intent == "unknown":
            if clarify_count >= self.MAX_CLARIFY:
                return self._result("handoff", "handoff", missing, clarify_count)
            return self._result("clarify", "ask_back", missing, clarify_count)

        route = INTENT_ROUTES.get(intent, "rag")
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
        if state.get("private_input") and state.get("route") == "rag":
            return {"answer": pick(lang, "This may involve private account information. Please ask to check your balance or a specific transfer.",
                                   "您的问题可能涉及私人账户资料，请说明是要查询余额还是某笔转账。")}
        return {"answer": pick(lang,
                               "Sorry, could you tell me a bit more? For example: card, transfer, fees or account balance.",
                               "不好意思，能再具体说明一下吗？例如：银行卡、转账汇款、手续费或账户余额。")}


class HandoffNode(BaseNode):
    name = "handoff"

    def process(self, state: GraphState) -> StateUpdate:
        urgent = state.get("intent") == "fraud_report"
        lang = state.get("lang", "en")
        priority = "urgent" if urgent else "normal"
        try:
            ticket_id = create_ticket(state, priority)
        except (MySQLError, ValueError) as e:
            self.logger.error(f"人工工单创建失败：{e}")
            return {"priority": priority, "ticket_id": None,
                    "answer": pick(lang, "I couldn't create a support ticket. Please contact the bank directly.",
                                   "暂时无法创建人工工单，请直接联系银行客服。")}
        return {"priority": priority, "ticket_id": ticket_id,
                "answer": pick(lang, f"A support ticket has been created (#{ticket_id}). "
                                     "Human-agent notification is not yet available; please contact the bank directly.",
                               f"已创建人工工单（编号 {ticket_id}）。目前尚未接通人工通知，请直接联系银行客服。")}


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
        # 账户结果只返回给客户，不放进会传给外部模型的对话历史
        private = state.get("route") in ("account", "handoff") or state.get("private_input")
        history_answer = "[私人业务结果已展示]" if private else answer
        updates = []
        if private:
            latest_user = next((m for m in reversed(state.get("messages") or []) if isinstance(m, HumanMessage)), None)
            if latest_user is not None and latest_user.id:
                updates.append(HumanMessage("[私人业务问题已隐藏]", id=latest_user.id))
        updates.append(AIMessage(history_answer))
        return {"answer": answer, "messages": updates}


if __name__ == "__main__":
    from src.schema.state import create_default_state

    # 实体抽取：第二个参数是上一轮留下来的 entities
    node = EntityExtractionNode()
    tests = [
        ("冻结我的信用卡尾号 7730", {}),
        ("汇钱回家要几天？", {"products": ["mastercard_credit"], "card_last4": "7730"}),  # 卡号留着，产品换成这一轮的
        ("我想激活新卡", {"products": ["overseas_tt"], "attr": "arrival_time", "card_last4": "7730"}),  # 上一轮的产品去掉
        ("PayNow 和 FAST 哪个限额高", {}),
        ("ending 4821 我要转 SGD 500", {}),
    ]
    for question, last_entities in tests:
        state = create_default_state(masked_question=question, entities=last_entities)
        print(f"{question}\n    -> {node(state)['entities']}")
