# 意图识别节点
# 1. 问候、"嗯嗯"这类没有内容的消息用规则判断
# 2. 其他消息交给微调过的 Qwen2.5-1.5B（LoRA），输出 "一级意图/二级意图"
# 3. 模型加载不了的时候（没有 GPU、没有模型文件）退回关键词规则
import logging
import re
import time

import torch

from src.intents import ALL_LABELS, INTENT_SYSTEM, INTENTS, LABEL_SEP, split_label
from src.nodes.loan_calculator import (
    LOAN_CALCULATION_INTENT, bare_amount_followup, implicit_amount_followup, is_loan_calculation_request,
    loan_followup_arguments,
)
from src.nodes.stub_nodes import KEYWORD_RULES, is_internal_role_question, router_text
from src.schema.base import BaseNode, StateUpdate
from src.schema.config import get_config
from src.schema.state import GraphState
from src.utils.banking_terminology import banking_term_faq_intent
from src.utils.financial_products import financial_product_query
from src.utils.fraud_context import (fraud_action_status_question, fraud_context,
                                     fraud_comparison_requests_action, fraud_dispute_comparison_question,
                                     unfamiliar_merchant_name_question)
from src.utils.history_utils import (annual_fee_followup, fraud_confirmed_followup, fraud_general_followup, history_of,
                                    negated_fraud_limit_question, replacement_fee_stolen_followup,
                                    statement_fee_followup, statement_fee_general_followup, stolen_card_clarification)
from src.utils.query_expansion_utils import (expand_card_delivery_query,
                                             is_unanchored_overdue_delivery_followup,
                                             resolve_card_delivery_followup)
from src.utils.reference_bank_utils import (is_reference_policy_question, reference_question_intent,
                                            resolve_reference_bank)

logger = logging.getLogger(__name__)

GREETING_RE = re.compile(r"^(hi|hello|hey|你好|您好|哈喽|早安|早上好[呀啊吗]?|嗨(?:[，,\s]+(?:hi|hello|hey))?|good (morning|afternoon|evening))[!！.。,，\s]*$",
                         re.IGNORECASE)
THANKS_RE = re.compile(
    r"^(?:(?:好|好的|嗯|明白了|知道了)[，,、\s]*)?(?:谢谢(?:你)?(?:的)?(?:解答|回答|帮助)?|多谢|感谢(?:你)?(?:的)?(?:解答|回答|帮助)?)[。！!？?\s]*$|"
    r"^(?:(?:okay|ok|great|got it)[,，\s]*)?(?:thanks?|thank you(?: for (?:the )?(?:answer|help))?)[.!?\s]*$",
    re.IGNORECASE,
)
GOODBYE_RE = re.compile(r"^(再见|拜拜|回头见|祝你再见|bye(?: bye)?|goodbye|see you)[。！!,.\s]*$", re.IGNORECASE)
ACKNOWLEDGEMENT_RE = re.compile(r"^(明白了|知道了|收到|好的|好|ok|okay|got it)[。！!,.，\s]*$", re.IGNORECASE)
# 训练数据里没有"嗯嗯"、"那个"这种消息，交给模型的话它会随便选一个意图，所以直接当成 unknown
FILLER_RE = re.compile(r"^(嗯+|啊+|哦+|呃+|那个|就是那个|这个|是的|对|好的?|ok(ay)?|hmm+|um+|yes|no|\?+|？+)[!！.。,，?？~\s]*$",
                       re.IGNORECASE)
UNKNOWN_SCORE = 0.5  # 判断不了的时候给 0.5，gate 会去追问

