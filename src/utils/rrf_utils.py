# 用加权 RRF 合并两路检索结果：问题检索权重 1.0，HyDE 检索权重 0.7
from src.schema.config import get_config


def process(embedding_chunks, hyde_embedding_chunks):
    config = get_config()

    # 1. 两路结果和各自的权重
    sources = [
        (embedding_chunks or [], 1.0),
        (hyde_embedding_chunks or [], config.hyde_weight),
    ]

    # 2. RRF 排序
    rrf_results = rrf(sources, k=60, max_result=config.rag_candidates)

    # 3. 把分数记到块里
    rrf_chunks = []
    for chunk, score in rrf_results:
        chunk = dict(chunk)
        chunk["rrf_score"] = round(score, 5)
        rrf_chunks.append(chunk)
    return {"rrf_chunks": rrf_chunks}


def rrf(source_weights, k=60, max_result=10):
    """
    source_weights: [(块列表, 权重), ...]
    每个块的得分 = 权重 / (k + 排名)，在两路里都出现的块分数会累加
    """
    chunk_map = {}  # id -> 块
    score_map = {}  # id -> 累计得分

    for rank_list, weight in source_weights:
        for pos, item in enumerate(rank_list, start=1):
            chunk_id = item.get("id")
            if chunk_id is None:
                continue
            score_map[chunk_id] = score_map.get(chunk_id, 0.0) + weight / (k + pos)
            chunk_map.setdefault(chunk_id, item)

    result = [(chunk_map[cid], score) for cid, score in score_map.items()]
    result.sort(key=lambda x: x[1], reverse=True)
    return result[:max_result] if max_result else result


if __name__ == "__main__":
    mock_embedding = [{"id": 1, "section": "A"}, {"id": 2, "section": "B"}, {"id": 3, "section": "C"}]
    mock_hyde = [{"id": 2, "section": "B"}, {"id": 4, "section": "D"}, {"id": 1, "section": "A"}]
    print([(c["section"], c["rrf_score"]) for c in process(mock_embedding, mock_hyde)["rrf_chunks"]])
