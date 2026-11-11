"""
构建意图识别的数据集，有英文、中文和中英混杂三种

1. 从 Banking77 抽样，每个意图最多 300 条，二级意图之间平均分
2. Banking77 里没有或者太少的意图，用 DeepSeek 生成英文句子
3. 每个意图里 1/3 保留英文，1/3 翻译成中文，1/3 改成新加坡那种中英混着说的
4. 去重，测试集里和训练集重复的句子删掉，最后输出 jsonl 和 Label Studio 的导入文件

运行：
    uv run python src/scripts/build_intent_dataset.py --trial   # 先少量试跑看看质量
    uv run python src/scripts/build_intent_dataset.py

DeepSeek 返回的结果会缓存在 data/intent/cache/，重新运行不会再调用
"""
import argparse
import hashlib
import json
import math
import random
import sys
import threading
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

PROJECT_ROOT = Path(__file__).parent.parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

import pandas as pd
from langchain_openai import ChatOpenAI

from src.intents import (BANKING77_TO_INTENT, GENERATE_ONLY, INTENTS, NEEDS_TOP_UP, full_label, sub_intents_of)
from src.schema.config import get_config

config = get_config()

# 1. 配置
CONFIG = {
    "raw_dir": config.data_dir / "raw",
    "out_dir": config.data_dir / "intent",
    "cache_file": config.data_dir / "intent" / "cache" / "llm_cache.jsonl",
    "train_per_intent": 300,  # 与 src/intents.py 的 MAX_PER_INTENT 一致
    "test_per_intent": 60,
    "batch_size": 25,  # 每次请求翻译 / 生成的句子数
    "workers": 8,  # 并发请求数
    "seed": 42,
}
LANGS = ["en", "zh", "mixed"]
# 这几个 Banking77 标签里混着两种意图，标注的时候要一条条看
REVIEW_LABELS = {"transfer_not_received_by_recipient", "transfer_timing",
                 "card_payment_fee_charged", "extra_charge_on_statement"}

# 自己定义的二级意图，生成数据时告诉 LLM 每个是什么意思
SUB_INTENT_DESC = {
    "check_balance": "asks how much money is in their own account (balance of savings / current / multi-currency account)",
    "lost_or_stolen_card": "says their card is lost, missing or stolen and wants it frozen or blocked",
    "waiting_time": "complains about waiting too long for the bank's SERVICE: hotline queue, branch queue, slow email or chat "
                    "reply. NOT about a specific transfer, refund or card delivery being late (those are other categories)",
    "staff_attitude": "complains about rude, unhelpful or careless bank staff",
    "app_problem": "complains that the mobile banking app or internet banking crashes, logs out, is slow or buggy",
    "unfair_fee": "complains that the bank's fees or charges are unfair or too high IN GENERAL. NOT asking what a fee is, "
                  "and NOT saying a specific charge on their account is wrong",
    "investment_advice": "asks which stock, fund, crypto or investment to buy, or for personal financial advice",
    "chitchat": "small talk unrelated to banking tasks (jokes, weather, how are you, asking about the bot itself)",
    "other_company": "asks about ANOTHER company's own products or policies (telco plan, airline fee, food delivery refund, "
                     "another bank's credit card). The question must NOT involve this bank's account, card, PayNow or transfers",
    "non_banking": "asks a general question unrelated to banking (recipes, homework, directions, news)",
}


