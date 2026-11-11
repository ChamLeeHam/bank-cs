# 加载 bge-reranker-base 模型，给 [问题, 文本] 打分
import logging

import torch
from transformers import AutoModelForSequenceClassification, AutoTokenizer

from src.schema.config import get_config

logger = logging.getLogger(__name__)

_reranker_model = None
_reranker_tokenizer = None
device = "cuda" if torch.cuda.is_available() else "cpu"


def get_reranker_model():
    """获取 reranker 模型和分词器，只加载一次"""
    global _reranker_model, _reranker_tokenizer
    if _reranker_model is None:
        model_path = get_config().reranker_model
        logger.info(f"加载 reranker 模型：{model_path}，设备：{device}")
        dtype = torch.float16 if device == "cuda" else torch.float32
        _reranker_tokenizer = AutoTokenizer.from_pretrained(model_path)
        _reranker_model = AutoModelForSequenceClassification.from_pretrained(model_path, dtype=dtype)
        _reranker_model = _reranker_model.to(device).eval()
    return _reranker_model, _reranker_tokenizer


def compute_score(pairs):
    """pairs: [[问题, 文本], ...]，返回 0~1 的分数"""
    model, tokenizer = get_reranker_model()
    batch = tokenizer(pairs, padding=True, truncation=True, max_length=512, return_tensors="pt").to(device)
    with torch.no_grad():
        logits = model(**batch).logits.view(-1).float()
    return torch.sigmoid(logits).cpu().tolist()


if __name__ == "__main__":
    query = "Can I open an account if I'm 15?"
    documents = [
        "开户条件\nAged 16 or above can open a savings account. Under 16, a parent must open a joint account.",
        "销户\nYou can close a savings account in the app under Settings > Close account.",
    ]
    print(compute_score([[query, doc] for doc in documents]))
