# 用 reranker 对检索到的块重新排序，再用断崖算法决定留几个
import logging
import re

from src.schema.config import get_config
from src.utils.bge_rerank_utils import compute_score

logger = logging.getLogger(__name__)
_CJK = re.compile(r"[一-鿿]")


def process(question, chunks):
    # 1. 精排
    sorted_chunks = _reranker(question, chunks)
    # 2. 断崖截断
    topk_chunks = _cliff_cutoff(sorted_chunks)
    logger.info(f"精排后保留 {len(topk_chunks)} 个")
    return {"reranker_chunks": topk_chunks, "sorted_chunks": sorted_chunks}


def _cliff_cutoff(chunks):
    """相邻两个分数差距突然变大的地方截断，后面的不要"""
    config = get_config()
    min_topk = min(config.rerank_min_topk, len(chunks))
    max_topk = min(config.rerank_max_topk, len(chunks))

    top_k = max_topk
    for i in range(min_topk - 1, max_topk - 1):
        s1 = chunks[i]["rerank_score"]
        s2 = chunks[i + 1]["rerank_score"]
        if s1 is None or s2 is None:
            continue
        gap = abs(s1 - s2)
        ratio = gap / (abs(s1) + 1e-6)
        if gap >= config.rerank_gap_abs or ratio >= config.rerank_ratio:
            top_k = i + 1
            break
    return chunks[:top_k]


def _reranker(question, chunks):
    if not chunks:
        return []
    try:
        pairs = [[question, _same_language_text(question, item["text"])] for item in chunks]
        scores = compute_score(pairs)
        result = []
        for chunk, score in zip(chunks, scores):
            chunk = dict(chunk)
            chunk["rerank_score"] = round(score, 4)
            result.append(chunk)
        result.sort(key=lambda x: x["rerank_score"], reverse=True)
        return result
    except Exception as e:
        # 精排失败就按原来的顺序，不要把检索结果丢掉
        logger.error(f"精排失败：{e}")
        return [dict(chunk, rerank_score=None) for chunk in chunks]


def _same_language_text(question, chunk_text):
    """
    每个章节都是 标题 + 中文段 + 英文段，整段一起打分时英文问题的分数会偏低，
    所以只取和问题同一种语言的那段（加上标题）
    """
    title, _, body = chunk_text.partition("\n")
    paras = [p.strip() for p in body.split("\n\n") if p.strip()]
    want_zh = bool(_CJK.search(question))
    same = [p for p in paras if bool(_CJK.search(p)) == want_zh]
    return title + "\n" + "\n".join(same or paras)


if __name__ == "__main__":
    from src.utils import search_embedding_utils

    question = "ATM 把我的卡吃掉了"
    candidates = search_embedding_utils.process(question)["embedding_chunks"]
    result = process(question, candidates)
    print([(c["section"].split(" / ")[0], c["rerank_score"]) for c in result["sorted_chunks"][:4]])
    print("保留：", [c["section"].split(" / ")[0] for c in result["reranker_chunks"]])
