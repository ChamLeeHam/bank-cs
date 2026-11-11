# 用本地的 bge-m3 模型生成混合向量：稠密向量 + 稀疏向量，写法和课程 03-rag 一样
# 在项目根目录运行测试：uv run python -m src.utils.bge_m3_embedding_utils
# 注意：模型目录里要同时有 sparse_linear.pt 和 colbert_linear.pt，
# 少了一个的话 FlagEmbedding 不会报错，但是稀疏向量那一层会是随机的，检索结果就不对了
import logging
from typing import List

import torch
from pymilvus.model.hybrid import BGEM3EmbeddingFunction

from src.schema.config import get_config
from src.schema.exceptions import EmbeddingError

logger = logging.getLogger(__name__)

bge_m3_model = None


# 1. 获取 bge-m3 模型，只加载一次
def get_bge_m3_embedding_model():
    global bge_m3_model
    if bge_m3_model is None:
        path = get_config().embed_model
        for name in ["sparse_linear.pt", "colbert_linear.pt"]:
            if not (path / name).exists():
                raise EmbeddingError(f"bge-m3 模型目录里缺少 {name}：{path}", "bge_m3_embedding_utils")
        use_gpu = torch.cuda.is_available()
        bge_m3_model = BGEM3EmbeddingFunction(
            model_name=str(path),
            device="cuda:0" if use_gpu else "cpu",
            use_fp16=use_gpu,
        )
        logger.info("bge-m3 模型加载完成")
    return bge_m3_model


# 2. 生成混合向量，返回 {"dense": [向量, ...], "sparse": [{token_id: 权重}, ...]}
def generate_hybrid_embedding(docs: List):
    model = get_bge_m3_embedding_model()
    embedding_result = model.encode_documents(docs)

    # 稀疏向量返回的是 CSR 格式（所有文档放在一个矩阵里），转成每个文档一个 {token_id: 权重}
    sparse_list = []
    csr_sparse = embedding_result["sparse"]
    indices = csr_sparse.indices
    data = csr_sparse.data
    indptr = csr_sparse.indptr
    for i in range(len(docs)):
        # 第 i 个文档的数据在 indptr[i] 到 indptr[i+1] 之间
        start_idx = indptr[i]
        end_idx = indptr[i + 1]
        token_id = indices[start_idx:end_idx].tolist()
        weights = data[start_idx:end_idx].tolist()
        sparse_list.append(dict(zip(token_id, weights)))

    return {
        "dense": [dense.tolist() for dense in embedding_result["dense"]],
        "sparse": sparse_list,
    }


if __name__ == "__main__":
    result = generate_hybrid_embedding(["海外电汇手续费多少？", "How much is the overseas TT fee?"])
    a, b = result["dense"]
    print("稠密向量维度：", len(a), " 两个问题的相似度：", round(sum(x * y for x, y in zip(a, b)), 4))

    # 看一下稀疏向量里权重最大的几个词
    tokenizer = get_bge_m3_embedding_model().model.tokenizer
    for sparse in result["sparse"]:
        top = sorted(sparse.items(), key=lambda x: x[1], reverse=True)[:6]
        print("稀疏向量：", len(sparse), "个词，权重最大的：",
              [(tokenizer.convert_ids_to_tokens(t), round(w, 3)) for t, w in top])
