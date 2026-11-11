"""
知识库存储与检索：Milvus 2.6 混合检索（《需求文档》F2）

一个集合，两路检索：
  - dense：BGE-M3 稠密向量（语义相似，跨语言，"汇款手续费" 能找到 "TT fee"）
  - sparse：Milvus 内置 BM25（关键词精确匹配，如 "SWIFT"、"PIN"、"GIRO"），由 text 字段自动生成，不需要自己算
两路结果用 RRF（Reciprocal Rank Fusion）融合：只看排名，不需要把两种分数换算到同一尺度

text 字段使用 jieba 分词 + 小写化，中英文混杂的问题也能正确切词（"我想 check 一下 TT 手续费"）。
"""
from pymilvus import AnnSearchRequest, DataType, Function, FunctionType, MilvusClient, RRFRanker

from src.rag.embedder import EMBED_DIM
from src.schema.config import get_config
from src.schema.exceptions import MilvusError

ANALYZER = {"tokenizer": "jieba", "filter": ["cnalphanumonly", "lowercase"]}
OUTPUT_FIELDS = ["doc", "title", "section", "intent", "sub_intents", "text"]
RRF_K = 60


def get_client() -> MilvusClient:
    config = get_config()
    try:
        return MilvusClient(uri=config.milvus_uri, token=config.milvus_token or None)
    except Exception as e:
        raise MilvusError(f"无法连接 Milvus：{config.milvus_uri}", "kb_store", e)


def create_collection(client: MilvusClient, name: str) -> None:
    """（重新）创建知识库集合；只删除本项目自己的集合"""
    if client.has_collection(name):
        client.drop_collection(name)

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
    schema.add_function(Function(name="text_bm25", function_type=FunctionType.BM25,
                                 input_field_names=["text"], output_field_names=["sparse"]))

    index = client.prepare_index_params()
    index.add_index("dense", index_type="AUTOINDEX", metric_type="IP")  # 向量已归一化：内积 = 余弦
    index.add_index("sparse", index_type="SPARSE_INVERTED_INDEX", metric_type="BM25")
    client.create_collection(name, schema=schema, index_params=index)


def hybrid_search(client: MilvusClient, query: str, query_vector: list[float], top_k: int,
                  intent: str | None = None) -> list[dict]:
    """稠密 + BM25 两路检索，RRF 融合；intent 不为空时只在该意图的文档中检索"""
    expr = f'intent == "{intent}"' if intent else ""
    candidates = top_k * 3  # 每一路多取一些候选，融合后再截取 top_k
    reqs = [
        AnnSearchRequest(data=[query_vector], anns_field="dense", param={"metric_type": "IP"},
                         limit=candidates, expr=expr),
        AnnSearchRequest(data=[query], anns_field="sparse", param={"metric_type": "BM25"},
                         limit=candidates, expr=expr),
    ]
    try:
        hits = client.hybrid_search(get_config().kb_collection, reqs, ranker=RRFRanker(RRF_K), limit=top_k,
                                    output_fields=OUTPUT_FIELDS)[0]
    except Exception as e:
        raise MilvusError("知识库检索失败", "kb_store", e)
    return [{**hit["entity"], "id": hit["id"], "score": round(hit["distance"], 4)} for hit in hits]
