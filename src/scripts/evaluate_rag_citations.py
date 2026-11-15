"""Run real answers on the labelled FAQ set and evaluate displayed source selection."""
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


def names(section):
    return {part.strip() for part in (section or "").split(" / ") if part.strip()}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, default=0, help="0 evaluates the full labelled FAQ set")
    args = parser.parse_args()

    from src.nodes.rag_answer import RagAnswerNode
    from src.schema.config import get_config
    from src.utils.startup_warmup import warmup_runtime

    rows = [json.loads(line) for line in DATA.read_text(encoding="utf-8").splitlines() if line.strip()]
    if args.limit:
        rows = rows[:args.limit]
    warmup = warmup_runtime()
    node = RagAnswerNode()
    results = []
    for index, sample in enumerate(rows, 1):
        question = sample["question"]
        history = [tuple(pair) for pair in sample.get("history", [])]
        lang = "en" if sample.get("lang") == "en" else "zh"
        started = time.perf_counter()
        try:
            result = node.answer(question, history, lang,
                                 sub_intent=sample.get("sub_intent"))
            info = node.last_run
            error = None
        except Exception as exc:
            result, info = {"answer": "", "sources": []}, {}
            error = f"{type(exc).__name__}: {str(exc)[:240]}"
        elapsed_ms = (time.perf_counter() - started) * 1000

        expected = sample.get("expected") or []
        expected_set = set(expected)
        candidates = info.get("kept", [])
        displayed = result.get("sources", [])
        candidate_names = set().union(*(names(doc.get("section")) for doc in candidates)) if candidates else set()
        displayed_names = set().union(*(names(doc.get("section")) for doc in displayed)) if displayed else set()
        expected_found = expected_set & displayed_names
        source_hit = bool(expected_found) if expected else None
        relevant_cited = sum(bool(expected_set & names(doc.get("section"))) for doc in displayed) if expected else None
        citation_valid = info.get("citations_valid")
        if info.get("generator") == "grounded_template":
            citation_valid = True

        results.append({
            "id": sample.get("id"), "question": question, "expected": expected,
            "candidate_sources": sorted(candidate_names), "displayed_sources": sorted(displayed_names),
            "candidate_count": len(candidates), "displayed_count": len(displayed),
            "source_hit": source_hit,
            "citation_precision": (relevant_cited / len(displayed) if expected and displayed else
                                    (1.0 if expected and not displayed else None)),
            "citations_valid": citation_valid, "generator": info.get("generator"),
            "generation_calls": info.get("generation_calls", 0),
            "used_rewrite": info.get("used_rewrite", False), "used_hyde": info.get("used_hyde", False),
            "latency_ms": round(elapsed_ms, 2), "error": error,
        })
        if index % 5 == 0 or index == len(rows):
            print(f"[RAG citation evaluation] {index}/{len(rows)}", flush=True)

    valid = [row for row in results if not row["error"]]
    answerable = [row for row in valid if row["expected"]]
    cited = [row for row in valid if row["displayed_count"]]
    all_candidates = sum(row["candidate_count"] for row in valid)
    all_displayed = sum(row["displayed_count"] for row in valid)
    valid_citations = [row["citations_valid"] for row in valid if row["citations_valid"] is not None]
    latencies = [row["latency_ms"] for row in valid]
    summary = {
        "created_at": datetime.now().isoformat(timespec="seconds"),
        "collection": get_config().kb_collection,
        "generator": get_config().rag_generator,
        "warmup": warmup,
        "count": len(rows), "success_count": len(valid), "error_count": len(rows) - len(valid),
        "generation_calls_total": sum(row["generation_calls"] for row in valid),
        "answerable_count": len(answerable),
        "expected_source_recall": (sum(row["source_hit"] for row in answerable) / len(answerable)
                                   if answerable else None),
        "mean_citation_precision": (statistics.mean(row["citation_precision"] for row in answerable
                                                      if row["citation_precision"] is not None)
                                    if any(row["citation_precision"] is not None for row in answerable) else None),
        "citation_format_valid_rate": statistics.mean(valid_citations) if valid_citations else None,
        "candidate_sources_total": all_candidates, "displayed_sources_total": all_displayed,
        "source_reduction_rate": (1 - all_displayed / all_candidates) if all_candidates else None,
        "mean_latency_ms": round(statistics.mean(latencies), 2) if latencies else None,
        "p50_latency_ms": round(statistics.median(latencies), 2) if latencies else None,
        "p95_latency_ms": round(percentile(latencies, .95), 2) if latencies else None,
        "details": results,
    }

    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    OUT.mkdir(parents=True, exist_ok=True)
    json_path = OUT / f"rag_citations_{stamp}.json"
    md_path = OUT / f"rag_citations_{stamp}.md"
    json_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")

    def pct(value):
        return "N/A" if value is None else f"{value:.1%}"

    lines = ["# RAG 回答来源实测", "", f"- 测试集：{len(rows)} 题；成功：{len(valid)}；错误：{len(rows) - len(valid)}",
             f"- 知识库：`{summary['collection']}`；生成器：`{summary['generator']}`", "",
             f"- 启动预热：{warmup['elapsed_seconds']:.2f}s（预热组件：{', '.join(warmup['warmed']) or '无'}；失败：{', '.join(warmup['failed']) or '无'}）",
             "| 指标 | 结果 |", "|---|---:|",
             f"| 答案生成模型调用次数 | {summary['generation_calls_total']} |",
             f"| 预期来源召回率 | {pct(summary['expected_source_recall'])} |",
             f"| 引用准确率（按预期章节） | {pct(summary['mean_citation_precision'])} |",
             f"| JSON 引用格式有效率 | {pct(summary['citation_format_valid_rate'])} |",
             f"| 候选来源总数 → 展示来源总数 | {all_candidates} → {all_displayed} |",
             f"| 来源展示减少 | {pct(summary['source_reduction_rate'])} |",
             f"| 平均 / P50 / P95 总耗时 | {summary['mean_latency_ms']} / {summary['p50_latency_ms']} / {summary['p95_latency_ms']} ms |",
             "", "## 未命中预期资料", ""]
    misses = [row for row in answerable if not row["source_hit"]]
    lines.extend([f"- {row['question']}（期望：{'、'.join(row['expected'])}；展示：{'、'.join(row['displayed_sources']) or '无'}；生成器：{row['generator']}）"
                  for row in misses] or ["- 无"])
    lines.extend(["", "## 无效引用格式回退", ""])
    invalid = [row for row in valid if row["citations_valid"] is False]
    lines.extend([f"- {row['question']}（展示了 {row['displayed_count']} 条候选来源）" for row in invalid] or ["- 无"])
    md_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"JSON: {json_path.relative_to(ROOT)}")
    print(f"Markdown: {md_path.relative_to(ROOT)}")
    print("\n".join(lines))


if __name__ == "__main__":
    main()
