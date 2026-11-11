"""
RAG 评估（参考课程 03-rag/18-rag_rrf_eval.py、20-rag_rrf_eval_reranker.py、17-ragas-eval.py）

测试集：data/kb/eval_questions.jsonl（36 条：30 条知识库内的问题，4 条知识库外的问题，2 条依赖上一轮对话的追问）

第一部分：检索评估（逐步加入课程流程中的各个环节，看每一步的作用）
    A 混合检索              —— 只用原问题，稠密 + BM25
    B A + 问题重写
    C B + HyDE + 加权 RRF
    D C + reranker 精排     —— 完整流程（精排后的排序）
  指标：Hit@1、Hit@3、MRR（正确章节排名的倒数的平均）；
       完整流程另外统计：知识库内的问题是否保留了正确章节、知识库外的问题是否正确判为"没有答案"

第二部分：RAGAS（--ragas），对完整流程生成的回答打分，评判模型用 DeepSeek，向量用本地 BGE-M3：
    ContextRelevance（检索内容是否与问题相关）、AnswerRelevancy（回答是否切题）、
    Faithfulness（回答中的事实能否被检索内容支撑）、ResponseGroundedness（回答是否由检索内容支撑）

运行（在项目根目录）：
    uv run python src/scripts/eval_rag.py           # 检索评估
    uv run python src/scripts/eval_rag.py --ragas   # 检索评估 + RAGAS
"""
import argparse
import json
import logging
import sys
import time
from pathlib import Path

PROJECT_ROOT = Path(__file__).parent.parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from src.schema.config import get_config

config = get_config()
QUESTIONS = config.kb_dir / "eval_questions.jsonl"
OUT_DIR = config.data_dir / "kb" / "eval"


def section_name(chunk: dict) -> str:
    return chunk["section"].split(" / ")[0].strip()


def rank_of(chunks: list[dict], expected: list[str]) -> int | None:
    for i, c in enumerate(chunks, 1):
        if section_name(c) in expected:
            return i
    return None


def retrieval_eval(rows: list[dict]) -> dict:
    from src.nodes.rag_answer import RagAnswerNode
    from src.utils.bge_m3_embedding_utils import generate_dense_embedding
    from src.utils.milvus_utils import get_milvus_client, hybrid_search
    from src.utils.rewrite_query_utils import rewrite_query

    node = RagAnswerNode()
    client = get_milvus_client()
    stages = {"A 混合检索": [], "B +问题重写": [], "C +HyDE+RRF": [], "D +reranker": []}
    details = []
    for row in rows:
        q, history = row["question"], [tuple(h) for h in row.get("history", [])]
        start = time.perf_counter()
        a = hybrid_search(client, q, generate_dense_embedding([q])[0], top_k=config.rag_candidates)
        rewritten = rewrite_query(q, history)
        b = hybrid_search(client, rewritten, generate_dense_embedding([rewritten])[0], top_k=config.rag_candidates)
        _, kept, info = node.retrieve(q, history)  # 完整流程：重写 + 两路检索 + RRF + 精排 + 断崖截断
        elapsed = time.perf_counter() - start
        results = {"A 混合检索": a, "B +问题重写": b, "C +HyDE+RRF": info["fused"], "D +reranker": info["reranked"]}
        detail = {"question": q, "expected": row["expected"], "rewritten": info["rewritten"], "hyde": info["hyde"],
                  "best_score": info["best_score"], "kept": [section_name(c) for c in kept], "seconds": round(elapsed, 1)}
        for stage, chunks in results.items():
            detail[stage] = rank_of(chunks, row["expected"]) if row["expected"] else None
            if row["expected"]:
                stages[stage].append(detail[stage])
        detail["top_reranked"] = [(section_name(c), c["rerank_score"]) for c in info["reranked"][:3]]
        details.append(detail)
        print(f"  {'✓' if (not row['expected'] and not kept) or (row['expected'] and rank_of(kept, row['expected'])) else '✗'}"
              f" {q[:40]:<42} 保留 {detail['kept']}  最高分 {detail['best_score']}")

    summary = {}
    for stage, ranks in stages.items():
        n = len(ranks)
        summary[stage] = {"hit@1": sum(r == 1 for r in ranks if r) / n, "hit@3": sum(bool(r and r <= 3) for r in ranks) / n,
                          "mrr": sum(1 / r for r in ranks if r) / n}
    answerable = [d for d in details if d["expected"]]
    unanswerable = [d for d in details if not d["expected"]]
    summary["完整流程"] = {
        "知识库内：保留了正确章节": sum(any(s in d["expected"] for s in d["kept"]) for d in answerable) / len(answerable),
        "知识库外：正确判为没有答案": sum(not d["kept"] for d in unanswerable) / len(unanswerable),
        "平均保留章节数": sum(len(d["kept"]) for d in answerable) / len(answerable),
        "平均检索用时（秒）": sum(d["seconds"] for d in details) / len(details),
    }
    return {"summary": summary, "details": details}


def generate_samples(rows: list[dict]) -> list[dict]:
    """完整流程为知识库内的问题生成回答（知识库外的问题没有检索内容，不适用 RAGAS 指标）"""
    from src.nodes.rag_answer import RagAnswerNode

    node = RagAnswerNode()
    samples = []
    for row in rows:
        if not row["expected"]:
            continue
        lang = "en" if row["lang"] == "en" else "zh"
        result = node.answer(row["question"], [tuple(h) for h in row.get("history", [])], lang)
        samples.append({"query": node.last_run.get("rewritten", row["question"]),
                        "answer": result["answer"].split("\n\n")[0],
                        "contexts": [c["text"] for c in node.last_run.get("kept", [])]})
        print(f"  已生成回答 {len(samples)}：{row['question'][:40]}")
    return samples


