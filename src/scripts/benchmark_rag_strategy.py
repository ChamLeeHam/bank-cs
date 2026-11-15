"""Paired benchmark for legacy and adaptive retrieval on the same FAQ question set."""
import argparse
import json
import math
import statistics
import sys
import time
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
DATA = ROOT / "data/kb/eval_questions.jsonl"
OUT = ROOT / "data/evaluation/results"


def percentile(values, p):
    ordered = sorted(values)
    return ordered[max(0, math.ceil(p * len(ordered)) - 1)] if ordered else None


def source_names(docs):
    names = set()
    for doc in docs:
        names.update(part.strip() for part in doc.get("section", "").split(" / ") if part.strip())
    return names


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, default=0, help="0 means all reviewed FAQ cases")
    args = parser.parse_args()
    from src.nodes.rag_answer import RagAnswerNode
    from src.utils.startup_warmup import warmup_runtime

    rows = [json.loads(line) for line in DATA.read_text(encoding="utf-8").splitlines() if line.strip()]
    if args.limit:
        rows = rows[:args.limit]

    warmup = warmup_runtime()
    node = RagAnswerNode()
    results = {"legacy": [], "adaptive": []}
    for index, sample in enumerate(rows, 1):
        history = [tuple(item) for item in sample.get("history", [])]
        # Alternate order to reduce warm cache/order bias while keeping every case paired.
        strategies = ("adaptive", "legacy") if index % 2 else ("legacy", "adaptive")
        by_strategy = {}
        for strategy in strategies:
            started = time.perf_counter()
            try:
                _, docs, info = node.retrieve(sample["question"], history, strategy=strategy)
                error = None
            except Exception as exc:
                docs, info = [], {}
                error = f"{type(exc).__name__}: {str(exc)[:200]}"
            elapsed_ms = (time.perf_counter() - started) * 1000
            expected = sample.get("expected") or []
            actual = source_names(docs)
            hit = bool(expected and set(expected) & actual) if expected else None
            detail = {
                "question": sample["question"], "expected": expected, "retrieved": sorted(actual),
                "source_hit": hit, "latency_ms": round(elapsed_ms, 2),
                "best_score": info.get("best_score"), "used_rewrite": info.get("used_rewrite", False),
                "used_hyde": info.get("used_hyde", False), "error": error,
            }
            results[strategy].append(detail)
            by_strategy[strategy] = detail
        if index % 5 == 0 or index == len(rows):
            print(f"[paired RAG benchmark] {index}/{len(rows)}", flush=True)

    summary = {"count": len(rows), "warmup": warmup, "strategies": {}}
    for strategy, details in results.items():
        valid = [item for item in details if not item["error"]]
        latencies = [item["latency_ms"] for item in valid]
        answerable = [item for item in valid if item["expected"]]
        summary["strategies"][strategy] = {
            "successful": len(valid), "errors": len(details) - len(valid),
            "expected_source_recall": (sum(item["source_hit"] for item in answerable) / len(answerable)
                                        if answerable else None),
            "mean_latency_ms": round(statistics.mean(latencies), 2) if latencies else None,
            "p50_latency_ms": round(statistics.median(latencies), 2) if latencies else None,
            "p95_latency_ms": round(percentile(latencies, .95), 2) if latencies else None,
            "rewrite_calls": sum(item["used_rewrite"] for item in valid),
            "hyde_calls": sum(item["used_hyde"] for item in valid),
            "details": details,
        }

    paired = []
    for i in range(len(rows)):
        old, new = results["legacy"][i], results["adaptive"][i]
        if old["source_hit"] and not new["source_hit"]:
            paired.append({"question": rows[i]["question"], "kind": "adaptive_missed",
                           "expected": old["expected"], "legacy_retrieved": old["retrieved"],
                           "adaptive_retrieved": new["retrieved"]})
        elif new["source_hit"] and not old["source_hit"]:
            paired.append({"question": rows[i]["question"], "kind": "adaptive_recovered",
                           "expected": old["expected"], "legacy_retrieved": old["retrieved"],
                           "adaptive_retrieved": new["retrieved"]})
    summary["source_differences"] = paired

    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    OUT.mkdir(parents=True, exist_ok=True)
    json_path = OUT / f"rag_strategy_{stamp}.json"
    md_path = OUT / f"rag_strategy_{stamp}.md"
    json_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    legacy, adaptive = summary["strategies"]["legacy"], summary["strategies"]["adaptive"]
    lines = ["# RAG 检索策略同批对比", "", f"- 用例数：{len(rows)}", 
             f"- 启动预热：{warmup['elapsed_seconds']:.2f}s（预热组件：{', '.join(warmup['warmed']) or '无'}）", "",
             "| 策略 | 平均延迟 | P50 | P95 | 关键资料召回 | 重写调用 | HyDE调用 |", "|---|---:|---:|---:|---:|---:|---:|"]
    for name, data in (("旧流程", legacy), ("自适应", adaptive)):
        recall = "N/A" if data["expected_source_recall"] is None else f"{data['expected_source_recall']:.1%}"
        lines.append(f"| {name} | {data['mean_latency_ms']} ms | {data['p50_latency_ms']} ms | {data['p95_latency_ms']} ms | {recall} | {data['rewrite_calls']} | {data['hyde_calls']} |")
    lines.extend(["", "## 关键资料召回变化", ""])
    lines.extend([f"- {item['kind']}: {item['question']}（期望：{'、'.join(item['expected'])}；自适应召回：{'、'.join(item['adaptive_retrieved']) or '无'}）"
                  for item in paired] or ["- 两种策略的关键资料召回结果一致。"])
    md_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"JSON: {json_path.relative_to(ROOT)}")
    print(f"Markdown: {md_path.relative_to(ROOT)}")
    print("\n".join(lines))


if __name__ == "__main__":
    main()
