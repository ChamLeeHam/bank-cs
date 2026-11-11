# 用本地的 bge-m3 模型生成稠密向量
# 关键词检索用的是 Milvus 自带的 BM25，所以这里不需要 bge-m3 的稀疏向量
import logging

import torch
from transformers import AutoModel, AutoTokenizer

from src.schema.config import get_config
from src.schema.exceptions import EmbeddingError

logger = logging.getLogger(__name__)

EMBED_DIM = 1024
bge_m3_model = None
bge_m3_tokenizer = None
device = "cuda" if torch.cuda.is_available() else "cpu"


def get_bge_m3_embedding_model():
    """获取 bge-m3 模型和分词器，只加载一次"""
    global bge_m3_model, bge_m3_tokenizer
    if bge_m3_model is None:
        path = get_config().embed_model
        if not path.exists():
            raise EmbeddingError(f"找不到 bge-m3 模型：{path}", "bge_m3_embedding_utils")
        dtype = torch.float16 if device == "cuda" else torch.float32
        bge_m3_tokenizer = AutoTokenizer.from_pretrained(path)
        bge_m3_model = AutoModel.from_pretrained(path, dtype=dtype).to(device).eval()
        logger.info(f"bge-m3 模型加载完成，设备：{device}")
    return bge_m3_model, bge_m3_tokenizer


def generate_dense_embedding(docs, batch_size=16, max_length=512):
    """生成稠密向量，返回 {"dense": [向量, ...]}"""
    model, tokenizer = get_bge_m3_embedding_model()

    dense_list = []
    for i in range(0, len(docs), batch_size):
        # 1. 分词
        batch = tokenizer(docs[i:i + batch_size], padding=True, truncation=True, max_length=max_length,
                          return_tensors="pt").to(device)
        # 2. 取 [CLS] 位置的向量，再归一化（这样内积就是余弦相似度）
        with torch.no_grad():
            cls = model(**batch).last_hidden_state[:, 0]
            cls = torch.nn.functional.normalize(cls.float(), dim=-1)
        dense_list += cls.cpu().tolist()

    return {"dense": dense_list}


if __name__ == "__main__":
    result = generate_dense_embedding(["海外电汇手续费多少？", "How much is the overseas TT fee?"])
    a, b = result["dense"]
    print("维度：", len(a), " 两个问题的相似度：", round(sum(x * y for x, y in zip(a, b)), 4))
