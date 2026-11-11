# Milvus 向量数据库的操作：建知识库集合、混合检索（稠密向量 + BM25）
import logging

from pymilvus import AnnSearchRequest, DataType, Function, FunctionType, MilvusClient, RRFRanker

from src.schema.config import get_config
from src.schema.exceptions import MilvusError

logger = logging.getLogger(__name__)

EMBED_DIM = 1024
# jieba 分词再转小写，中英混杂的问题也能切词
ANALYZER = {"tokenizer": "jieba", "filter": ["cnalphanumonly", "lowercase"]}
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
    schema.add_field("text", DataType.VARCHAR, max_length=8192, enable_analyzer=True, analyzer_params=ANALYZER)
    schema.add_field("dense", DataType.FLOAT_VECTOR, dim=EMBED_DIM)
    schema.add_field("sparse", DataType.SPARSE_FLOAT_VECTOR)

    # BM25：Milvus 根据 text 字段自动生成 sparse 向量
    schema.add_function(Function(name="text_bm25", function_type=FunctionType.BM25,
                                 input_field_names=["text"], output_field_names=["sparse"]))

    # 索引
    index_params = client.prepare_index_params()
    index_params.add_index("dense", index_type="AUTOINDEX", metric_type="IP")
    index_params.add_index("sparse", index_type="SPARSE_INVERTED_INDEX", metric_type="BM25")
    client.create_collection(collection_name, schema=schema, index_params=index_params)


# 3. 混合检索的两个请求：稠密向量 + BM25
def create_hybrid_search_request(dense_vector, query_text, expr=None, limit=10):
    dense_req = AnnSearchRequest(
        data=[dense_vector],
        anns_field="dense",
        param={"metric_type": "IP"},
        expr=expr,
        limit=limit,
    )
    # BM25 直接传问题原文
    sparse_req = AnnSearchRequest(
        data=[query_text],
        anns_field="sparse",
        param={"metric_type": "BM25"},
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
