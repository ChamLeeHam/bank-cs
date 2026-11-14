"""用相同样本对比智增增 Jev 与已有 Qwen+规则报告；默认只跑小样本。"""
import argparse
import json
import statistics
import time
from collections import Counter, defaultdict, deque
from datetime import datetime
from pathlib import Path

from src.nodes.jev_intent import JevError, classify
from src.scripts.evaluate_quality import INTENT_DATA, RESULT_DIR, accuracy, load_jsonl, macro_f1, percentile, sha256


def sample_rows(rows, limit):
    if limit == 0 or limit >= len(rows):
        return list(rows)
    groups = defaultdict(deque)
    for row in rows:
        groups[(row["intent"], row["lang"])].append(row)
    selected = []
    while len(selected) < limit and any(groups.values()):
        for key in sorted(groups):
            if groups[key] and len(selected) < limit:
                selected.append(groups[key].popleft())
    return selected


def main():
    parser = argparse.ArgumentParser(description="Jev 与 Qwen+规则意图分类对照（只使用演示测试集）")
    parser.add_argument("--baseline", type=Path, default=RESULT_DIR / "evaluation_20261114_234123.json",
                        help="已有 Qwen+规则评估报告 JSON")
    parser.add_argument("--limit", type=int, default=12, help="样本数，默认 12；0 表示完整测试集")
    parser.add_argument("--resume", type=Path, help="从先前未完成的对照 JSON 续跑")
    parser.add_argument("--dry-run", action="store_true", help="只检查样本和基线，不调用 API")
    args = parser.parse_args()
    if args.limit < 0:
        parser.error("--limit 不能为负数")
    rows = sample_rows(load_jsonl(INTENT_DATA), args.limit)
    baseline = json.loads(args.baseline.read_text(encoding="utf-8"))
    dataset_hash = sha256(INTENT_DATA)
    if baseline.get("dataset_sha256", {}).get("intent") != dataset_hash:
        raise SystemExit("基线报告与当前意图数据集的 SHA-256 不同，不能直接比较。")
    old = {row["id"]: row for row in baseline["intent"]["predictions"]}
    if any(row["id"] not in old or old[row["id"]]["text"] != row["text"] for row in rows):
        raise SystemExit("基线报告缺少对应样本或文本不一致，不能直接比较。")
    print(f"样本 {len(rows)} 条；基线为 Qwen+规则；测试集 SHA-256：{dataset_hash}", flush=True)
    if args.dry_run:
        print("样本与基线匹配；未发送 API 请求。")
        return

    predictions = []
    if args.resume:
        previous = json.loads(args.resume.read_text(encoding="utf-8"))
        predictions = previous.get("predictions", [])
        if previous.get("dataset_sha256") != dataset_hash or len(predictions) > len(rows):
            raise SystemExit("续跑文件的数据集或样本数量不匹配。")
        for row, result in zip(rows, predictions):
            if (row["id"] != result.get("id") or row["text"] != result.get("text") or
                    old[row["id"]]["predicted_intent"] != result.get("baseline_intent")):
                raise SystemExit("续跑文件与本次样本或基线不匹配。")
        print(f"续跑已完成的 {len(predictions)} 条", flush=True)
    for index in range(len(predictions), len(rows)):
        row = rows[index]
        start = time.perf_counter()
        for attempt in range(3):
            try:
                value = classify(row["text"])
                elapsed = round((time.perf_counter() - start) * 1000, 2)
                predictions.append({"id": row["id"], "lang": row["lang"], "text": row["text"],
                                    "expected_intent": row["intent"], "predicted_intent": value["intent"],
                                    "baseline_intent": old[row["id"]]["predicted_intent"],
                                    "latency_ms": elapsed, "model": value["model"],
                                    "confidence": value["confidence"], "probabilities": value["probabilities"],
                                    "usage": value["usage"]})
                break
            except JevError as exc:
                transient = any(s in str(exc) for s in ("TimeoutError", "URLError", "HTTP 429", "HTTP 500", "HTTP 502", "HTTP 503", "HTTP 504"))
                if not transient or attempt == 2:
                    print(f"第 {index + 1} 条请求失败：{exc}；中止并保留已完成结果。", flush=True)
                    break
                print(f"第 {index + 1} 条暂时失败，正在重试（{attempt + 1}/2）。", flush=True)
                time.sleep(2 ** attempt)
        else:
            raise AssertionError("unreachable")
        if len(predictions) != index + 1:
            break
        if (index + 1) % 25 == 0 or index + 1 == len(rows):
            print(f"[Jev] {index + 1}/{len(rows)}", flush=True)

    if not predictions:
        raise SystemExit("没有得到有效 Jev 结果，未生成比较报告。")
    count = len(predictions)
    elapsed = [r["latency_ms"] for r in predictions]
    metrics = {
        "requested": len(rows), "completed": count, "complete": count == len(rows),
        "jev_accuracy": accuracy(predictions, lambda r: r["predicted_intent"] == r["expected_intent"]),
        "baseline_accuracy_same_samples": accuracy(predictions, lambda r: r["baseline_intent"] == r["expected_intent"]),
        "jev_macro_f1": macro_f1(predictions, "expected_intent", "predicted_intent"),
        "baseline_macro_f1_same_samples": macro_f1(predictions, "expected_intent", "baseline_intent"),
        "jev_latency_ms": {"mean": round(statistics.mean(elapsed), 2), "p95": round(percentile(elapsed, .95), 2)},
        "models": dict(Counter(r["model"] for r in predictions)),
        "baseline_mean_ms_same_samples": round(statistics.mean(old[r["id"]]["latency_ms"] for r in predictions), 2),
    }
    result = {"dataset_sha256": dataset_hash, "baseline": str(args.baseline), "metrics": metrics,
              "predictions": predictions}
    RESULT_DIR.mkdir(parents=True, exist_ok=True)
    stem = "jev_comparison_" + datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    out_json, out_md = RESULT_DIR / f"{stem}.json", RESULT_DIR / f"{stem}.md"
    out_json.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    lines = ["# Jev 与 Qwen+规则：意图分类对照", "", f"样本：{count}/{len(rows)}；完整运行：{metrics['complete']}",
             f"数据集 SHA-256：`{dataset_hash}`", f"Jev 版本：{metrics['models']}", "",
             "| 指标 | Jev（纯分类） | Qwen+规则（已有报告） |", "|---|---:|---:|",
             f"| 一级准确率 | {metrics['jev_accuracy']:.2%} | {metrics['baseline_accuracy_same_samples']:.2%} |",
             f"| 一级 Macro-F1 | {metrics['jev_macro_f1']:.4f} | {metrics['baseline_macro_f1_same_samples']:.4f} |",
             f"| 单条平均耗时 | {metrics['jev_latency_ms']['mean']:.2f} ms | {metrics['baseline_mean_ms_same_samples']:.2f} ms |",
             "", "Jev 使用在线 API、耗时含网络；基线使用本地模型和规则，耗时不含加载。两者不属于同一部署环境。",
             "历史测试集曾用于分析和调整规则；这组数字用于回归对照，不能视为完全独立的最终泛化评估。",
             "只比较一级标签；失败后会中止，未完成的运行不能作为完整测试集结论。"]
    out_md.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"结果已保存：{out_md}\n{out_json}")
    if not metrics["complete"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
