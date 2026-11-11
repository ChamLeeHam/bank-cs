# 根据用户的问题进行混合检索，返回相应的块（参考课程 03-rag/search_embedding.py）
import logging

from src.schema.config import get_config
from src.utils.bge_m3_embedding_utils import generate_dense_embedding
from src.utils.milvus_utils import create_hybrid_search_request, execute_hybrid_search_request, get_milvus_client

logger = logging.getLogger(__name__)


def process(query: str, intent: str = None):
    config = get_config()

    # 1. 生成查询的稠密向量
    logger.info("1. 生成查询向量")
    embeddings = generate_dense_embedding([query])  # {"dense": [...]}

    # 2. 构建标量条件：根据一级意图筛选（为空时不筛选）
    filter_expr = _build_filter_expr(intent)

    # 3. 构建混合检索：每一路多取一些候选，融合后再截取
    requests = create_hybrid_search_request(
        dense_vector=embeddings["dense"][0],
        query_text=query,
        expr=filter_expr,
        limit=config.rag_candidates * 3,
    )

    # 4. 执行混合检索
    client = get_milvus_client()
    result = execute_hybrid_search_request(
        client=client,
        collection_name=config.kb_collection,
        requests=requests,
        limit=config.rag_candidates,
    )
    logger.info("4. 执行完成混合检索")
    chunks = _extract_entities(result[0] if result else [])
    return {"embedding_chunks": chunks}


def _build_filter_expr(intent: str):
    if not intent:
        return None
    return f'intent == "{intent}"'


def _extract_entities(hits) -> list[dict]:
    """把 Milvus 的命中结果整理成块：{doc, title, section, ..., id, score}"""
    return [{**hit["entity"], "id": hit["id"], "score": round(hit["distance"], 4)} for hit in hits]


if __name__ == "__main__":
    for q in ["How much is the overseas TT fee?", "我的 PIN 被 lock 了怎么办"]:
        chunks = process(q)["embedding_chunks"]
        print(q, "->", [c["section"].split(" / ")[0] for c in chunks[:3]])
