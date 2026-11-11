"""
意图路由节点（《需求文档》F1 / F7）：关键词规则前置短路，微调模型（Qwen2.5-1.5B + LoRA）兜底

结构参考课程 day04 的 IntentClassifyNode：
  1. 关键词规则只放"几乎不可能误判"的场景（问候），命中即返回，省一次模型推理
  2. 其余交给微调模型，输出 "一级意图/二级意图"
  3. 置信度 = 一级意图部分各个 token 概率的乘积（gate 用它和 0.7 / 0.4 比较）
  4. 输出校验：不在 ALL_LABELS 中时，一级意图有效则保留一级（二级置空），否则视为 unknown -> 追问
  5. 没有 GPU / 模型文件 / torch 时自动降级为关键词规则（router_backend 记录实际使用的方式）

单独测试（在项目根目录）：
    uv run python -m src.nodes.intent_router
"""
import logging
import re
import threading
import time

from src.intents import ALL_LABELS, INTENT_SYSTEM, INTENTS, LABEL_SEP, split_label
from src.nodes.stub_nodes import KEYWORD_RULES, router_text
from src.schema.base import BaseNode, StateUpdate
from src.schema.config import get_config
from src.schema.state import GraphState

logger = logging.getLogger("bank.intent_model")

# 前置规则：只放问候（训练数据中没有 greeting）
GREETING_RE = re.compile(r"^(hi|hello|hey|你好|您好|哈喽|早安|早上好|good (morning|afternoon|evening))[!！.。,，\s]*$",
                         re.IGNORECASE)
# 没有内容的消息（"嗯嗯"、"那个"、"ok"）：训练数据里没有这类句子，模型会硬选一个意图（如 chitchat -> 拒答），
# 所以用规则直接判为 unknown，让 gate 追问
FILLER_RE = re.compile(r"^(嗯+|啊+|哦+|呃+|那个|就是那个|这个|是的|对|好的?|ok(ay)?|hmm+|um+|yes|no|\?+|？+)[!！.。,，?？~\s]*$",
                       re.IGNORECASE)
UNKNOWN_SCORE = 0.5  # 无法识别时的分数：落在 0.4~0.7 之间 -> gate 追问


class IntentModel:
    """Qwen2.5-1.5B + LoRA，进程内只加载一次（第一次调用时加载，约 10 秒）"""

    _instance = None
    _lock = threading.Lock()

    def __init__(self):
        import torch
        from peft import PeftModel
        from transformers import AutoModelForCausalLM, AutoTokenizer

        config = get_config()
        if not config.intent_adapter.exists():
            raise FileNotFoundError(f"找不到 LoRA 权重：{config.intent_adapter}（先运行 llamafactory-cli train）")
        self.torch = torch
        self.device = "cuda" if torch.cuda.is_available() else "cpu"
        dtype = torch.bfloat16 if self.device == "cuda" else torch.float32
        start = time.perf_counter()
        self.tokenizer = AutoTokenizer.from_pretrained(config.intent_base_model)
        base = AutoModelForCausalLM.from_pretrained(config.intent_base_model, dtype=dtype)
        self.model = PeftModel.from_pretrained(base, config.intent_adapter).to(self.device).eval()
        logger.info("意图模型已加载：%s，设备 %s，用时 %.1fs", config.intent_adapter.name, self.device,
                    time.perf_counter() - start)

    @classmethod
    def get(cls) -> "IntentModel":
        with cls._lock:
            if cls._instance is None:
                cls._instance = cls()
            return cls._instance

    def predict(self, text: str) -> tuple[str, float]:
        """返回 (模型输出的标签文本, 一级意图置信度)"""
        torch = self.torch
        messages = [{"role": "system", "content": INTENT_SYSTEM}, {"role": "user", "content": text}]
        prompt = self.tokenizer.apply_chat_template(messages, tokenize=False, add_generation_prompt=True)
        inputs = self.tokenizer(prompt, return_tensors="pt").to(self.device)
        with torch.no_grad():
            out = self.model.generate(**inputs, max_new_tokens=32, do_sample=False, output_scores=True,
                                      return_dict_in_generate=True, pad_token_id=self.tokenizer.eos_token_id)
        new_tokens = out.sequences[0, inputs["input_ids"].shape[1]:]
        label = self.tokenizer.decode(new_tokens, skip_special_tokens=True).strip().splitlines()[0].strip() \
            if len(new_tokens) else ""

        # 一级意图置信度：逐个累乘生成 token 的概率，直到已解码的文本中出现分隔符 "/"
        score, decoded = 1.0, ""
        for step, token_id in enumerate(new_tokens):
            probs = torch.softmax(out.scores[step][0].float(), dim=-1)
            score *= float(probs[token_id])
            decoded += self.tokenizer.decode([int(token_id)])
            if LABEL_SEP in decoded:
                break
        return label, round(score, 4)