# 账单上的一般费用询问应先走知识库；明确表示非本人交易、盗刷或费用错误时，
# 不应用这条规则覆盖模型的风险分类。
_STATEMENT_RE = re.compile(r"\bstatement\b|账单|月结单", re.IGNORECASE)
_STATEMENT_CHARGE_RE = re.compile(r"\b(?:charge|fee|small charge|small fee)\b|扣费|费用|扣款", re.IGNORECASE)
_STATEMENT_EXPLANATION_RE = re.compile(
    r"看不懂|不明白|不清楚|什么意思|是什么|什么费用|哪种费用|解释一下|what(?:'s| is)?|"
    r"what does .{1,30} mean|not sure what|don't understand|do not understand|can you explain|why is there",
    re.IGNORECASE,
)
_STATEMENT_RISK_RE = re.compile(
    r"不是我|非本人|不是本人|未经授权|盗刷|可疑|不认识|没(?:有)?做过|重复扣|扣了两次|扣错|多扣|收错|金额不对|退款|"
    r"\b(?:not mine|unauthori[sz]ed|fraud|suspicious|stolen|don't recognize|do not recognize|"
    r"never made|didn't make|did not make|charged twice|double charged|duplicate charge|wrong amount|"
    r"charged by mistake|refund)\b",
    re.IGNORECASE,
)
_LATEST_CARD_TRANSACTION_RE = re.compile(
    r"\b(?:my|mine)\s+(?:last|latest|most recent)\s+card\s+(?:transaction|payment)\b|"
    r"\b(?:last|latest|most recent)\s+(?:purchase|payment|transaction)\s+(?:on|with|using)\s+my\s+(?:(?:debit|credit|bank)\s+)?card\b|"
    r"(?:我的|本人)(?:最近一?|上一|最后一)(?:笔|次)?(?:银行卡|信用卡|借记卡|卡片)(?:交易|消费)|"
    r"(?:我(?:的)?\s*)?(?:银行卡|信用卡|借记卡|\b(?:debit|credit|bank) card\b)\s*"
    r"(?:最近一?|上一|最后一)(?:笔|次)?\s*(?:消费|交易|扣款|transaction\b|purchase\b|payment\b)",
    re.IGNORECASE,
)
_DISPUTE_PATTERNS = (
    ("transaction_charged_twice", re.compile(
        r"\b(?:charged twice|double[- ]charged|charged two times|charged more than once|duplicate charge)\b|"
        r"重复扣款|重复收费|(?:被)?扣了两次|扣款两次", re.IGNORECASE)),
    ("Refund_not_showing_up", re.compile(
        r"\brefund\b.{0,35}\b(?:not|never|hasn't|has not|didn't|did not)\b.{0,20}\b(?:arriv\w*|show\w* up|appear\w*|receiv\w*|credit\w*)\b|"
        r"\brefund\b.{0,25}\b(?:missing|not showing up)\b|"
        r"退款.{0,8}(?:未到账|没到账|未收到|没收到|还没到)", re.IGNORECASE)),
    ("transaction_dispute", re.compile(
        r"\bchargebacks?\b|\bdisput(?:e|ed|ing)\b.{0,25}\b(?:card|credit card|bank|transaction|payment|charge)\b|"
        r"\b(?:wrong amount|incorrect amount|amount is wrong)\b.{0,25}\b(?:charge|payment|transaction|card|cash)\b|"
        r"拒付|交易争议|交易不认可|金额不符|金额不对|多扣了?钱", re.IGNORECASE)),
)


_CARD_REPAYMENT_RE = re.compile(r"信用卡.{0,12}还款|\bcredit[- ]card (?:repayment|payment)\b", re.I)
_REPAYMENT_UNPOSTED_RE = re.compile(
    r"(?:还没|尚未|未|没有|没)(?:显示|入账|到账)|"
    r"\b(?:not|hasn't|has not) (?:been )?(?:posted|appeared|credited)\b", re.I
)
_REPAYMENT_GUIDANCE_RE = re.compile(
    r"(?:怎么|如何|该|需要).{0,10}(?:核对|处理|排查)|"
    r"\bwhat (?:should|can|do) i (?:check|do)\b", re.I
)
_ACCOUNT_PROOF_RE = re.compile(r"账户(?:归属|余额)?证明|\baccount (?:confirmation|certificate|proof)\b", re.I)
_ACCOUNT_STATEMENT_RE = re.compile(r"对账单|月结单|\b(?:monthly |account |bank )?statement\b", re.I)
_DOCUMENT_COMPARISON_RE = re.compile(
    r"区别|不同|互相替代|互相代替|(?:能|可)(?:否|不能|不可以|以)?(?:用.{0,20})?(?:替代|代替)|"
    r"\b(?:difference|differ|interchangeable|substitute|instead of)\b", re.I
)
_PERSONAL_ACTION_RE = re.compile(
    r"(?:帮我|请|能否|可以).{0,5}(?:查(?:询)?(?:一下)?|开具|出具|下载|打印)|"
    r"\b(?:can you|could you|please)\s+(?:check|look up|issue|generate|download|print)\b", re.I
)


