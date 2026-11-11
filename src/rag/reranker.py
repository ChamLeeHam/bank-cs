"""
精排（参考课程 03-rag/reranker.py）：bge-reranker-base 对"问题-章节"逐对打分，再用断崖算法决定保留几个

- 向量检索把问题和章节分别编码再比较；reranker 把问题和章节放在一起读，判断更准，但只适合对少量候选重新排序
- 分数：模型输出经过 sigmoid 归一化到 0–1（课程用的是未归一化的分数）
- 断崖截断：相邻两个分数的差距（绝对值或相对值）突然变大时，后面的章节不再保留
- 修正了课程代码的 bug：精排出错时课程返回空列表（所有检索结果丢失），这里返回原来的顺序
- 直接用 transformers 加载，不需要额外安装 FlagEmbedding
"""
import logging
import re
import threading

from src.schema.config import get_config

logger = logging.getLogger("bank.rag.reranker")


class BgeReranker:
    _instance = None
    _lock = threading.Lock()

    def __init__(self):
        import torch
        from transformers import AutoModelForSequenceClassification, AutoTokenizer

        path = get_config().reranker_model
        self.torch = torch
        self.device = "cuda" if torch.cuda.is_available() else "cpu"
        dtype = torch.float16 if self.device == "cuda" else torch.float32
        self.tokenizer = AutoTokenizer.from_pretrained(path)
        self.model = AutoModelForSequenceClassification.from_pretrained(path, dtype=dtype).to(self.device).eval()
        logger.info("bge-reranker-base 已加载，设备 %s", self.device)

    @classmethod
    def get(cls) -> "BgeReranker":
        with cls._lock:
            if cls._instance is None:
                cls._instance = cls()
            return cls._instance

    def score(self, query: str, texts: list[str]) -> list[float]:
        torch = self.torch
        batch = self.tokenizer([[query, t] for t in texts], padding=True, truncation=True, max_length=512,
                               return_tensors="pt").to(self.device)
        with torch.no_grad():
            logits = self.model(**batch).logits.view(-1).float()
        return torch.sigmoid(logits).cpu().tolist()


_CJK = re.compile(r"[一-鿿]")


def same_language_text(query: str, chunk_text: str) -> str:
    """
    知识库每个章节是"标题 + 中文段落 + 英文段落"。中英两段一起送进 reranker 时，模型对英文问题打分明显偏低
    （"Can I open an account if I'm 15?" 对"开户条件"只有 0.05），所以只用与问题语言相同的那一段（加标题）打分
    """
    title, _, body = chunk_text.partition("\n")
    paras = [p.strip() for p in body.split("\n\n") if p.strip()]
    want_zh = bool(_CJK.search(query))
    same = [p for p in paras if bool(_CJK.search(p)) == want_zh]
    return f"{title}\n" + "\n".join(same or paras)


def rerank(query: str, chunks: list[dict]) -> list[dict]:
    """返回按 rerank_score 从高到低排序的块；出错时保持原顺序（rerank_score 为 None）"""
    if not chunks:
        return []
    try:
        scores = BgeReranker.get().score(query, [same_language_text(query, c["text"]) for c in chunks])
    except Exception as e:
        logger.error("精排失败，保留原检索顺序：%s", e)
        return [{**c, "rerank_score": None} for c in chunks]
    scored = [{**c, "rerank_score": round(s, 4)} for c, s in zip(chunks, scores)]
    return sorted(scored, key=lambda c: c["rerank_score"], reverse=True)


def cliff_cutoff(chunks: list[dict]) -> list[dict]:
    """断崖算法：在 [min_topk, max_topk] 之间，找到第一个分数骤降的位置并截断"""
    config = get_config()
    min_k = min(config.rerank_min_topk, len(chunks))
    max_k = min(config.rerank_max_topk, len(chunks))
    for i in range(min_k - 1, max_k - 1):
        s1, s2 = chunks[i]["rerank_score"], chunks[i + 1]["rerank_score"]
        if s1 is None or s2 is None:
            continue
        gap = s1 - s2
        if gap >= config.rerank_gap_abs or gap / (abs(s1) + 1e-6) >= config.rerank_ratio:
            return chunks[:i + 1]
    return chunks[:max_k]
