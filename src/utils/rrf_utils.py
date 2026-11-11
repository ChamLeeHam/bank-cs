"""
多路检索结果融合（参考课程 03-rag/rrf.py）：加权 RRF，score = Σ weight / (k + 排名)

第一路：问题本身的混合检索（权重 1.0）；第二路：HyDE 检索（权重 0.7，假设答案可能带偏，权重低一些）
按块的 id 合并：同一个章节在两路中都排得靠前，得分最高
"""


def weighted_rrf(ranked_lists: list[tuple[list[dict], float]], k: int = 60, max_result: int = 10) -> list[dict]:
    """ranked_lists: [(结果列表, 权重), ...]；返回融合后的块，带 rrf_score，按得分从高到低"""
    chunks: dict[int, dict] = {}
    scores: dict[int, float] = {}
    for results, weight in ranked_lists:
        for rank, chunk in enumerate(results, start=1):
            cid = chunk["id"]
            scores[cid] = scores.get(cid, 0.0) + weight / (k + rank)
            chunks.setdefault(cid, chunk)
    ordered = sorted(scores, key=scores.get, reverse=True)[:max_result]
    return [{**chunks[cid], "rrf_score": round(scores[cid], 5)} for cid in ordered]