def _billing_knowledge_intent(text):
    """Match general billing and document guidance before the model fallback."""
    if _STATEMENT_RISK_RE.search(text) or _PERSONAL_ACTION_RE.search(text):
        return None
    if (_CARD_REPAYMENT_RE.search(text) and _REPAYMENT_UNPOSTED_RE.search(text)
            and _REPAYMENT_GUIDANCE_RE.search(text)):
        return "fees_rates"
    if (_ACCOUNT_STATEMENT_RE.search(text) and _ACCOUNT_PROOF_RE.search(text)
            and _DOCUMENT_COMPARISON_RE.search(text)):
        return "account_service"
    return banking_term_faq_intent(text)


def _general_statement_charge_question(text):
    """识别只是在询问账单常见费用类型的问题，排除疑似非本人或错误扣款。"""
    return (
        bool(_STATEMENT_RE.search(text))
        and bool(_STATEMENT_CHARGE_RE.search(text))
        and bool(_STATEMENT_EXPLANATION_RE.search(text))
        and not _STATEMENT_RISK_RE.search(text)
    )

_intent_model = None
_intent_tokenizer = None
device = "cuda" if torch.cuda.is_available() else "cpu"


def get_intent_model():
    """加载 Qwen2.5-1.5B + LoRA，只加载一次"""
    global _intent_model, _intent_tokenizer
    if _intent_model is None:
        from peft import PeftModel
        from transformers import AutoModelForCausalLM, AutoTokenizer

        config = get_config()
        if not config.intent_adapter.exists():
            raise FileNotFoundError(f"找不到 LoRA 权重：{config.intent_adapter}，需要先训练")
        start = time.perf_counter()
        dtype = torch.bfloat16 if device == "cuda" else torch.float32
        _intent_tokenizer = AutoTokenizer.from_pretrained(config.intent_base_model)
        base = AutoModelForCausalLM.from_pretrained(config.intent_base_model, dtype=dtype)
        _intent_model = PeftModel.from_pretrained(base, config.intent_adapter).to(device).eval()
        logger.info(f"意图模型加载完成，设备：{device}，用时 {time.perf_counter() - start:.1f}s")
    return _intent_model, _intent_tokenizer


def predict(text):
    """返回 (模型输出的标签, 一级意图的置信度)"""
    model, tokenizer = get_intent_model()
    messages = [{"role": "system", "content": INTENT_SYSTEM}, {"role": "user", "content": text}]
    prompt = tokenizer.apply_chat_template(messages, tokenize=False, add_generation_prompt=True)
    inputs = tokenizer(prompt, return_tensors="pt").to(device)
    with torch.no_grad():
        out = model.generate(**inputs, max_new_tokens=32, do_sample=False, output_scores=True,
                             return_dict_in_generate=True, pad_token_id=tokenizer.eos_token_id)

    new_tokens = out.sequences[0, inputs["input_ids"].shape[1]:]
    label = ""
    if len(new_tokens):
        label = tokenizer.decode(new_tokens, skip_special_tokens=True).strip().splitlines()[0].strip()

    # 置信度：把 "/" 前面每个 token 的概率乘起来，就是模型生成这个一级意图的概率
    score = 1.0
    decoded = ""
    for step, token_id in enumerate(new_tokens):
        probs = torch.softmax(out.scores[step][0].float(), dim=-1)
        score *= float(probs[token_id])
        decoded += tokenizer.decode([int(token_id)])
        if LABEL_SEP in decoded:
            break
    return label, round(score, 4)