def keyword_intent(text: str) -> tuple[str, float]:
    """关键词规则（模型不可用时的降级方案，与 Day 1 占位节点相同）"""
    for intent, pattern in KEYWORD_RULES:
        if re.search(pattern, text, re.IGNORECASE):
            return intent, 0.9
    return "unknown", UNKNOWN_SCORE


class IntentRouterNode(BaseNode):
    """意图路由：问候规则 -> 微调模型 -> （模型不可用时）关键词规则"""
    name = "intent_router"

    def __init__(self):
        super().__init__()
        self.model_error: str | None = None  # 模型加载失败的原因，只记录一次，之后直接走关键词

    def _model(self) -> IntentModel | None:
        if self.config.intent_backend != "finetuned" or self.model_error:
            return None
        try:
            return IntentModel.get()
        except Exception as e:  # 没有 torch / 没有模型文件 / 显存不足：降级而不是让整个客服停掉
            self.model_error = f"{type(e).__name__}: {e}"
            self.logger.warning("意图模型不可用，降级为关键词规则：%s", self.model_error)
            return None

    def _classify(self, model: IntentModel, text: str) -> StateUpdate:
        label, score = model.predict(text)
        intent, sub_intent = split_label(label)
        if label not in ALL_LABELS:
            # 输出校验：二级意图不合法但一级合法 -> 保留一级；一级也不合法 -> unknown（gate 会追问）
            self.logger.warning("模型输出不在标签表中：%r", label)
            if intent in INTENTS:
                sub_intent = ""
            else:
                intent, sub_intent, score = "unknown", "", UNKNOWN_SCORE
        return {"intent": intent, "sub_intent": sub_intent, "intent_score": score, "router_backend": "finetuned"}

    def process(self, state: GraphState) -> StateUpdate:
        # 1. 规则短路：问候 / 没有内容的消息（只看本轮消息，不看拼接后的文本）
        current = state.get("masked_question", "").strip()
        if GREETING_RE.match(current):
            return {"intent": "greeting", "sub_intent": "hello", "intent_score": 0.95, "router_backend": "keyword"}
        if FILLER_RE.match(current):  # 追问后再回"那个"也没有新信息；连续追问 2 次后 gate 会转人工
            return {"intent": "unknown", "sub_intent": "", "intent_score": UNKNOWN_SCORE, "router_backend": "keyword"}

        # 2. 微调模型（不可用时用关键词规则）
        model = self._model()
        if model is None:
            intent, score = keyword_intent(router_text(state))
            return {"intent": intent, "sub_intent": "", "intent_score": score, "router_backend": "keyword"}

        # 3. 先只看本轮消息；上一轮是追问、且本轮单独看不够确定时，再拼接上一条消息试一次，取更确定的结果
        #    （客户追问后可能回复碎片"第二笔"，也可能直接换一个完整的新问题）
        result = self._classify(model, current)
        joined = router_text(state)
        if joined != current and result["intent_score"] < self.config.intent_threshold:
            with_context = self._classify(model, joined)
            if with_context["intent_score"] > result["intent_score"]:
                result = with_context
        return result


if __name__ == "__main__":
    from src.schema.base import setup_logging

    setup_logging(logging.INFO)
    node = IntentRouterNode()
    samples = ["How much is the overseas TT fee?", "我的 card 不见了，帮我 freeze", "我昨天给妈妈的 TT 到了没？",
               "There's a payment I didn't make!", "同一笔被扣了两次钱", "你们的 App 一直闪退，太差了",
               "Which stock should I buy?", "嗯嗯", "你好", "我想 check 一下我的 balance"]
    for q in samples:
        start = time.perf_counter()
        r = node.process({"masked_question": q, "messages": [], "clarify_count": 0})
        ms = (time.perf_counter() - start) * 1000
        print(f"{r['router_backend']:<9} {r['intent']:<17} {r['sub_intent']:<40} {r['intent_score']:<6} {ms:6.0f}ms  {q}")