# 2. 调用 LLM，结果缓存起来
class LLMClient:
    """调 DeepSeek，返回 JSON。同样的提示词只调一次，结果存到文件里"""

    def __init__(self, cache_file: Path):
        # 生成句子温度高一点，句子多样一些；翻译温度低一点，意思不要跑偏
        self.llms = {t: ChatOpenAI(model=config.deepseek_model, api_key=config.deepseek_api_key,
                                   base_url=config.deepseek_base_url, temperature=t, timeout=120, max_retries=3,
                                   model_kwargs={"response_format": {"type": "json_object"}})
                     for t in (0.3, 1.0)}
        self.cache_file = cache_file
        self.cache: dict[str, list[str]] = {}
        self.lock = threading.Lock()
        self.calls = 0
        if cache_file.exists():
            for line in cache_file.read_text(encoding="utf-8").splitlines():
                row = json.loads(line)
                self.cache[row["key"]] = row["items"]
        cache_file.parent.mkdir(parents=True, exist_ok=True)

    def items(self, prompt: str, expected: int, exact: bool, temperature: float) -> list[str]:
        """发送 prompt，返回 {"items": [...]} 中的字符串列表；exact=True 时数量必须与 expected 相同"""
        key = hashlib.sha256(prompt.encode("utf-8")).hexdigest()
        if key in self.cache:
            return self.cache[key]
        for attempt in range(3):
            reply = self.llms[temperature].invoke(prompt).content
            try:
                items = [str(s).strip() for s in json.loads(reply)["items"]]
            except (json.JSONDecodeError, KeyError, TypeError):
                continue
            items = [s for s in items if s]
            if (len(items) == expected) if exact else (len(items) >= expected * 0.8):
                with self.lock:
                    self.calls += 1
                    self.cache[key] = items
                    with self.cache_file.open("a", encoding="utf-8") as f:
                        f.write(json.dumps({"key": key, "items": items}, ensure_ascii=False) + "\n")
                return items
        raise RuntimeError(f"LLM 连续 3 次返回格式不对，prompt 开头：{prompt[:80]}")


TRANSLATE_PROMPTS = {
    "zh": """You are preparing training data for a Singapore bank's customer-service chatbot.
Rewrite each English customer message as a Singapore Chinese customer would type it in Simplified Chinese.
Rules:
- Keep EXACTLY the same meaning and request. Do not add or remove details.
- Natural, casual chat style.
- Translate EVERYTHING into Chinese, including slang (wa lao, sia, lah -> natural Chinese equivalents) and common
  English words (app -> 手机银行/App 均可, crash -> 闪退, status -> 进度, refund -> 退款, fee -> 费用).
  Only these may stay in English: brand / company names (Visa, Mastercard, UnionPay, PayNow, GIRO, Singtel, Grab ...),
  and the abbreviations ATM, PIN, OTP, SGD, NRIC, MRT, App.
- One output per input, same order.
Return JSON: {{"items": ["...", ...]}} with exactly {n} items.

Input:
{texts}""",
    "mixed": """You are preparing training data for a Singapore bank's customer-service chatbot.
Rewrite each English customer message in Singapore-style Chinese-English code-switching, the way people in Singapore
really type: Chinese sentence structure with common English words mixed in, for example
"我的 card 被 block 了", "昨天 transfer 的钱还没 receive 到", "这个 charge 是什么来的", "可以帮我 check 一下 balance 吗".
Rules:
- Keep EXACTLY the same meaning and request. Do not add or remove details.
- Every output must contain BOTH Chinese characters and at least one English word.
- Vary how much English is used. Occasionally add a Singlish particle (lah, leh, ah), but not in every sentence.
- One output per input, same order.
Return JSON: {{"items": ["...", ...]}} with exactly {n} items.

Input:
{texts}""",
}

GENERATE_PROMPT = """You are preparing training data for a Singapore bank's customer-service chatbot.
Write {n} different English messages that a bank customer might type, where the customer {desc}.
Rules:
- Each message must clearly belong to this category and not to any other banking request.
- Vary length (3 to 30 words), tone (polite, annoyed, rushed), and wording. Include some typos, missing punctuation
  and lowercase-only messages, like real chat messages.
- Use Singapore context where natural (SGD, PayNow, GIRO, NRIC, MRT), but not in every message.
- Do not number the messages. No duplicates.
- Style for this batch: {style}.
Return JSON: {{"items": ["...", ...]}} with {n} items."""

# 每一批换一种风格，这样可以同时生成好几批，也不太会重复
STYLES = ["short and direct (3-8 words)", "polite and formal", "annoyed or impatient", "casual, lowercase, typos, no punctuation",
          "longer, with some background details", "Singlish style (lah, leh, can or not)", "phrased as a question",
          "phrased as a statement, not a question", "elderly customer, simple words", "young customer, chat abbreviations (pls, u, thx)"]


def translate(client: LLMClient, texts: list[str], lang: str) -> list[str]:
    """批量翻译，返回的条数不对就拆成两半再试"""
    numbered = json.dumps(texts, ensure_ascii=False, indent=0)
    try:
        return client.items(TRANSLATE_PROMPTS[lang].format(n=len(texts), texts=numbered), len(texts),
                            exact=True, temperature=0.3)
    except RuntimeError:
        if len(texts) == 1:
            raise
        mid = len(texts) // 2
        return translate(client, texts[:mid], lang) + translate(client, texts[mid:], lang)