def chat(messages, max_new_tokens=256):
    """关掉 LoRA 就是原来的 Qwen，可以用来生成知识库的回答，不用再加载一个模型"""
    model, tokenizer = get_intent_model()
    prompt = tokenizer.apply_chat_template(messages, tokenize=False, add_generation_prompt=True)
    inputs = tokenizer(prompt, return_tensors="pt").to(device)
    with torch.no_grad(), model.disable_adapter():
        out = model.generate(**inputs, max_new_tokens=max_new_tokens, do_sample=False,
                             repetition_penalty=1.05, pad_token_id=tokenizer.eos_token_id)
    return tokenizer.decode(out[0, inputs["input_ids"].shape[1]:], skip_special_tokens=True).strip()


def keyword_intent(text):
    """关键词规则，模型用不了的时候用"""
    for intent, pattern in KEYWORD_RULES:
        if re.search(pattern, text, re.IGNORECASE):
            return intent, 0.9
    return "unknown", UNKNOWN_SCORE


class IntentRouterNode(BaseNode):
    name = "intent_router"

    def __init__(self):
        super().__init__()
        self.model_error = None  # 模型加载失败过一次，之后就直接用关键词

    def process(self, state: GraphState) -> StateUpdate:
        self.log_step(self.name, "开始识别意图")
        current = state.get("masked_question", "").strip()
        history = history_of(state)
        signal, topic = fraud_context(current)
        comparison_question = fraud_dispute_comparison_question(current)
        urgent_comparison = comparison_question and (state.get("human_requested")
                                                      or fraud_comparison_requests_action(current))
        if urgent_comparison:
            return {"intent": "fraud_report", "sub_intent": "", "intent_score": 0.99,
                    "router_backend": "keyword", "fraud_signal": "reported",
                    "fraud_confirmation_pending": False}
        if comparison_question:
            return {"intent": "card_service", "sub_intent": "lost_or_stolen_card", "intent_score": 0.99,
                    "router_backend": "keyword", "fraud_signal": "none",
                    "fraud_confirmation_pending": False}
        if signal == "reported" or fraud_confirmed_followup(current, history, state.get("fraud_confirmation_pending")):
            return {"intent": "fraud_report", "sub_intent": "", "intent_score": 0.99,
                    "router_backend": "keyword", "fraud_signal": "reported"}
        if unfamiliar_merchant_name_question(current):
            return {"intent": "card_service", "sub_intent": "lost_or_stolen_card", "intent_score": 0.99,
                    "router_backend": "keyword", "fraud_signal": "none",
                    "fraud_confirmation_pending": False}
        if signal == "hypothetical":
            return {"intent": "unknown", "sub_intent": "", "intent_score": UNKNOWN_SCORE,
                    "router_backend": "keyword", "fraud_signal": "hypothetical"}
        if fraud_action_status_question(current, state.get("fraud_ticket_id")):
            return {"intent": "unknown", "sub_intent": "", "intent_score": UNKNOWN_SCORE, "router_backend": "keyword"}

        # Explicit, general questions about another bank's published policy use the isolated reference corpus.
        # A reported fraud incident was handled above and continues through the existing urgent route.
        reference_bank = resolve_reference_bank(current, history)
        if is_reference_policy_question(current, reference_bank):
            self.log_step(self.name, "明确询问外部银行公开资料，路由到银行参考知识库")
            return {"intent": reference_question_intent(current), "sub_intent": "", "intent_score": 0.99,
                    "router_backend": "keyword", "fraud_signal": "none",
                    "fraud_confirmation_pending": False}

        # 1. 规则：问候、没有内容的消息
        if GREETING_RE.match(current):
            return {"intent": "greeting", "sub_intent": "hello", "intent_score": 0.95, "router_backend": "keyword"}
        if THANKS_RE.fullmatch(current):
            return {"intent": "greeting", "sub_intent": "thanks", "intent_score": 0.99, "router_backend": "keyword"}
        if GOODBYE_RE.fullmatch(current):
            return {"intent": "greeting", "sub_intent": "goodbye", "intent_score": 0.99, "router_backend": "keyword"}
        if ACKNOWLEDGEMENT_RE.fullmatch(current) and history and history[-1][0] == "客服":
            return {"intent": "greeting", "sub_intent": "acknowledgement", "intent_score": 0.99,
                    "router_backend": "keyword"}
        if fraud_general_followup(current, history, state.get("fraud_confirmation_pending")):
            # 用户已说明只是了解一般流程：结束盗刷确认状态，转知识库给出有来源的说明。
            return {"intent": "card_service", "sub_intent": "lost_or_stolen_card", "intent_score": 0.99,
                    "router_backend": "keyword", "fraud_confirmation_pending": False}

        # Card delivery is a safe FAQ. Resolve its common standalone phrasing before
        # the classifier so low-confidence questions do not stop before RAG retrieval.
        delivery_followup = resolve_card_delivery_followup(current, history)
        if delivery_followup:
            return {"intent": "card_service", "sub_intent": "card_delivery_estimate", "intent_score": 0.99,
                    "router_backend": "keyword", "fraud_signal": "none",
                    "resolved_question": delivery_followup}
        if is_unanchored_overdue_delivery_followup(current, history):
            return {"intent": "unknown", "sub_intent": "", "intent_score": UNKNOWN_SCORE,
                    "router_backend": "keyword", "fraud_signal": "none"}
        if expand_card_delivery_query(current):
            return {"intent": "card_service", "sub_intent": "card_delivery_estimate", "intent_score": 0.99,
                    "router_backend": "keyword", "fraud_signal": "none"}
        if negated_fraud_limit_question(current) or not topic:
            return {"intent": "unknown", "sub_intent": "", "intent_score": UNKNOWN_SCORE, "router_backend": "keyword"}
        if FILLER_RE.match(current):
            return {"intent": "unknown", "sub_intent": "", "intent_score": UNKNOWN_SCORE, "router_backend": "keyword"}
        bare_amount = bare_amount_followup(current)
        if ((bare_amount and not state.get("loan_calculation_context") and not state.get("loan_calculation_pending"))
                or (not history and implicit_amount_followup(current))):
            return {"intent": "unknown", "sub_intent": "ambiguous_amount",
                    "intent_score": UNKNOWN_SCORE, "router_backend": "keyword"}

        # 只把否认之后的业务问题交给分类器，避免否定词被重新识别为盗刷。
        joined = router_text(state) if topic == current else topic
        current = topic
        for sub_intent, pattern in _DISPUTE_PATTERNS:
            if pattern.search(current):
                self.log_step(self.name, "检测到明确的交易争议，按争议退款意图转人工")
                return {
                    "intent": "dispute_refund",
                    "sub_intent": sub_intent,
                    "intent_score": 0.99,
                    "router_backend": "keyword",
                }

        if (_LATEST_CARD_TRANSACTION_RE.search(current)
                and not re.search(r"冻结|挂失|盗刷|争议|退款|freeze|block|stolen|fraud|disput|refund", current, re.IGNORECASE)):
            return {"intent": "card_transaction_inquiry", "sub_intent": "", "intent_score": 0.99,
                    "router_backend": "keyword"}

        choice = stolen_card_clarification(current, history_of(state))
        if choice:
            return {"intent": "card_lost_freeze" if choice == "freeze" else "card_service",
                    "sub_intent": "", "intent_score": 0.99, "router_backend": "keyword"}
        if statement_fee_general_followup(current, history_of(state)):
            return {"intent": "fees_rates", "sub_intent": "extra_charge_on_statement", "intent_score": 0.99,
                    "router_backend": "keyword"}
        matched, _ = statement_fee_followup(current, history_of(state))
        if matched:
            return {"intent": "fees_rates", "sub_intent": "", "intent_score": 0.99, "router_backend": "keyword"}
        if annual_fee_followup(current, history_of(state)):
            return {"intent": "fees_rates", "sub_intent": "", "intent_score": 0.99, "router_backend": "keyword"}
        if replacement_fee_stolen_followup(current, history_of(state)):
            return {"intent": "fees_rates", "sub_intent": "", "intent_score": 0.99, "router_backend": "keyword"}

        # “statement 上有一笔看不懂的小额 charge 是什么”属于一般费用知识查询。
        # 明确的非本人交易、盗刷、重复/错误扣款等仍留给风险意图分类。
        if _general_statement_charge_question(current):
            self.log_step(self.name, "账单费用解释问题，按费用 FAQ 路由")
            return {
                "intent": "fees_rates",
                "sub_intent": "extra_charge_on_statement",
                "intent_score": 0.99,
                "router_backend": "keyword",
            }

        billing_knowledge_intent = _billing_knowledge_intent(current)
        if billing_knowledge_intent and not state.get("private_input") and not state.get("human_requested"):
            return {"intent": billing_knowledge_intent, "sub_intent": "", "intent_score": 0.99,
                    "router_backend": "keyword"}

        if not state.get("private_input") and not state.get("human_requested"):
            loan_context = state.get("loan_calculation_context") or {}
            followup = loan_followup_arguments(current, loan_context)
            if followup or is_loan_calculation_request(current, state.get("loan_calculation_pending", False)):
                return {"intent": "account_service", "sub_intent": LOAN_CALCULATION_INTENT,
                        "intent_score": 0.99, "router_backend": "keyword"}

        if (not state.get("private_input") and not state.get("human_requested")
                and financial_product_query(current) is not None):
            return {"intent": "account_service", "sub_intent": "", "intent_score": 0.99,
                    "router_backend": "keyword"}

        if is_internal_role_question(current) and not state.get("human_requested"):
            return {"intent": "out_of_scope", "sub_intent": "non_banking", "intent_score": 0.99,
                    "router_backend": "keyword"}

        # 2. 模型用不了，用关键词
        if not self._model_ready():
            intent, score = keyword_intent(joined)
            return {"intent": intent, "sub_intent": "", "intent_score": score, "router_backend": "keyword",
                    "resolved_question": joined if joined != current else ""}

        # 3. 如果上一轮是追问，客户的短补充（如“银行卡”）必须和原问题一起分类。
        # 单独分类补充内容可能得到高置信度但错误的宽泛标签，丢失原问题的具体属性。
        if joined != current:
            with_context = self._classify(joined)
            result = with_context
            # 完整上下文仍不确定时，再比较只看当前消息的结果，保留置信度更高者。
            if with_context["intent_score"] < self.config.intent_threshold:
                current_only = self._classify(current)
                if current_only["intent_score"] > with_context["intent_score"]:
                    result = current_only
        else:
            result = self._classify(current)
        if joined != current:
            # 同一份上下文既用于分类，也用于后续 RAG 检索，避免正确识别意图后仍拿短补充词检索。
            result["resolved_question"] = joined
        self.log_step(self.name, f"意图：{result['intent']}/{result['sub_intent']}，置信度 {result['intent_score']}")
        return result

    def _model_ready(self):
        if self.config.intent_backend != "finetuned" or self.model_error:
            return False
        try:
            get_intent_model()
            return True
        except Exception as e:
            self.model_error = f"{type(e).__name__}: {e}"
            self.logger.warning(f"意图模型加载失败，改用关键词规则：{self.model_error}")
            return False

    def _classify(self, text):
        label, score = predict(text)
        intent, sub_intent = split_label(label)
        # 模型偶尔会输出标签表里没有的标签：一级意图对的话保留一级，否则当成 unknown
        if label not in ALL_LABELS:
            self.logger.warning(f"模型输出的标签不存在：{label}")
            if intent in INTENTS:
                sub_intent = ""
            else:
                intent, sub_intent, score = "unknown", "", UNKNOWN_SCORE
        return {"intent": intent, "sub_intent": sub_intent, "intent_score": score, "router_backend": "finetuned"}


if __name__ == "__main__":
    from src.schema.base import setup_logging

    setup_logging(logging.WARNING)
    node = IntentRouterNode()
    samples = ["How much is the overseas TT fee?", "我的 card 不见了，帮我 freeze", "我昨天给妈妈的 TT 到了没？",
               "There's a payment I didn't make!", "同一笔被扣了两次钱", "你们的 App 一直闪退，太差了",
               "Which stock should I buy?", "嗯嗯", "你好", "我想 check 一下我的 balance"]
    for q in samples:
        start = time.perf_counter()
        r = node.process({"masked_question": q, "messages": [], "clarify_count": 0})
        ms = (time.perf_counter() - start) * 1000
        print(f"{r['router_backend']:<9} {r['intent']:<17} {r['sub_intent']:<40} {r['intent_score']:<6} {ms:6.0f}ms  {q}")
