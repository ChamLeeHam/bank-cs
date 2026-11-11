"""
给意图识别打分：读 LLaMA Factory 输出的 generated_predictions.jsonl，和测试集一条条对比

算这些指标：
- 一级、二级意图的准确率和宏平均 F1
- 英文、中文、中英混杂分开算
- 无效输出的比例（模型输出了标签表里没有的标签）
- 最常见的分错的情况（一级意图分错，就会走错分支）

运行：uv run python src/scripts/score_intent.py checkpoint/intent_lora/predict
"""
import json
import sys
from collections import Counter, defaultdict
from pathlib import Path

PROJECT_ROOT = Path(__file__).parent.parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from src.intents import ALL_LABELS, split_label

TEST_FILE = PROJECT_ROOT / "data" / "intent" / "reviewed" / "test.jsonl"


def macro_f1(gold: list[str], pred: list[str]) -> float:
    """宏平均 F1：每个类别的 F1 再取平均，小类和大类一样重要"""
    scores = []
    for c in set(gold):
        tp = sum(g == c and p == c for g, p in zip(gold, pred))
        fp = sum(g != c and p == c for g, p in zip(gold, pred))
        fn = sum(g == c and p != c for g, p in zip(gold, pred))
        scores.append(2 * tp / (2 * tp + fp + fn) if tp else 0.0)
    return sum(scores) / len(scores)


def clean_output(text: str) -> str:
    """只要第一行，模型有时候会多写几句解释"""
    return text.strip().splitlines()[0].strip() if text.strip() else ""


def score(pred_dir: Path) -> dict:
    test = [json.loads(line) for line in TEST_FILE.open(encoding="utf-8")]
    preds = [json.loads(line) for line in (pred_dir / "generated_predictions.jsonl").open(encoding="utf-8")]
    assert len(test) == len(preds), f"条数不一致：测试集 {len(test)}，预测 {len(preds)}"

    rows = []
    for t, p in zip(test, preds):
        # 按句子对齐，不按标签。这样测试集改了标签以后，旧的预测结果也能重新打分
        assert t["text"] in p["prompt"], f"预测文件与测试集顺序不一致：{t['text']}"
        out = clean_output(p["predict"])
        rows.append({"lang": t["lang"], "gold": t["label"], "pred": out, "valid": out in ALL_LABELS,
                     "gold1": t["intent"], "pred1": split_label(out)[0]})

    def metrics(subset: list[dict]) -> dict:
        n = len(subset)
        return {"n": n,
                "acc_l2": sum(r["gold"] == r["pred"] for r in subset) / n,
                "acc_l1": sum(r["gold1"] == r["pred1"] for r in subset) / n,
                "f1_l2": macro_f1([r["gold"] for r in subset], [r["pred"] for r in subset]),
                "f1_l1": macro_f1([r["gold1"] for r in subset], [r["pred1"] for r in subset]),
                "invalid": sum(not r["valid"] for r in subset) / n}

    by_lang = defaultdict(list)
    for r in rows:
        by_lang[r["lang"]].append(r)
    confusions = Counter((r["gold1"], r["pred1"]) for r in rows if r["gold1"] != r["pred1"])
    return {"name": pred_dir.parent.name, "all": metrics(rows),
            "by_lang": {lang: metrics(by_lang[lang]) for lang in ("en", "zh", "mixed")},
            "confusions": confusions.most_common(8),
            "invalid_examples": [r["pred"] for r in rows if not r["valid"]][:5]}


def main() -> None:
    if len(sys.argv) < 2:
        sys.exit(__doc__)
    results = [score(Path(p)) for p in sys.argv[1:]]

    print(f"\n{'模型':<14}{'语言':<7}{'条数':>5}{'二级准确率':>11}{'一级准确率':>11}{'二级F1':>9}{'一级F1':>9}{'无效输出':>9}")
    for res in results:
        for lang, m in [("全部", res["all"]), *res["by_lang"].items()]:
            print(f"{res['name']:<14}{lang:<7}{m['n']:>5}{m['acc_l2']:>11.1%}{m['acc_l1']:>11.1%}"
                  f"{m['f1_l2']:>9.3f}{m['f1_l1']:>9.3f}{m['invalid']:>9.1%}")
    for res in results:
        print(f"\n[{res['name']}] 会进错分支的混淆（真实一级意图 -> 预测）：")
        for (gold, pred), n in res["confusions"]:
            print(f"  {gold:<17} -> {pred or '(无效输出)':<17} {n}")
        if res["invalid_examples"]:
            print(f"  无效输出示例：{res['invalid_examples']}")
        out = Path(sys.argv[1 + results.index(res)]) / "intent_scores.json"
        out.write_text(json.dumps(res, ensure_ascii=False, indent=2), encoding="utf-8")


if __name__ == "__main__":
    main()
