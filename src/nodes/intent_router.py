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

logger = logging.getLogger(__name__)

GREETING_RE = re.compile(r"^(hi|hello|hey|你好|您好|哈喽|早安|早上好|good (morning|afternoon|evening))[!！.。,，\s]*$",
                         re.IGNORECASE)
# 训练数据里没有"嗯嗯"、"那个"这种消息，交给模型的话它会随便选一个意图，所以直接当成 unknown
FILLER_RE = re.compile(r"^(嗯+|啊+|哦+|呃+|那个|就是那个|这个|是的|对|好的?|ok(ay)?|hmm+|um+|yes|no|\?+|？+)[!！.。,，?？~\s]*$",
                       re.IGNORECASE)
UNKNOWN_SCORE = 0.5  # 判断不了的时候给 0.5，gate 会去追问

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

        # 1. 规则：问候、没有内容的消息
        if GREETING_RE.match(current):
            return {"intent": "greeting", "sub_intent": "hello", "intent_score": 0.95, "router_backend": "keyword"}
        if FILLER_RE.match(current):
            return {"intent": "unknown", "sub_intent": "", "intent_score": UNKNOWN_SCORE, "router_backend": "keyword"}

        # 2. 模型用不了，用关键词
        if not self._model_ready():
            intent, score = keyword_intent(router_text(state))
            return {"intent": intent, "sub_intent": "", "intent_score": score, "router_backend": "keyword"}

        # 3. 先只看这一条消息。如果上一轮是追问、这一条又不太确定，就和上一条消息拼起来再判断一次
        result = self._classify(current)
        joined = router_text(state)
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
