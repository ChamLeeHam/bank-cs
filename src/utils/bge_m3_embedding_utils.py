# 用本地的 bge-m3 模型生成混合向量：稠密向量 + 稀疏向量
# 和课程 03-rag 的 generate_hybrid_embedding 一样，返回 {"dense": [...], "sparse": [...]}
# 课程用的是 pymilvus 的 BGEM3EmbeddingFunction（里面是 FlagEmbedding），它和 transformers 5 不兼容，
# 所以这里用 transformers 自己算，算法和 FlagEmbedding 一样：
#   稠密向量：[CLS] 位置的向量，归一化
#   稀疏向量：每个 token 的向量经过 sparse_linear 再 relu，得到这个词的权重；同一个词出现多次取最大的
import logging

import torch
from transformers import AutoModel, AutoTokenizer

from src.schema.config import get_config
from src.schema.exceptions import EmbeddingError

logger = logging.getLogger(__name__)

EMBED_DIM = 1024
bge_m3_model = None
bge_m3_tokenizer = None
bge_m3_sparse_linear = None
device = "cuda" if torch.cuda.is_available() else "cpu"


def get_bge_m3_embedding_model():
    """获取 bge-m3 模型、分词器和稀疏向量的那一层，只加载一次"""
    global bge_m3_model, bge_m3_tokenizer, bge_m3_sparse_linear
    if bge_m3_model is None:
        path = get_config().embed_model
        sparse_path = path / "sparse_linear.pt"
        if not path.exists() or not sparse_path.exists():
            raise EmbeddingError(f"找不到 bge-m3 模型或 sparse_linear.pt：{path}", "bge_m3_embedding_utils")
        dtype = torch.float16 if device == "cuda" else torch.float32
        bge_m3_tokenizer = AutoTokenizer.from_pretrained(path)
        bge_m3_model = AutoModel.from_pretrained(path, dtype=dtype).to(device).eval()
        # sparse_linear 是一个 1024 -> 1 的线性层，给每个 token 打一个权重
        bge_m3_sparse_linear = torch.nn.Linear(EMBED_DIM, 1)
        bge_m3_sparse_linear.load_state_dict(torch.load(sparse_path, map_location="cpu"))
        bge_m3_sparse_linear = bge_m3_sparse_linear.to(device).eval()
        logger.info(f"bge-m3 模型加载完成，设备：{device}")
    return bge_m3_model, bge_m3_tokenizer, bge_m3_sparse_linear


def generate_hybrid_embedding(docs, batch_size=16, max_length=1024):
    """生成混合向量，返回 {"dense": [向量, ...], "sparse": [{token_id: 权重}, ...]}"""
    model, tokenizer, sparse_linear = get_bge_m3_embedding_model()
    # [CLS]、[SEP]、padding 这些特殊符号不算进稀疏向量
    skip_ids = {tokenizer.cls_token_id, tokenizer.eos_token_id, tokenizer.pad_token_id, tokenizer.unk_token_id}

    dense_list, sparse_list = [], []
    for i in range(0, len(docs), batch_size):
        # 1. 分词
        batch = tokenizer(docs[i:i + batch_size], padding=True, truncation=True, max_length=max_length,
                          return_tensors="pt").to(device)
        with torch.no_grad():
            hidden = model(**batch).last_hidden_state.float()
            # 2. 稠密向量：取 [CLS] 位置，再归一化（这样内积就是余弦相似度）
            dense = torch.nn.functional.normalize(hidden[:, 0], dim=-1)
            # 3. 稀疏向量：每个 token 一个权重
            weights = torch.relu(sparse_linear(hidden)).squeeze(-1)
        dense_list += dense.cpu().tolist()

        # 4. 转成 {token_id: 权重}，同一个词出现多次取最大的权重
        for ids, ws in zip(batch["input_ids"].tolist(), weights.cpu().tolist()):
            sparse = {}
            for token_id, w in zip(ids, ws):
                if token_id in skip_ids or w <= 0:
                    continue
                sparse[token_id] = max(w, sparse.get(token_id, 0.0))
            sparse_list.append(sparse)

    return {"dense": dense_list, "sparse": sparse_list}


if __name__ == "__main__":
    result = generate_hybrid_embedding(["海外电汇手续费多少？", "How much is the overseas TT fee?"])
    a, b = result["dense"]
    print("稠密向量维度：", len(a), " 两个问题的相似度：", round(sum(x * y for x, y in zip(a, b)), 4))

    # 看一下稀疏向量里权重最大的几个词
    _, tokenizer, _ = get_bge_m3_embedding_model()
    for sparse in result["sparse"]:
        top = sorted(sparse.items(), key=lambda x: x[1], reverse=True)[:6]
        print("稀疏向量：", len(sparse), "个词，权重最大的：",
              [(tokenizer.convert_ids_to_tokens(t), round(w, 3)) for t, w in top])
