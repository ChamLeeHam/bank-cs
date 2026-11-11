# 对块使用 reranker 模型进行精排（参考课程 03-rag/reranker.py）
# 1. reranker 把问题和章节放在一起读，比向量检索判断更准，但只适合对少量候选重新排序
# 2. 断崖算法：相邻两个分数的差距（绝对值或相对值）突然变大时，后面的章节不再保留
# 3. 最高分低于 BANK_RERANK_MIN_SCORE 时视为知识库中没有答案（由回答节点处理）
import logging
import re

from src.schema.config import get_config
from src.utils.bge_rerank_utils import compute_score

logger = logging.getLogger(__name__)
_CJK = re.compile(r"[一-鿿]")


def process(question, chunks):
    # 1. reranker 模型进行精排
    sorted_chunks = _reranker(question, chunks)
    # 2. 通过断崖算法实现 TopK
    topk_chunks = _cliff_cutoff(sorted_chunks)
    logger.info(f"精排后保留 {len(topk_chunks)} 个章节")
    return {"reranker_chunks": topk_chunks, "sorted_chunks": sorted_chunks}


def _cliff_cutoff(chunks):
    config = get_config()
    min_topk = min(config.rerank_min_topk, len(chunks))
    max_topk = min(config.rerank_max_topk, len(chunks))

    # 实现断崖算法
    top_k = max_topk
    for i in range(min_topk - 1, max_topk - 1):
        s1 = chunks[i]["rerank_score"]
        s2 = chunks[i + 1]["rerank_score"]
        if s1 is None or s2 is None:
            continue
        gap = abs(s1 - s2)  # 绝对值
        ratio = gap / (abs(s1) + 1e-6)  # 相对值
        if gap >= config.rerank_gap_abs or ratio >= config.rerank_ratio:
            top_k = i + 1
            logger.info(f"断崖位置：{top_k}")
            break
    return chunks[:top_k]


def _reranker(question, chunks):
    if not chunks:
        return []
    try:
        pairs = [[question, _same_language_text(question, item["text"])] for item in chunks]
        scores = compute_score(pairs)
        # 给每个块添加 rerank_score 属性，按分数从大到小排序
        scored = [{**chunk, "rerank_score": round(scores[i], 4)} for i, chunk in enumerate(chunks)]
        scored.sort(key=lambda x: x["rerank_score"], reverse=True)
        return scored
    except Exception as e:
        # 修正课程代码的 bug：课程这里返回 []（所有检索结果丢失），这里保留原检索顺序
        logger.error(f"Error in reranking: {e}")
        return [{**chunk, "rerank_score": None} for chunk in chunks]


def _same_language_text(question: str, chunk_text: str) -> str:
    """
    知识库每个章节是"标题 + 中文段落 + 英文段落"。中英两段一起送进 reranker 时，模型对英文问题打分明显偏低
    （"Can I open an account if I'm 15?" 对"开户条件"只有 0.05），所以只用与问题语言相同的那一段（加标题）打分
    """
    title, _, body = chunk_text.partition("\n")
    paras = [p.strip() for p in body.split("\n\n") if p.strip()]
    want_zh = bool(_CJK.search(question))
    same = [p for p in paras if bool(_CJK.search(p)) == want_zh]
    return f"{title}\n" + "\n".join(same or paras)


if __name__ == "__main__":
    from src.utils import search_embedding_utils

    question = "ATM 把我的卡吃掉了"
    candidates = search_embedding_utils.process(question)["embedding_chunks"]
    result = process(question, candidates)
    print([(c["section"].split(" / ")[0], c["rerank_score"]) for c in result["sorted_chunks"][:4]])
    print("保留：", [c["section"].split(" / ")[0] for c in result["reranker_chunks"]])