def generate(client: LLMClient, sub_intent: str, n: int, batch_no: int, tag: str) -> list[str]:
    prompt = GENERATE_PROMPT.format(n=n, desc=SUB_INTENT_DESC[sub_intent], style=STYLES[batch_no % len(STYLES)])
    prompt += f"\n(batch {tag}-{batch_no})"  # 训练集 / 测试集、不同批次的 prompt 不同，缓存才不会返回同一批句子
    return client.items(prompt, n, exact=False, temperature=1.0)


# 3. 抽样和生成
def balanced_sample(groups: dict[str, list[str]], target: int, rng: random.Random) -> list[tuple[str, str]]:
    """在各个二级意图之间轮流抽，抽够 target 条或者抽完为止"""
    pools = {k: rng.sample(v, len(v)) for k, v in groups.items() if v}
    picked: list[tuple[str, str]] = []
    while len(picked) < target and pools:
        for sub in list(pools):
            if len(picked) >= target:
                break
            picked.append((sub, pools[sub].pop()))
            if not pools[sub]:
                del pools[sub]
    return picked


def banking77_rows(split: str, target: int, rng: random.Random) -> dict[str, list[tuple[str, str]]]:
    """Banking77 按一级意图分好，每个意图是 [(二级意图, 英文句子)]"""
    df = pd.read_csv(CONFIG["raw_dir"] / f"banking77_{split}.csv")
    df = df[df["category"].isin(BANKING77_TO_INTENT)]
    by_intent: dict[str, dict[str, list[str]]] = defaultdict(lambda: defaultdict(list))
    for text, category in zip(df["text"], df["category"]):
        by_intent[BANKING77_TO_INTENT[category]][category].append(str(text).strip())
    return {intent: balanced_sample(groups, target, rng) for intent, groups in by_intent.items()}


def generated_rows(client: LLMClient, intent: str, need: int, split: str) -> list[tuple[str, str]]:
    """给一个意图生成 need 条英文句子，几个二级意图平均分"""
    subs = [s for s in sub_intents_of(intent) if s in SUB_INTENT_DESC]
    per_sub = math.ceil(need / len(subs))
    size = CONFIG["batch_size"]
    texts: dict[str, list[str]] = {sub: [] for sub in subs}
    next_batch = {sub: 0 for sub in subs}

    for _ in range(4):  # 每轮并行生成所有缺口，去重后仍不够就再来一轮
        jobs = []
        for sub in subs:
            missing = per_sub - len(set(texts[sub]))
            for _ in range(math.ceil(missing * 1.3 / size) if missing > 0 else 0):  # 多生成 30%，抵消重复
                jobs.append((sub, next_batch[sub]))
                next_batch[sub] += 1
        if not jobs:
            break
        with ThreadPoolExecutor(CONFIG["workers"]) as pool:
            results = pool.map(lambda job: (job[0], generate(client, job[0], size, job[1], split)), jobs)
            for sub, items in results:
                texts[sub] += items

    rows: list[tuple[str, str]] = []
    for sub in subs:
        unique = list(dict.fromkeys(t for t in texts[sub]))[:per_sub]
        if len(unique) < per_sub:
            print(f"  注意：{sub} 只生成了 {len(unique)}/{per_sub} 条不重复的句子")
        rows += [(sub, t) for t in unique]
    return rows[:need]


def assign_langs(rows: list[tuple[str, str]], rng: random.Random) -> list[dict]:
    """每个意图里按 en、zh、mixed 轮流分（先打乱顺序）"""
    rows = rng.sample(rows, len(rows))
    return [{"sub_intent": sub, "source_text": text, "lang": LANGS[i % 3]} for i, (sub, text) in enumerate(rows)]


def build_split(client: LLMClient, split: str, target: int, rng: random.Random, only: list[str] | None) -> list[dict]:
    b77 = banking77_rows(split, target, rng)
    records: list[dict] = []
    for intent in INTENTS:
        if intent == "greeting" or (only and intent not in only):
            continue  # greeting 用规则识别，不生成训练数据
        rows = [(sub, text) for sub, text in b77.get(intent, [])]
        source = {text: "banking77" for _, text in rows}
        if intent in GENERATE_ONLY or intent in NEEDS_TOP_UP:
            need = target - len(rows)
            if need > 0:
                gen = generated_rows(client, intent, need, split)
                source.update({text: "llm" for _, text in gen})
                rows += gen
        for r in assign_langs(rows, rng):
            r.update(intent=intent, label=full_label(intent, r["sub_intent"]), source=source[r["source_text"]])
            records.append(r)
        print(f"  [{split}] {intent:<17} {len(rows):>4} 条（其中 LLM 生成 {sum(s == 'llm' for s in source.values())}）")
    return records