def ragas_eval(samples: list[dict]) -> dict:
    """用 RAGAS 的四个指标打分（课程 18-rag_eval.py 的做法）；评判请求逐个发送并失败重试，避免并发时被限流"""
    import asyncio

    from openai import AsyncOpenAI
    from ragas.embeddings.base import embedding_factory
    from ragas.llms.base import llm_factory
    from ragas.metrics.collections import AnswerRelevancy, ContextRelevance, Faithfulness, ResponseGroundedness

    client = AsyncOpenAI(api_key=config.deepseek_api_key, base_url=config.deepseek_base_url)
    # max_tokens 要设大：deepseek-v4-flash 回答前会先输出推理内容，RAGAS 默认的长度不够，
    # Faithfulness 逐条判断事实时输出会被截断（IncompleteOutputException）
    judge = llm_factory(config.deepseek_model, client=client, max_tokens=8192)
    embeddings = embedding_factory("huggingface", model=str(config.embed_model))
    metrics = {"context_relevance": ContextRelevance(llm=judge),
               "answer_relevancy": AnswerRelevancy(llm=judge, embeddings=embeddings),
               "faithfulness": Faithfulness(llm=judge),
               "response_groundedness": ResponseGroundedness(llm=judge)}

    async def with_retry(make_call, tries: int = 3):
        for attempt in range(tries):
            try:
                return float((await make_call()).value)
            except Exception as e:
                if attempt == tries - 1:
                    print(f"    评分失败：{type(e).__name__}: {str(e)[:120]}")
                    return None
                await asyncio.sleep(2 * (attempt + 1))

    async def score_all():
        scored = []
        for i, s in enumerate(samples, 1):
            if not s["contexts"]:
                continue
            q, a, ctx = s["query"], s["answer"], s["contexts"]
            calls = {
                "context_relevance": lambda: metrics["context_relevance"].ascore(user_input=q, retrieved_contexts=ctx),
                "answer_relevancy": lambda: metrics["answer_relevancy"].ascore(user_input=q, response=a),
                "faithfulness": lambda: metrics["faithfulness"].ascore(user_input=q, response=a, retrieved_contexts=ctx),
                "response_groundedness": lambda: metrics["response_groundedness"].ascore(response=a, retrieved_contexts=ctx),
            }
            row = dict(s)
            for name, make_call in calls.items():
                row[name] = await with_retry(make_call)
            scored.append(row)
            print(f"  已评分 {i}/{len(samples)}")
        return scored

    scored = asyncio.run(score_all())
    means = {name: sum(s[name] for s in scored if s[name] is not None) / max(1, sum(s[name] is not None for s in scored))
             for name in metrics}
    counts = {name: sum(s[name] is not None for s in scored) for name in metrics}
    return {"means": means, "counts": counts, "samples": scored}


def main() -> None:
    parser = argparse.ArgumentParser(description="RAG 评估")
    parser.add_argument("--ragas", action="store_true", help="同时运行 RAGAS 回答评估")
    parser.add_argument("--rescore", action="store_true", help="只用上次保存的回答重新跑 RAGAS（不重新检索和生成）")
    args = parser.parse_args()
    logging.basicConfig(level=logging.WARNING)
    rows = [json.loads(line) for line in QUESTIONS.read_text(encoding="utf-8").splitlines() if line.strip()]
    OUT_DIR.mkdir(parents=True, exist_ok=True)

    if args.rescore:
        saved = json.loads((OUT_DIR / "ragas_eval.json").read_text(encoding="utf-8"))["samples"]
        samples = [{k: s[k] for k in ("query", "answer", "contexts")} for s in saved]
        ragas = ragas_eval(samples)
        for k, v in ragas["means"].items():
            print(f"  {k:<24}{v:.3f}  （{ragas['counts'][k]} 条）")
        (OUT_DIR / "ragas_eval.json").write_text(json.dumps(ragas, ensure_ascii=False, indent=1), encoding="utf-8")
        return

    print(f"检索评估：{len(rows)} 条问题")
    result = retrieval_eval(rows)
    print(f"\n{'阶段':<14}{'Hit@1':>8}{'Hit@3':>8}{'MRR':>8}")
    for stage, m in result["summary"].items():
        if "hit@1" in m:
            print(f"{stage:<14}{m['hit@1']:>8.1%}{m['hit@3']:>8.1%}{m['mrr']:>8.3f}")
    for k, v in result["summary"]["完整流程"].items():
        print(f"  {k}：{v:.1%}" if v <= 1 and "章节数" not in k and "秒" not in k else f"  {k}：{v:.1f}")
    (OUT_DIR / "retrieval_eval.json").write_text(json.dumps(result, ensure_ascii=False, indent=1), encoding="utf-8")

    if args.ragas:
        print("\nRAGAS 回答评估（评判模型 DeepSeek）：")
        ragas = ragas_eval(generate_samples(rows))
        for k, v in ragas["means"].items():
            print(f"  {k:<24}{v:.3f}  （{ragas['counts'][k]} 条）")
        (OUT_DIR / "ragas_eval.json").write_text(json.dumps(ragas, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"\n结果已保存到 {OUT_DIR}")


if __name__ == "__main__":
    main()
