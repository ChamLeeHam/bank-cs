"""Run the versioned intent, FAQ-source, and live API latency evaluation.

Run from the repository root with::

    uv run python src/scripts/evaluate_quality.py

The intent and FAQ retrieval measurements use the real local model/services.
The API subset uses the live demo server and only sends public FAQ questions.
"""
import argparse
import hashlib
import json
import math
import statistics
import sys
import time
import urllib.error
import urllib.request
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT_ROOT))

INTENT_DATA = PROJECT_ROOT / "data/intent/reviewed/test.jsonl"
FAQ_DATA = PROJECT_ROOT / "data/kb/eval_questions.jsonl"
API_DATA = PROJECT_ROOT / "data/evaluation/api_faq_questions.jsonl"
KB_DIR = PROJECT_ROOT / "data/kb"
RESULT_DIR = PROJECT_ROOT / "data/evaluation/results"


def load_jsonl(path):
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def sha256(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def accuracy(rows, predicate):
    return sum(bool(predicate(row)) for row in rows) / len(rows) if rows else None


def macro_f1(rows, truth_key, pred_key):
    labels = sorted({row[truth_key] for row in rows} | {row[pred_key] for row in rows})
    scores = []
    for label in labels:
        tp = sum(row[truth_key] == label and row[pred_key] == label for row in rows)
        fp = sum(row[truth_key] != label and row[pred_key] == label for row in rows)
        fn = sum(row[truth_key] == label and row[pred_key] != label for row in rows)
        denom = 2 * tp + fp + fn
        scores.append(2 * tp / denom if denom else 0.0)
    return sum(scores) / len(scores) if scores else None


def percentile(values, p):
    if not values:
        return None
    ordered = sorted(values)
    index = max(0, math.ceil(p * len(ordered)) - 1)
    return ordered[index]


def summarize_intent(rows):
    from src.nodes.intent_router import IntentRouterNode

    router = IntentRouterNode()
    # Keep model load time separate from per-message classification latency.
    warmup_start = time.perf_counter()
    model_ready = router._model_ready()
    warmup_seconds = time.perf_counter() - warmup_start
    predictions = []
    backend_counts = Counter()
    for i, sample in enumerate(rows, 1):
        start = time.perf_counter()
        result = router.process({"masked_question": sample["text"], "messages": [], "clarify_count": 0})
        elapsed = (time.perf_counter() - start) * 1000
        backend = result.get("router_backend", "unknown")
        backend_counts[backend] += 1
        predictions.append({
            "id": sample.get("id"), "lang": sample["lang"], "text": sample["text"],
            "expected_intent": sample["intent"], "predicted_intent": result.get("intent", ""),
            "expected_sub_intent": sample.get("sub_intent", ""),
            "predicted_sub_intent": result.get("sub_intent", ""),
            "intent_correct": result.get("intent") == sample["intent"],
            "exact_label_correct": (result.get("intent") == sample["intent"] and
                                    result.get("sub_intent", "") == sample.get("sub_intent", "")),
            "backend": backend, "latency_ms": round(elapsed, 2),
        })
        if i % 50 == 0 or i == len(rows):
            print(f"[intent] {i}/{len(rows)}", flush=True)

    by_lang = {}
    for lang in sorted({row["lang"] for row in predictions}):
        subset = [row for row in predictions if row["lang"] == lang]
        by_lang[lang] = {
            "count": len(subset),
            "intent_accuracy": accuracy(subset, lambda r: r["intent_correct"]),
            "exact_label_accuracy": accuracy(subset, lambda r: r["exact_label_correct"]),
            "macro_f1": macro_f1(subset, "expected_intent", "predicted_intent"),
        }
    latencies = [row["latency_ms"] for row in predictions]
    return {
        "count": len(predictions), "model_ready": model_ready,
        "backend_counts": dict(backend_counts),
        "model_load_seconds_excluded_from_latency": round(warmup_seconds, 3),
        "intent_accuracy": accuracy(predictions, lambda r: r["intent_correct"]),
        "exact_label_accuracy": accuracy(predictions, lambda r: r["exact_label_correct"]),
        "macro_f1": macro_f1(predictions, "expected_intent", "predicted_intent"),
        "latency_ms": {"mean": round(statistics.mean(latencies), 2),
                       "p50": round(statistics.median(latencies), 2),
                       "p95": round(percentile(latencies, .95), 2)},
        "by_language": by_lang,
        "predictions": predictions,
    }


def summarize_retrieval(rows):
    from src.nodes.rag_answer import RagAnswerNode

    node = RagAnswerNode()
    details = []
    for i, sample in enumerate(rows, 1):
        start = time.perf_counter()
        try:
            _, docs, info = node.retrieve(sample["question"], [tuple(item) for item in sample.get("history", [])])
            kept = [doc.get("section", "").split(" / ")[0].strip() for doc in docs]
            error = None
        except Exception as exc:
            kept = []
            error = f"{type(exc).__name__}: {str(exc)[:240]}"
        elapsed = (time.perf_counter() - start) * 1000
        expected = sample.get("expected") or []
        details.append({
            "question": sample["question"], "lang": sample["lang"], "expected": expected or None,
            "kept": kept, "source_recall": bool(expected and set(expected) & set(kept)) if expected else None,
            "top1_correct": bool(kept and kept[0] in expected) if expected else None,
            "correctly_rejected": (not kept) if not expected else None,
            "latency_ms": round(elapsed, 2), "error": error,
        })
        if i % 5 == 0 or i == len(rows):
            print(f"[faq retrieval] {i}/{len(rows)}", flush=True)

    answerable = [d for d in details if d["expected"]]
    unanswerable = [d for d in details if not d["expected"]]
    valid = [d for d in details if not d["error"]]
    by_lang = {}
    for lang in sorted({row["lang"] for row in details}):
        subset = [row for row in details if row["lang"] == lang]
        subset_answerable = [row for row in subset if row["expected"]]
        by_lang[lang] = {
            "count": len(subset), "answerable_count": len(subset_answerable),
            "expected_source_recall": accuracy(subset_answerable, lambda r: r["source_recall"]),
            "top1_source_accuracy": accuracy(subset_answerable, lambda r: r["top1_correct"]),
        }
    return {
        "count": len(details), "successful_count": len(valid), "error_count": len(details) - len(valid),
        "answerable_count": len(answerable), "expected_source_recall": accuracy(answerable, lambda r: r["source_recall"]),
        "top1_source_accuracy": accuracy(answerable, lambda r: r["top1_correct"]),
        "out_of_scope_count": len(unanswerable),
        "out_of_scope_rejection_accuracy": accuracy(unanswerable, lambda r: r["correctly_rejected"]),
        "mean_latency_ms": round(statistics.mean([d["latency_ms"] for d in valid]), 2) if valid else None,
        "by_language": by_lang, "details": details,
    }


def section_aliases():
    aliases = {}
    for path in KB_DIR.glob("*.md"):
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.startswith("## ") and " / " in line:
                zh, en = line[3:].split(" / ", 1)
                aliases[zh.strip()] = en.strip()
    return aliases


def choose_api_cases(rows, per_language):
    groups = defaultdict(list)
    for row in rows:
        if row.get("expected"):
            groups[row["lang"]].append(row)
    selected = []
    for lang in ("zh", "en", "mixed"):
        selected.extend(groups[lang][:per_language])
    return selected


def classify_citation_source(citation, citation_present=False):
    """区分 FAQ 知识库引用、产品图谱引用和无法识别的来源。"""
    normalized = (citation or "").strip().lower()
    if not normalized:
        return "other" if citation_present else "none"
    if any(marker in normalized for marker in ("术语图谱", "产品资料", "product data", "knowledge graph")):
        return "product_graph"
    if any(marker in normalized for marker in ("《", "source:", "来源：")):
        return "faq"
    return "other"


def summarize_api_sources(api_result):
    """Recalculate source attribution without sending new API requests."""
    details = api_result.get("details", [])
    for row in details:
        citation = row.get("source_citation")
        if citation is None:
            # Older reports only retained a preview. The source line is included
            # in the current API preview format and is enough for attribution.
            answer = row.get("answer_preview", "")
            citation_start = max(answer.rfind("来源："), answer.rfind("Source:"))
            citation = answer[citation_start:] if citation_start >= 0 else ""
        row["source_type"] = classify_citation_source(citation, row.get("citation_present", False))

    valid = [row for row in details if "latency_ms" in row]
    counts = Counter(row["source_type"] for row in valid)
    api_result["source_type_counts"] = dict(counts)
    api_result["faq_expected_source_citation_accuracy"] = accuracy(
        valid, lambda row: row.get("source_cited_correctly", False))
    for lang, stats in api_result.get("by_language", {}).items():
        subset = [row for row in valid if row.get("lang") == lang]
        stats["source_type_counts"] = dict(Counter(row["source_type"] for row in subset))
        stats["faq_expected_source_citation_accuracy"] = accuracy(
            subset, lambda row: row.get("source_cited_correctly", False))
    return api_result


def post_json(url, payload, timeout):
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    request = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"}, method="POST")
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return response.status, json.loads(response.read().decode("utf-8"))


def summarize_api(rows, api_base, per_language, timeout):
    aliases = section_aliases()
    selected = choose_api_cases(rows, per_language)
    details = []
    stopped_early_reason = None
    for i, sample in enumerate(selected, 1):
        record = {"question": sample["question"], "lang": sample["lang"], "expected": sample["expected"]}
        try:
            _, session = post_json(f"{api_base}/api/sessions", {"customer_id": "C001"}, timeout)
            start = time.perf_counter()
            status, result = post_json(f"{api_base}/api/sessions/{session['session_id']}/messages",
                                       {"text": sample["question"]}, timeout)
            elapsed = (time.perf_counter() - start) * 1000
            answer = result.get("answer", "")
            citation_start = max(answer.rfind("来源："), answer.rfind("Source:"))
            citation = answer[citation_start:] if citation_start >= 0 else ""
            lang = sample["lang"]
            expected_aliases = []
            for section in sample["expected"]:
                expected_aliases.append(section if lang != "en" else aliases.get(section, section))
            record.update({"http_status": status, "latency_ms": round(elapsed, 2),
                           "source_cited_correctly": any(alias in citation for alias in expected_aliases),
                           "citation_present": bool(citation), "source_citation": citation,
                           "pending": result.get("pending"),
                           "answer_preview": answer[:320]})
            if result.get("pending") or any(marker in answer.lower() for marker in
                                             ("已创建人工工单", "已为您创建工单", "ticket has been created")):
                stopped_early_reason = f"检测到非 FAQ 流程（pending={result.get('pending')!r}）；为避免继续触发验证或工单，已停止后续 API 样本。"
                record["safety_route_detected"] = True
        except (urllib.error.URLError, TimeoutError, KeyError, json.JSONDecodeError) as exc:
            record.update({"error": f"{type(exc).__name__}: {str(exc)[:240]}", "source_cited_correctly": False})
        details.append(record)
        print(f"[live API] {i}/{len(selected)} {sample['lang']} {record.get('latency_ms', 'ERROR')} ms", flush=True)
        if stopped_early_reason:
            break

    valid = [d for d in details if "latency_ms" in d]
    latencies = [d["latency_ms"] for d in valid]
    by_lang = {}
    for lang in ("zh", "en", "mixed"):
        subset = [row for row in details if row["lang"] == lang]
        subset_valid = [row for row in subset if "latency_ms" in row]
        by_lang[lang] = {
            "count": len(subset), "successful_count": len(subset_valid),
            "source_citation_accuracy": accuracy(subset_valid, lambda r: r["source_cited_correctly"]),
            "mean_latency_ms": round(statistics.mean(r["latency_ms"] for r in subset_valid), 2) if subset_valid else None,
        }
    result = {
        "api_base": api_base, "selected_count": len(selected), "successful_count": len(valid),
        "per_language_requested": per_language,
        "stopped_early_reason": stopped_early_reason,
        "source_citation_accuracy": accuracy(valid, lambda r: r["source_cited_correctly"]),
        "citation_presence_rate": accuracy(valid, lambda r: r.get("citation_present", False)),
        "latency_ms": {"mean": round(statistics.mean(latencies), 2),
                       "p50": round(statistics.median(latencies), 2),
                       "p95": round(percentile(latencies, .95), 2)} if latencies else None,
        "by_language": by_lang, "details": details,
    }
    return summarize_api_sources(result)


def format_pct(value):
    return "N/A" if value is None else f"{value:.1%}"


def write_report(result, output_dir):
    stamp = result["run_id"]
    output_dir.mkdir(parents=True, exist_ok=True)
    json_path = output_dir / f"evaluation_{stamp}.json"
    md_path = output_dir / f"evaluation_{stamp}.md"
    json_path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    lines = [f"# Bank CS 评估报告 {stamp}", "", f"- 评估时间：{result['timestamp']}",
             f"- 代码版本：`{result['git_commit']}`（dirty={result['git_dirty']}）",
             f"- 数据集 SHA-256：intent `{result['dataset_sha256']['intent']}`；FAQ `{result['dataset_sha256']['faq']}`；API FAQ `{result['dataset_sha256'].get('api_faq', '旧报告未记录')}`", ""]
    if result.get("stage_provenance"):
        lines += ["阶段运行记录："]
        for stage, run_id in result["stage_provenance"].items():
            lines.append(f"- {stage}：`{run_id}`")
        lines.append("")
    if result.get("audit_note"):
        lines += [f"> 评估审计：{result['audit_note']}", ""]
    if result.get("intent"):
        item = result["intent"]
        lines += ["## 意图分类（本机真实路由器）", "",
                  f"- 一级意图准确率：{format_pct(item['intent_accuracy'])}；宏平均 F1：{item['macro_f1']:.3f}",
                  f"- 一级+二级标签完全准确率：{format_pct(item['exact_label_accuracy'])}",
                  f"- 数据：{item['count']} 条；实际后端：`{item['backend_counts']}`；模型预热/加载 {item['model_load_seconds_excluded_from_latency']} 秒（不计入单条延迟）",
                  f"- 单条分类耗时：均值 {item['latency_ms']['mean']} ms，P50 {item['latency_ms']['p50']} ms，P95 {item['latency_ms']['p95']} ms", "",
                  "| 语言 | 条数 | 一级准确率 | 完全标签准确率 | Macro-F1 |", "|---|---:|---:|---:|---:|"]
        for lang, stats in item["by_language"].items():
            lines.append(f"| {lang} | {stats['count']} | {format_pct(stats['intent_accuracy'])} | {format_pct(stats['exact_label_accuracy'])} | {stats['macro_f1']:.3f} |")
        wrong_l1 = [row for row in item["predictions"] if not row["intent_correct"]]
        confusion = Counter((row["expected_intent"], row["predicted_intent"]) for row in wrong_l1)
        if confusion:
            lines += ["", "一级意图主要混淆："]
            for (expected, predicted), count in confusion.most_common(8):
                lines.append(f"- `{expected}` → `{predicted}`：{count} 条")
        wrong_labels = [row for row in item["predictions"] if not row["exact_label_correct"]]
        if wrong_labels:
            lines += ["", f"一级正确但二级标签错误/缺失：{len(wrong_labels) - len(wrong_l1)} 条；完全标签错误样本共 {len(wrong_labels)} 条。"]
        lines.append("")
    if result.get("retrieval"):
        item = result["retrieval"]
        lines += ["## FAQ 来源检索（RAG 实际检索链）", "",
                  f"- 预期来源召回率：{format_pct(item['expected_source_recall'])}（正确来源是否在最终保留章节中）",
                  f"- Top-1 来源准确率：{format_pct(item['top1_source_accuracy'])}（首个保留章节是否正确）",
                  f"- 知识库外拒答准确率：{format_pct(item['out_of_scope_rejection_accuracy'])}（{item['out_of_scope_count']} 条）",
                  f"- 检索平均耗时：{item['mean_latency_ms']} ms；成功 {item['successful_count']}/{item['count']}，失败 {item['error_count']} 条", ""]
        source_misses = [row for row in item["details"] if row["expected"] and not row["source_recall"]]
        if source_misses:
            lines += ["检索未命中预期来源："]
            for row in source_misses[:8]:
                question = row["question"].replace("\n", " ")[:90]
                lines.append(f"- [{row['lang']}] {question}；预期 `{', '.join(row['expected'])}`；实际 `{', '.join(row['kept']) or '无'}`")
            lines.append("")
    if result.get("api"):
        item = result["api"]
        latency = item["latency_ms"]
        source_counts = item.get("source_type_counts", {})
        lines += ["## FastAPI 端到端时延与引用", "",
                  f"- 平均响应时间：{latency['mean'] if latency else 'N/A'} ms；P50 {latency['p50'] if latency else 'N/A'} ms；P95 {latency['p95'] if latency else 'N/A'} ms。计时只包括发送消息到收到答复，不含新建会话。",
                  f"- FAQ 预期标题严格匹配率：{format_pct(item.get('faq_expected_source_citation_accuracy', item['source_citation_accuracy']))}；引用出现率：{format_pct(item['citation_presence_rate'])}。实际请求 {item['successful_count']}/{item['selected_count']}。",
                  f"- 引用来源类型（按成功请求）：FAQ {source_counts.get('faq', 0)}，产品图谱 {source_counts.get('product_graph', 0)}，其他 {source_counts.get('other', 0)}，无来源 {source_counts.get('none', 0)}。", "",
                  "| 语言 | 条数 | FAQ 标题严格匹配率 | 产品图谱引用 | 平均响应时间 |", "|---|---:|---:|---:|---:|"]
        for lang, stats in item["by_language"].items():
            lang_source_counts = stats.get("source_type_counts", {})
            strict_accuracy = stats.get("faq_expected_source_citation_accuracy", stats["source_citation_accuracy"])
            lines.append(f"| {lang} | {stats['successful_count']}/{stats['count']} | {format_pct(strict_accuracy)} | {lang_source_counts.get('product_graph', 0)} | {stats['mean_latency_ms'] if stats['mean_latency_ms'] is not None else 'N/A'} ms |")
        citation_misses = [row for row in item["details"] if not row.get("source_cited_correctly")]
        if item.get("stopped_early_reason"):
            lines += ["", f"- 安全停止：{item['stopped_early_reason']}"]
        if citation_misses:
            lines += ["", "未能匹配预期 FAQ 标题的端到端样本（含产品图谱回答；不据此判定事实错误）："]
            for row in citation_misses[:10]:
                question = row["question"].replace("\n", " ")[:90]
                preview = row.get("answer_preview", row.get("error", "请求失败")).replace("\n", " ")[:140]
                lines.append(f"- [{row['lang']}] {question}；来源类型 `{row.get('source_type', 'unknown')}`；预期 FAQ `{', '.join(row['expected'])}`；答复片段：{preview}")
        lines.append("")
    lines += ["## 指标口径与限制", "",
              "- 意图集是人工复核的留出集；准确率是一级意图完全相等的比例，Macro-F1 对每个意图等权。",
              "- FAQ 检索指标检查保留章节与人工标注的 `expected` 是否相符；API 的 FAQ 标题严格匹配率仅衡量是否引用预期 FAQ 标题。产品图谱是独立的公开数据源，单独统计，不计作 FAQ 标题命中或错误答案；所有来源指标都不等于完整事实性评审。",
              "- 混合语言问题按 FAQ 测试集的 `mixed` 标签归组；API 时延子集按每种语言各取固定前 N 条。模型/服务、网络、缓存和机器负载都会影响单次时延。",
              "- 端到端 API 子集只使用一般性 FAQ 问法，不包含个人账户、交易查询、投诉或办理请求；每次请求仍会创建演示会话。", ""]
    md_path.write_text("\n".join(lines), encoding="utf-8")
    return json_path, md_path


def main():
    parser = argparse.ArgumentParser(description="银行客服真实链路质量评估")
    parser.add_argument("--api-base", default="http://127.0.0.1:8002")
    parser.add_argument("--api-samples-per-language", type=int, default=6)
    parser.add_argument("--api-timeout", type=int, default=180)
    parser.add_argument("--skip-intent", action="store_true")
    parser.add_argument("--skip-retrieval", action="store_true")
    parser.add_argument("--skip-api", action="store_true")
    parser.add_argument("--reclassify-report", type=Path,
                        help="仅重算已有评估 JSON 的 API 来源类型并刷新报告，不调用模型或 API")
    args = parser.parse_args()

    if args.reclassify_report:
        report_path = args.reclassify_report
        result = json.loads(report_path.read_text(encoding="utf-8"))
        if not result.get("api"):
            parser.error(f"报告没有 API 评估结果：{report_path}")
        summarize_api_sources(result["api"])
        json_path, md_path = write_report(result, report_path.parent)
        print(f"已基于现有结果重算来源类型（未发送请求）：\n  {md_path}\n  {json_path}")
        return

    intent_rows = load_jsonl(INTENT_DATA)
    faq_rows = load_jsonl(FAQ_DATA)
    api_rows = load_jsonl(API_DATA)
    try:
        commit = __import__("subprocess").check_output(["git", "rev-parse", "--short", "HEAD"], cwd=PROJECT_ROOT, text=True).strip()
        dirty = bool(__import__("subprocess").check_output(["git", "status", "--porcelain"], cwd=PROJECT_ROOT, text=True).strip())
    except Exception:
        commit, dirty = "unknown", None
    result = {
        "run_id": datetime.now().astimezone().strftime("%Y%m%d_%H%M%S"),
        "timestamp": datetime.now().astimezone().isoformat(timespec="seconds"),
        "git_commit": commit, "git_dirty": dirty,
        "dataset_sha256": {"intent": sha256(INTENT_DATA), "faq": sha256(FAQ_DATA), "api_faq": sha256(API_DATA)},
        "dataset_counts": {"intent": len(intent_rows), "faq": len(faq_rows),
                           "api_faq": len(api_rows),
                           "intent_language": dict(Counter(row["lang"] for row in intent_rows)),
                           "faq_language": dict(Counter(row["lang"] for row in faq_rows)),
                           "api_faq_language": dict(Counter(row["lang"] for row in api_rows))},
    }
    if not args.skip_intent:
        result["intent"] = summarize_intent(intent_rows)
    if not args.skip_retrieval:
        result["retrieval"] = summarize_retrieval(faq_rows)
    if not args.skip_api:
        result["api"] = summarize_api(api_rows, args.api_base.rstrip("/"),
                                      args.api_samples_per_language, args.api_timeout)
    json_path, md_path = write_report(result, RESULT_DIR)
    print(f"\n评估报告已保存：\n  {md_path}\n  {json_path}")


if __name__ == "__main__":
    main()
