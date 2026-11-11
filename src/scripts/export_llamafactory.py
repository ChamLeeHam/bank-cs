"""
把 data/intent/reviewed/ 里的数据转成 LLaMA Factory 用的 alpaca 格式，输出到 data/intent/llamafactory/
- intent_train.json、intent_test.json、dataset_info.json
- intent_test_zeroshot.json：同一个测试集，提示词里列出了所有标签，给没微调的模型做对比

运行：uv run python src/scripts/export_llamafactory.py
"""
import json
import sys
from collections import Counter
from pathlib import Path

PROJECT_ROOT = Path(__file__).parent.parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from src.intents import INTENT_SYSTEM, zero_shot_system
from src.schema.config import get_config

config = get_config()
IN_DIR = config.data_dir / "intent" / "reviewed"
OUT_DIR = config.data_dir / "intent" / "llamafactory"
COLUMNS = {"system": "system", "prompt": "instruction", "query": "input", "response": "output"}


def to_alpaca(record: dict, system: str = INTENT_SYSTEM) -> dict:
    return {"system": system, "instruction": record["text"], "input": "", "output": record["label"]}


def write(name: str, rows: list[dict], dataset_info: dict) -> None:
    (OUT_DIR / f"{name}.json").write_text(json.dumps(rows, ensure_ascii=False, indent=1), encoding="utf-8")
    dataset_info[name] = {"file_name": f"{name}.json", "columns": COLUMNS}


def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    dataset_info = {}
    for split in ("train", "test"):
        records = [json.loads(line) for line in (IN_DIR / f"{split}.jsonl").open(encoding="utf-8")]
        write(f"intent_{split}", [to_alpaca(r) for r in records], dataset_info)
        langs = Counter(r["lang"] for r in records)
        print(f"intent_{split}: {len(records)} 条，{len({r['label'] for r in records})} 个标签，语言 {dict(langs)}")
    zero_shot = zero_shot_system()
    write("intent_test_zeroshot", [to_alpaca(r, zero_shot) for r in records], dataset_info)
    print(f"intent_test_zeroshot: {len(records)} 条（系统提示词 {len(zero_shot)} 字，列出全部标签）")
    (OUT_DIR / "dataset_info.json").write_text(json.dumps(dataset_info, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"输出目录：{OUT_DIR}")
    print("示例：", json.dumps(to_alpaca(records[0]), ensure_ascii=False))


if __name__ == "__main__":
    main()
