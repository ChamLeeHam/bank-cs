"""把现有路由器前置规则应用于已保存的 Jev 预测，不调用 API。"""
import argparse
import json
from collections import Counter
from datetime import datetime
from pathlib import Path

from src.nodes.intent_router import IntentRouterNode
from src.scripts.evaluate_quality import INTENT_DATA, RESULT_DIR, accuracy, load_jsonl, macro_f1, sha256


class CachedJevRouter(IntentRouterNode):
    """复用实际项目路由器，只替换分类器为同一条问题的缓存结果。"""

    def __init__(self, text, label):
        super().__init__()
        self.text, self.label = text, label

    def _model_ready(self):
        return True

    def _classify(self, text):
        if text != self.text:
            raise ValueError("缓存 Jev 预测只适用于单轮原始问题")
        return {"intent": self.label, "sub_intent": "", "intent_score": 1.0, "router_backend": "jev_cached"}


def apply_rules(row):
    router = CachedJevRouter(row["text"], row["predicted_intent"])
    result = router.process({"masked_question": row["text"], "messages": [], "clarify_count": 0})
    return {**row, "jev_rules_intent": result["intent"], "jev_rules_backend": result["router_backend"]}


def main():
    parser = argparse.ArgumentParser(description="同规则重算 Jev 意图结果，不发送 API 请求")
    parser.add_argument("source", type=Path, help="完整 Jev 对照报告 JSON")
    args = parser.parse_args()
    source = json.loads(args.source.read_text(encoding="utf-8"))
    dataset = load_jsonl(INTENT_DATA)
    if source.get("dataset_sha256") != sha256(INTENT_DATA) or not source.get("metrics", {}).get("complete"):
        raise SystemExit("数据集不一致或 Jev 报告未完成。")
    rows = source.get("predictions", [])
    if len(rows) != len(dataset) or any(a["id"] != b["id"] or a["text"] != b["text"] or
                                        a["expected_intent"] != b["intent"] for a, b in zip(rows, dataset)):
        raise SystemExit("预测结果和当前完整测试集不一致。")
    rows = [apply_rules(row) for row in rows]
    metrics = {"count": len(rows),
               "jev_accuracy": accuracy(rows, lambda r: r["predicted_intent"] == r["expected_intent"]),
               "jev_rules_accuracy": accuracy(rows, lambda r: r["jev_rules_intent"] == r["expected_intent"]),
               "qwen_rules_accuracy": accuracy(rows, lambda r: r["baseline_intent"] == r["expected_intent"]),
               "jev_rules_macro_f1": macro_f1(rows, "expected_intent", "jev_rules_intent"),
               "rule_backend_counts": dict(Counter(r["jev_rules_backend"] for r in rows)),
               "rule_changes": sum(r["predicted_intent"] != r["jev_rules_intent"] for r in rows)}
    result = {"dataset_sha256": source["dataset_sha256"], "source": str(args.source), "metrics": metrics,
              "predictions": rows}
    RESULT_DIR.mkdir(parents=True, exist_ok=True)
    path = RESULT_DIR / ("jev_shared_rules_" + datetime.now().strftime("%Y%m%d_%H%M%S_%f") + ".json")
    path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(metrics, ensure_ascii=False, indent=2))
    print(f"结果已保存：{path}")


if __name__ == "__main__":
    main()
