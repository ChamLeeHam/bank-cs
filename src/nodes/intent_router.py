# 意图识别节点
# 1. 问候、"嗯嗯"这类没有内容的消息用规则判断
# 2. 其他消息交给微调过的 Qwen2.5-1.5B（LoRA），输出 "一级意图/二级意图"
# 3. 模型加载不了的时候（没有 GPU、没有模型文件）退回关键词规则
import logging
import re
import time

import torch

from src.intents import ALL_LABELS, INTENT_SYSTEM, INTENTS, LABEL_SEP, split_label
from src.nodes.stub_nodes import KEYWORD_RULES, router_text
from src.schema.base import BaseNode, StateUpdate
from src.schema.config import get_config
from src.schema.state import GraphState
from src.utils.fraud_context import fraud_context
from src.utils.history_utils import (annual_fee_followup, fraud_confirmed_followup, fraud_general_followup, history_of,
                                    negated_fraud_limit_question, replacement_fee_stolen_followup,
                                    statement_fee_followup, stolen_card_clarification)

logger = logging.getLogger(__name__)

GREETING_RE = re.compile(r"^(hi|hello|hey|你好|您好|哈喽|早安|早上好|good (morning|afternoon|evening))[!！.。,，\s]*$",
                         re.IGNORECASE)
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
    r"(?:我的|本人)(?:最近一?|上一|最后一)(?:笔|次)?(?:银行卡|信用卡|借记卡|卡片)(?:交易|消费)",
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
        if signal == "reported" or fraud_confirmed_followup(current, history):
            return {"intent": "fraud_report", "sub_intent": "", "intent_score": 0.99,
                    "router_backend": "keyword", "fraud_signal": "reported"}
        if signal == "hypothetical":
            return {"intent": "unknown", "sub_intent": "", "intent_score": UNKNOWN_SCORE,
                    "router_backend": "keyword", "fraud_signal": "hypothetical"}

        # 1. 规则：问候、没有内容的消息
        if GREETING_RE.match(current):
            return {"intent": "greeting", "sub_intent": "hello", "intent_score": 0.95, "router_backend": "keyword"}
        if fraud_general_followup(current, history) or negated_fraud_limit_question(current) or not topic:
            return {"intent": "unknown", "sub_intent": "", "intent_score": UNKNOWN_SCORE, "router_backend": "keyword"}
        if FILLER_RE.match(current):
            return {"intent": "unknown", "sub_intent": "", "intent_score": UNKNOWN_SCORE, "router_backend": "keyword"}

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

        # 2. 模型用不了，用关键词
        if not self._model_ready():
            intent, score = keyword_intent(joined)
            return {"intent": intent, "sub_intent": "", "intent_score": score, "router_backend": "keyword"}

        # 3. 先只看这一条消息。如果上一轮是追问、这一条又不太确定，就和上一条消息拼起来再判断一次
        result = self._classify(current)
        if joined != current and result["intent_score"] < self.config.intent_threshold:
            with_context = self._classify(joined)
            if with_context["intent_score"] > result["intent_score"]:
                result = with_context
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
