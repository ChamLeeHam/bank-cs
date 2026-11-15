# 根据问题做混合检索，返回相关的块
import logging

from src.schema.config import get_config
from src.utils.bge_m3_embedding_utils import generate_hybrid_embedding
from src.utils.milvus_utils import create_hybrid_search_request, execute_hybrid_search_request, get_milvus_client

logger = logging.getLogger(__name__)


def process(query, intent=None, collection_name=None, doc_prefix=None):
    """collection_name 不传就查狮城银行的 FAQ（kb_collection）"""
    config = get_config()
    collection_name = collection_name or config.kb_collection
    if doc_prefix is None and collection_name == config.kb_collection:
        # Shared demo collection: default FAQ queries stay within Lion City Bank entries.
        doc_prefix = "lcb__faq__"

    # 1. 问题转成混合向量 {"dense": [...], "sparse": [...]}
    embeddings = generate_hybrid_embedding([query])

    # 2. 按一级意图过滤（不传就不过滤）
    filter_expr = _build_filter_expr(intent, doc_prefix)

    # 3. 构建检索请求，每一路多取一些，合并后再截取
    requests = create_hybrid_search_request(
        dense_vector=embeddings["dense"][0],
        sparse_vector=embeddings["sparse"][0],
        expr=filter_expr,
        limit=config.rag_candidates * 3,
    )

    # 4. 执行检索
    client = get_milvus_client()
    result = execute_hybrid_search_request(
        client=client,
        collection_name=collection_name,
        requests=requests,
        limit=config.rag_candidates,
    )
    logger.info("混合检索完成")

    hits = result[0] if result else []
    return {"embedding_chunks": _extract_entities(hits)}


def _build_filter_expr(intent, doc_prefix=None):
    filters = []
    if intent:
        filters.append(f'intent == "{intent}"')
    if doc_prefix:
        # Prefixes come from the fixed internal bank registry, not from user input.
        filters.append(f'doc like "{doc_prefix}%"')
    return " and ".join(filters) or None


def _extract_entities(hits):
    """把检索结果整理成字典列表"""
    chunks = []
    for hit in hits:
        chunk = dict(hit["entity"])
        chunk["id"] = hit["id"]
        chunk["score"] = round(hit["distance"], 4)
        chunks.append(chunk)
    return chunks


if __name__ == "__main__":
    for q in ["How much is the overseas TT fee?", "我的 PIN 被 lock 了怎么办"]:
        chunks = process(q)["embedding_chunks"]
        print(q, "->", [c["section"].split(" / ")[0] for c in chunks[:3]])