def translate_records(client: LLMClient, records: list[dict]) -> None:
    """分到 zh 和 mixed 的句子拿去翻译，en 的不用动"""
    jobs = []
    for lang in ("zh", "mixed"):
        todo = [r for r in records if r["lang"] == lang]
        for i in range(0, len(todo), CONFIG["batch_size"]):
            jobs.append((lang, todo[i:i + CONFIG["batch_size"]]))

    def run(job):
        lang, batch = job
        for r, text in zip(batch, translate(client, [r["source_text"] for r in batch], lang)):
            r["text"] = text

    with ThreadPoolExecutor(CONFIG["workers"]) as pool:
        for done, _ in enumerate(pool.map(run, jobs), 1):
            if done % 10 == 0 or done == len(jobs):
                print(f"  翻译进度 {done}/{len(jobs)} 批")
    for r in records:
        r.setdefault("text", r["source_text"])


# 4. 清洗和输出
import re  # noqa: E402

# 中文句子里可以留着的英文（品牌、缩写），有其他英文单词的话就改成 mixed
ZH_ALLOWED_EN = {"visa", "mastercard", "unionpay", "paynow", "giro", "atm", "pin", "otp", "sgd", "nric", "mrt", "app",
                 "singtel", "starhub", "grab", "shopee", "lazada", "foodpanda", "sq", "dbs", "ocbc", "uob", "tt", "fast",
                 "usd", "myr", "cny", "gbp", "eur", "id", "sms", "qr", "pdf"}
_EN_WORD = re.compile(r"[A-Za-z]+")
# 生成的句子有时会提到真的银行，比如"我的 POSB 卡丢了"，这就和意图对不上了
# other_company 本来就是问别的公司，所以保留
_OTHER_BANK = re.compile(r"\b(POSB|DBS|OCBC|UOB|Citi\w*|HSBC|Maybank|Standard Chartered|Revolut)\b", re.IGNORECASE)


# "Grab 退款还没到"在 Banking77 里算 dispute_refund，生成的时候却放在了 other_company，
# 两边对不上，所以 other_company 里提到退款的句子都删掉
_REFUND = re.compile(r"refund|refnd|money back|退款|退钱", re.IGNORECASE)


def norm_key(text: str) -> str:
    """去重的时候不管大小写、空格和标点"""
    return re.sub(r"[\W_]+", "", text.lower())


def clean(records: list[dict], forbidden: set[str] = frozenset()) -> list[dict]:
    """去掉空的、重复的、和训练集重复的句子；mixed 的句子要同时有中文和英文"""
    out, seen = [], set(forbidden)
    dropped = Counter()
    for r in records:
        text = r["text"].strip()
        key = norm_key(text)
        has_zh = any("一" <= c <= "鿿" for c in text)
        has_en = any(c.isascii() and c.isalpha() for c in text)
        if not text or key in seen:
            dropped["重复"] += 1
            continue
        if r["label"] != "out_of_scope/other_company" and _OTHER_BANK.search(text):
            dropped["提到其他银行"] += 1
            continue
        if r["label"] == "out_of_scope/other_company" and _REFUND.search(text + " " + r["source_text"]):
            dropped["其他公司退款（与 dispute_refund 冲突）"] += 1
            continue
        if r["lang"] == "mixed" and not (has_zh and has_en):
            dropped["混杂句缺少中文或英文"] += 1
            continue
        if r["lang"] == "zh" and not has_zh:
            dropped["中文句没有中文"] += 1
            continue
        if r["lang"] == "zh" and any(w.lower() not in ZH_ALLOWED_EN for w in _EN_WORD.findall(text)):
            r["lang"] = "mixed"  # 翻译时保留了普通英文单词，按实际情况标为中英混杂
            dropped["中文句改标为 mixed（未删除）"] += 1
        seen.add(key)
        r["text"] = text
        out.append(r)
    if dropped:
        print(f"  清洗删除：{dict(dropped)}")
    return out


