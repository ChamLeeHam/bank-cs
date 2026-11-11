# reranker 模型（参考课程 03-rag/utils/bge_rerank_utils.py）
# 直接用 transformers 加载 bge-reranker-base，不需要额外安装 FlagEmbedding
import logging

import torch
from transformers import AutoModelForSequenceClassification, AutoTokenizer

from src.schema.config import get_config

logger = logging.getLogger(__name__)

_reranker_model = None
_reranker_tokenizer = None
device = "cuda" if torch.cuda.is_available() else "cpu"


def get_reranker_model():
    """获取 Reranker 模型和分词器（单例模式）"""
    global _reranker_model, _reranker_tokenizer
    if _reranker_model is None:
        model_path = get_config().reranker_model
        logger.info(f"正在初始化 Reranker 模型，路径：{model_path}，设备：{device}")
        dtype = torch.float16 if device == "cuda" else torch.float32
        _reranker_tokenizer = AutoTokenizer.from_pretrained(model_path)
        _reranker_model = AutoModelForSequenceClassification.from_pretrained(model_path, dtype=dtype).to(device).eval()
        logger.info("Reranker 模型初始化成功！")
    return _reranker_model, _reranker_tokenizer


def compute_score(pairs: list[list[str]]) -> list[float]:
    """对 [问题, 文本] 逐对打分；模型输出经过 sigmoid 归一化到 0–1（课程用的是未归一化的分数）"""
    model, tokenizer = get_reranker_model()
    batch = tokenizer(pairs, padding=True, truncation=True, max_length=512, return_tensors="pt").to(device)
    with torch.no_grad():
        logits = model(**batch).logits.view(-1).float()
    return torch.sigmoid(logits).cpu().tolist()


if __name__ == "__main__":
    query = "Can I open an account if I'm 15?"
    documents = ["开户条件\nSingapore citizens ... aged 16 or above can open a savings account. Under 16, a parent must open a joint account.",
                 "销户\nYou can close a savings account in the app under Settings > Close account."]
    print(compute_score([[query, doc] for doc in documents]))
