# Milvus 向量数据库的操作：建知识库集合、混合检索（bge-m3 的稠密向量 + 稀疏向量，RRF 合并）
import logging

from pymilvus import AnnSearchRequest, DataType, MilvusClient, RRFRanker

from src.schema.config import get_config
from src.schema.exceptions import MilvusError

logger = logging.getLogger(__name__)

EMBED_DIM = 1024
OUTPUT_FIELDS = ["doc", "title", "section", "intent", "sub_intents", "text"]

milvus_client = None


# 1. 获取客户端
def get_milvus_client():
    global milvus_client
    if milvus_client is not None:
        return milvus_client

    config = get_config()
    try:
        milvus_client = MilvusClient(uri=config.milvus_uri, token=config.milvus_token or None)
        return milvus_client
    except Exception as e:
        logger.error(f"Milvus客户端创建失败：{e}")
        raise MilvusError(f"无法连接 Milvus：{config.milvus_uri}", "milvus_utils", e)


# 2. 创建知识库集合（已存在就先删掉重建）
def create_kb_collection(client, collection_name):
    if client.has_collection(collection_name):
        client.drop_collection(collection_name)

    # 字段
    schema = client.create_schema(auto_id=True, enable_dynamic_field=False)
    schema.add_field("id", DataType.INT64, is_primary=True)
    schema.add_field("doc", DataType.VARCHAR, max_length=64)  # 文件名
    schema.add_field("title", DataType.VARCHAR, max_length=200)  # 文档标题
    schema.add_field("section", DataType.VARCHAR, max_length=200)  # 章节标题
    schema.add_field("intent", DataType.VARCHAR, max_length=50)  # 一级意图
    schema.add_field("sub_intents", DataType.ARRAY, element_type=DataType.VARCHAR, max_capacity=16, max_length=60)
    schema.add_field("text", DataType.VARCHAR, max_length=8192)  # 章节内容（中英两段）
    # 两个向量都是 bge-m3 生成的（generate_hybrid_embedding）
    schema.add_field("dense_vector", DataType.FLOAT_VECTOR, dim=EMBED_DIM)
    schema.add_field("sparse_vector", DataType.SPARSE_FLOAT_VECTOR)

    # 索引，和课程一样：稠密向量 HNSW，稀疏向量倒排索引，都用内积
    index_params = client.prepare_index_params()
    index_params.add_index(
        field_name="dense_vector",
        index_name="dense_vector_index",
        index_type="HNSW",
        metric_type="IP",
    )
    index_params.add_index(
        field_name="sparse_vector",
        index_name="sparse_inverted_index",
        index_type="SPARSE_INVERTED_INDEX",
        metric_type="IP",
        params={"inverted_index_algo": "DAAT_MAXSCORE"},
    )
    client.create_collection(collection_name, schema=schema, index_params=index_params)


# 3. 混合检索的两个请求：稠密向量 + 稀疏向量
def create_hybrid_search_request(dense_vector, sparse_vector, expr=None, limit=10):
    dense_req = AnnSearchRequest(
        data=[dense_vector],
        anns_field="dense_vector",
        param={"metric_type": "IP"},
        expr=expr,
        limit=limit,
    )
    sparse_req = AnnSearchRequest(
        data=[sparse_vector],
        anns_field="sparse_vector",
        param={"metric_type": "IP"},
        expr=expr,
        limit=limit,
    )
    return [dense_req, sparse_req]


# 4. 执行混合检索，两路结果用 RRF 合并
def execute_hybrid_search_request(client, collection_name, requests, rrf_k=60, output_fields=None, limit=10):
    if output_fields is None:
        output_fields = OUTPUT_FIELDS
    try:
        return client.hybrid_search(
            collection_name=collection_name,
            reqs=requests,
            ranker=RRFRanker(rrf_k),
            output_fields=output_fields,
            limit=limit,
        )
    except Exception as e:
        logger.error(f"混合检索失败：{e}")
        raise MilvusError("知识库检索失败", "milvus_utils", e)


if __name__ == "__main__":
    client = get_milvus_client()
    collection = get_config().kb_collection
    print("集合：", collection, " 记录数：", client.get_collection_stats(collection)["row_count"])
