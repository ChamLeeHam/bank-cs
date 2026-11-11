"""
把人工检查的结果应用到数据集，输出 data/intent/reviewed/{train,test}.jsonl（Day 3 微调使用这里的文件）

检查结果有两种来源，都放在 data/intent/review/ 下：
  1. *.jsonl：每行一条决定 {"text", "action": ok|relabel|delete, "label", "note", "reviewer"}
  2. *.json ：Label Studio 导出的 JSON（Export -> JSON），自动转换成上面的格式
用句子原文（text）匹配，所以重新运行 build_intent_dataset.py 后（id 会变）检查结果仍然有效。

运行（在项目根目录）：
    uv run python src/scripts/apply_review.py
"""
import json
import sys
from collections import Counter
from pathlib import Path

PROJECT_ROOT = Path(__file__).parent.parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from src.intents import ALL_LABELS, split_label
from src.schema.config import get_config

config = get_config()
INTENT_DIR = config.data_dir / "intent"
REVIEW_DIR = INTENT_DIR / "review"
OUT_DIR = INTENT_DIR / "reviewed"


def from_label_studio(path: Path) -> list[dict]:
    """Label Studio 导出的 JSON -> 检查决定（每个任务取最后一次标注）"""
    decisions = []
    for task in json.loads(path.read_text(encoding="utf-8")):
        annotations = [a for a in task.get("annotations", []) if not a.get("was_cancelled")]
        if not annotations:
            continue  # 没有检查过的任务不处理
        label, quality = None, "ok"
        for item in annotations[-1]["result"]:
            if item["from_name"] == "intent" and item["value"].get("taxonomy"):
                label = "/".join(item["value"]["taxonomy"][0][:2])
            elif item["from_name"] == "quality" and item["value"].get("choices"):
                quality = item["value"]["choices"][0]
        data = task["data"]
        if quality in ("delete", "bad_translation"):
            action = "delete"
        elif label and label != data["label"]:
            action = "relabel"
        else:
            action = "ok"
        decisions.append({"text": data["text"], "action": action, "label": label or data["label"],
                          "note": quality, "reviewer": "label_studio"})
    return decisions


def load_decisions() -> dict[str, dict]:
    decisions: dict[str, dict] = {}
    for path in sorted(REVIEW_DIR.glob("*")):
        if path.suffix == ".jsonl":
            rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
        elif path.suffix == ".json":
            rows = from_label_studio(path)
        else:
            continue
        for row in rows:
            if row["action"] == "relabel" and row["label"] not in ALL_LABELS:
                raise ValueError(f"{path.name}: 标签不存在 {row['label']}（句子：{row['text']}）")
            decisions[row["text"]] = row  # 同一句子有多条决定时，后读到的文件为准
        print(f"读取 {path.name}: {len(rows)} 条")
    return decisions


def main() -> None:
    decisions = load_decisions()
    used = set()
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    for split in ("train", "test"):
        records = [json.loads(line) for line in (INTENT_DIR / f"{split}.jsonl").open(encoding="utf-8")]
        out, stats = [], Counter()
        for r in records:
            d = decisions.get(r["text"])
            if d is None:
                r["reviewed"] = False
                out.append(r)
                continue
            used.add(r["text"])
            stats[d["action"]] += 1
            if d["action"] == "delete":
                continue
            if d["action"] == "relabel":
                r["label"] = d["label"]
                r["intent"], r["sub_intent"] = split_label(d["label"])
            r["reviewed"] = True
            out.append(r)
        with (OUT_DIR / f"{split}.jsonl").open("w", encoding="utf-8") as f:
            for r in out:
                f.write(json.dumps(r, ensure_ascii=False) + "\n")
        print(f"{split}: {len(records)} -> {len(out)} 条；检查结果 {dict(stats)}")

    unused = [t for t in decisions if t not in used]
    if unused:
        print(f"注意：{len(unused)} 条检查结果在数据集中找不到对应句子（数据可能已重新生成），例如：{unused[:3]}")
    print(f"输出目录：{OUT_DIR}")


if __name__ == "__main__":
    main()