def finalize(records: list[dict], split: str) -> list[dict]:
    fields = ["id", "text", "lang", "intent", "sub_intent", "label", "source", "source_text", "review"]
    for i, r in enumerate(records, 1):
        r["id"] = f"{split}-{i:05d}"
        # 要重点检查的：LLM 生成的句子，还有容易混的几个 Banking77 标签
        r["review"] = r["source"] == "llm" or r["sub_intent"] in REVIEW_LABELS
    return [{k: r[k] for k in fields} for r in records]


def write_jsonl(path: Path, records: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        for r in records:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")


def write_label_studio(out_dir: Path, splits: dict[str, list[dict]]) -> None:
    """生成 Label Studio 的配置和任务，任务里预先填好标签，标注的人只要检查"""
    ls_dir = out_dir / "label_studio"
    ls_dir.mkdir(parents=True, exist_ok=True)

    choices = []
    for intent in INTENTS:
        if intent == "greeting":
            continue
        subs = "".join(f'\n      <Choice value="{s}"/>' for s in sub_intents_of(intent))
        choices.append(f'    <Choice value="{intent}">{subs}\n    </Choice>')
    xml = ('<View>\n'
           '  <Header value="$lang · $source · 原句：$source_text"/>\n'
           '  <Text name="text" value="$text"/>\n'
           '  <Taxonomy name="intent" toName="text" maxUsages="1" leafsOnly="true">\n'
           + "\n".join(choices) +
           '\n  </Taxonomy>\n'
           '  <Choices name="quality" toName="text" choice="single" showInline="true">\n'
           '    <Choice value="ok"/>\n    <Choice value="bad_translation"/>\n    <Choice value="delete"/>\n'
           '  </Choices>\n'
           '</View>\n')
    (ls_dir / "labeling_config.xml").write_text(xml, encoding="utf-8")

    for split, records in splits.items():
        tasks = [{"data": r, "predictions": [{"model_version": "dataset_v1", "result": [{
            "from_name": "intent", "to_name": "text", "type": "taxonomy",
            "value": {"taxonomy": [[r["intent"], r["sub_intent"]]]}}]}]} for r in records]
        (ls_dir / f"tasks_{split}.json").write_text(json.dumps(tasks, ensure_ascii=False, indent=1), encoding="utf-8")


def report(name: str, records: list[dict]) -> None:
    print(f"\n{name}: {len(records)} 条")
    print("  语言：", dict(Counter(r["lang"] for r in records)))
    print("  来源：", dict(Counter(r["source"] for r in records)))
    print("  需重点检查：", sum(r["review"] for r in records))
    for intent, n in sorted(Counter(r["intent"] for r in records).items()):
        print(f"    {intent:<17} {n}")


def main() -> None:
    parser = argparse.ArgumentParser(description="构建意图识别数据集")
    parser.add_argument("--trial", action="store_true", help="试跑：每个意图 15 条训练 + 6 条测试，只做 4 个意图")
    args = parser.parse_args()
    if not config.deepseek_api_key:
        sys.exit("缺少 DEEPSEEK_API_KEY，请在 .env 中设置")

    train_n, test_n = (15, 6) if args.trial else (CONFIG["train_per_intent"], CONFIG["test_per_intent"])
    only = ["card_service", "card_lost_freeze", "complaint", "out_of_scope"] if args.trial else None
    out_dir = CONFIG["out_dir"] / "trial" if args.trial else CONFIG["out_dir"]
    client = LLMClient(CONFIG["cache_file"])
    rng = random.Random(CONFIG["seed"])

    print(f"模型：{config.deepseek_model}，缓存：{len(client.cache)} 条")
    splits = {}
    for split, n in (("train", train_n), ("test", test_n)):
        print(f"\n== {split} ==")
        records = build_split(client, split, n, rng, only)
        translate_records(client, records)
        forbidden = {norm_key(r["text"]) for r in splits.get("train", [])}
        splits[split] = finalize(clean(records, forbidden), split)
        write_jsonl(out_dir / f"{split}.jsonl", splits[split])

    write_label_studio(out_dir, splits)
    for split, records in splits.items():
        report(split, records)
    print(f"\n本次实际调用 LLM {client.calls} 次；输出目录：{out_dir}")


if __name__ == "__main__":
    main()
