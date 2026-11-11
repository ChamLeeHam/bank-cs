# 针对 Milvus 向量数据库的操作（参考课程 03-rag/utils/milvus_utils.py）
# 一个集合，两路检索：
#   - dense：bge-m3 稠密向量（语义相似，跨语言）
#   - sparse：Milvus 内置 BM25（关键词精确匹配，如 SWIFT、PIN、GIRO），由 text 字段自动生成
# 两路结果用 RRF 融合；text 字段使用 jieba 分词 + 小写化，中英文混杂的问题也能正确切词
import logging
from typing import Optional

from pymilvus import AnnSearchRequest, DataType, Function, FunctionType, MilvusClient, RRFRanker

from src.schema.config import get_config
from src.schema.exceptions import MilvusError

logger = logging.getLogger(__name__)

EMBED_DIM = 1024
ANALYZER = {"tokenizer": "jieba", "filter": ["cnalphanumonly", "lowercase"]}
OUTPUT_FIELDS = ["doc", "title", "section", "intent", "sub_intents", "text"]

milvus_client = None


# 1. 获取向量数据库客户端
def get_milvus_client() -> Optional[MilvusClient]:
    global milvus_client

    # 1. 判断
    if milvus_client is not None:
        return milvus_client

    # 2. 获取参数
    config = get_config()
    try:
        # 3. 定义 MilvusClient 对象
        milvus_client = MilvusClient(uri=config.milvus_uri, token=config.milvus_token or None)
        return milvus_client
    except Exception as e:
        logger.error(f"Milvus客户端创建失败：{str(e)}")
        raise MilvusError(f"无法连接 Milvus：{config.milvus_uri}", "milvus_utils", e)


# 2. 创建知识库集合（重新创建；只删除本项目自己的集合）
def create_kb_collection(client: MilvusClient, collection_name: str) -> None:
    if client.has_collection(collection_name):
        client.drop_collection(collection_name)

    # 1. 定义字段
    schema = client.create_schema(auto_id=True, enable_dynamic_field=False)
    schema.add_field("id", DataType.INT64, is_primary=True)
    schema.add_field("doc", DataType.VARCHAR, max_length=64)  # 文件名，如 fees_rates
    schema.add_field("title", DataType.VARCHAR, max_length=200)  # 文档标题
    schema.add_field("section", DataType.VARCHAR, max_length=200)  # 章节标题（引用来源）
    schema.add_field("intent", DataType.VARCHAR, max_length=50)  # 一级意图，检索时可按意图过滤
    schema.add_field("sub_intents", DataType.ARRAY, element_type=DataType.VARCHAR, max_capacity=16, max_length=60)
    schema.add_field("text", DataType.VARCHAR, max_length=8192, enable_analyzer=True, analyzer_params=ANALYZER)
    schema.add_field("dense", DataType.FLOAT_VECTOR, dim=EMBED_DIM)
    schema.add_field("sparse", DataType.SPARSE_FLOAT_VECTOR)

    # 2. BM25 函数：text 字段自动生成 sparse 向量
    schema.add_function(Function(name="text_bm25", function_type=FunctionType.BM25,
                                 input_field_names=["text"], output_field_names=["sparse"]))

    # 3. 索引
    index_params = client.prepare_index_params()
    index_params.add_index("dense", index_type="AUTOINDEX", metric_type="IP")  # 向量已归一化：内积 = 余弦
    index_params.add_index("sparse", index_type="SPARSE_INVERTED_INDEX", metric_type="BM25")
    client.create_collection(collection_name, schema=schema, index_params=index_params)


# 3. 创建混合检索：稠密请求和 BM25 请求
def create_hybrid_search_request(dense_vector, query_text, expr=None, limit=10):
    # 1. 构建两路搜索请求
    dense_req = AnnSearchRequest(
        data=[dense_vector],
        anns_field="dense",
        param={"metric_type": "IP"},
        expr=expr,
        limit=limit,
    )

    # BM25 直接传原文，Milvus 内部分词（课程中这里传的是 bge-m3 的稀疏向量）
    sparse_req = AnnSearchRequest(
        data=[query_text],
        anns_field="sparse",
        param={"metric_type": "BM25"},
        expr=expr,
        limit=limit,
    )
    return [dense_req, sparse_req]


# 4. 执行混合检索
def execute_hybrid_search_request(client, collection_name, requests, rrf_k=60, output_fields=None, limit=10):
    """执行混合检索：两路结果按排名用 RRF 融合（两种分数的尺度不同，所以不用加权求和）"""
    if output_fields is None:
        output_fields = OUTPUT_FIELDS
    try:
        results = client.hybrid_search(
            collection_name=collection_name,
            reqs=requests,
            ranker=RRFRanker(rrf_k),
            output_fields=output_fields,
            limit=limit,
        )
        return results
    except Exception as e:
        logger.error(f"混合检索失败：{str(e)}")
        raise MilvusError("知识库检索失败", "milvus_utils", e)


if __name__ == "__main__":
    client = get_milvus_client()
    collection = get_config().kb_collection
    print("集合：", collection, "  记录数：", client.get_collection_stats(collection)["row_count"])
