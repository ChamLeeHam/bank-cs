# 实现多路融合排名（参考课程 03-rag/rrf.py）：加权 RRF，score = Σ weight / (k + 排名)
# 第一路：问题本身的混合检索（权重 1.0）；第二路：HyDE 检索（权重 0.7，假设答案可能带偏，权重低一些）
# 按块的 id 合并：同一个章节在两路中都排得靠前，得分最高
from typing import Any, Dict, List

from src.schema.config import get_config


def process(embedding_chunks, hyde_embedding_chunks):
    """
    采用 RRF 算法对两路结果进行合并排序
    :param embedding_chunks: 问题检索的结果
    :param hyde_embedding_chunks: HyDE 检索的结果
    :return: {"rrf_chunks": [...]}
    """
    config = get_config()

    # 1. 两路结果及其权重
    sources = {
        "embedding": (embedding_chunks or [], 1.0),
        "hyde": (hyde_embedding_chunks or [], config.hyde_weight),
    }

    # 2. 使用 RRF 算法对两路结果进行排序
    rrf_results = rrf(list(sources.values()), k=60, max_result=config.rag_candidates)

    # 3. 返回排序后的结果
    return {"rrf_chunks": [{**chunk, "rrf_score": round(score, 5)} for chunk, score in rrf_results]}


def rrf(source_weights, k=60, max_result=10):
    """
    实现 RRF 算法
    :param source_weights: [(文档列表，权重), ...]
    :param k: RRF 平滑系数
    :param max_result: 返回的最大结果数
    :return: [(文档, 得分), ...]，按得分从高到低
    """
    chunk_map: Dict[Any, Dict] = {}  # chunk_id -> chunk
    score_map: Dict[Any, float] = {}  # chunk_id -> 累计得分

    for rank_list, weight in source_weights:
        for pos, item in enumerate(rank_list, start=1):
            chunk_id = item.get("id")
            if chunk_id is None:
                continue
            # RRF 公式：weight / (k + rank)
            score_map[chunk_id] = score_map.get(chunk_id, 0.0) + weight / (k + pos)
            chunk_map.setdefault(chunk_id, item)

    # 根据 score_map 排序并截断
    result = sorted([(chunk_map[cid], score) for cid, score in score_map.items()], key=lambda x: x[1], reverse=True)
    return result[:max_result] if max_result else result


if __name__ == "__main__":
    mock_embedding = [{"id": 1, "section": "A"}, {"id": 2, "section": "B"}, {"id": 3, "section": "C"}]
    mock_hyde = [{"id": 2, "section": "B"}, {"id": 4, "section": "D"}, {"id": 1, "section": "A"}]
    print([(c["section"], c["rrf_score"]) for c in process(mock_embedding, mock_hyde)["rrf_chunks"]])
