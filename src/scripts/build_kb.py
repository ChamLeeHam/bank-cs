"""
构建知识库：data/kb/*.md -> 按章节切块 -> BGE-M3 向量 -> Milvus 集合（默认 lioncity_faq）

切块规则：每个 "## " 章节是一个块（中文 + 英文两段放在同一个块里，任何语言的问题都能匹配到）
  - 章节标题就是引用来源，回答时显示"来源：《费用与汇率》海外电汇（TT）手续费"
  - <!-- sub_intents: a, b --> 标注该章节对应的二级意图（必须是 src/intents.py 中存在的标签）
  - 文件开头的 front matter 写明文档标题和一级意图

运行（在项目根目录）：
    uv run python src/scripts/build_kb.py          # 重建集合并做检索自测
    uv run python src/scripts/build_kb.py --check  # 只解析和校验文档，不写入 Milvus
"""
import argparse
import re
import sys
import time
from pathlib import Path

PROJECT_ROOT = Path(__file__).parent.parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from src.intents import INTENTS, sub_intents_of
from src.schema.config import get_config

config = get_config()
FRONT_MATTER = re.compile(r"^---\s*\n(.*?)\n---\s*\n", re.DOTALL)
SUB_INTENTS = re.compile(r"<!--\s*sub_intents:\s*(.*?)\s*-->")
COMMENT = re.compile(r"<!--.*?-->", re.DOTALL)
ALL_SUBS = {s for intent in INTENTS for s in sub_intents_of(intent)}


def parse_doc(path: Path) -> list[dict]:
    raw = path.read_text(encoding="utf-8")
    m = FRONT_MATTER.match(raw)
    if not m:
        raise ValueError(f"{path.name}: 缺少 front matter（title / intent）")
    meta = dict(line.split(":", 1) for line in m.group(1).splitlines() if ":" in line)
    meta = {k.strip(): v.strip() for k, v in meta.items()}
    if meta.get("intent") not in INTENTS:
        raise ValueError(f"{path.name}: intent 不存在：{meta.get('intent')}")

    chunks = []
    for block in re.split(r"^## ", raw[m.end():], flags=re.MULTILINE)[1:]:
        section, _, body = block.partition("\n")
        subs_match = SUB_INTENTS.search(body)
        subs = [s.strip() for s in subs_match.group(1).split(",")] if subs_match else []
        unknown = [s for s in subs if s not in ALL_SUBS]
        if unknown:
            raise ValueError(f"{path.name} / {section}: 二级意图不存在 {unknown}")
        body = COMMENT.sub("", body).strip()
        chunks.append({"doc": path.stem, "title": meta["title"], "section": section.strip(), "intent": meta["intent"],
                       "sub_intents": subs,
                       # 写入向量和 BM25 的文本带上章节标题，标题里的关键词（如 "PIN"、"TT"）也能被检索到
                       "text": f"{section.strip()}\n{body}"})
    return chunks


def main() -> None:
    parser = argparse.ArgumentParser(description="构建知识库")
    parser.add_argument("--check", action="store_true", help="只解析和校验文档")
    args = parser.parse_args()

    docs = [p for p in sorted(config.kb_dir.glob("*.md")) if p.name.lower() != "readme.md"]  # README 是说明文档，不入库
    chunks = [c for path in docs for c in parse_doc(path)]
    for doc in sorted({c["doc"] for c in chunks}):
        n = sum(c["doc"] == doc for c in chunks)
        print(f"  {doc:<18} {n} 个章节")
    print(f"共 {len(chunks)} 个块，最长 {max(len(c['text']) for c in chunks)} 字符")
    if args.check:
        return

    from src.utils.bge_m3_embedding_utils import generate_dense_embedding
    from src.utils.milvus_utils import create_kb_collection, get_milvus_client, hybrid_search

    start = time.perf_counter()
    vectors = generate_dense_embedding([c["text"] for c in chunks])
    print(f"向量化完成：{len(vectors)} 条，用时 {time.perf_counter() - start:.1f}s")

    client = get_milvus_client()
    create_kb_collection(client, config.kb_collection)
    client.insert(config.kb_collection, [{**c, "dense": v} for c, v in zip(chunks, vectors)])
    client.flush(config.kb_collection)
    print(f"已写入 Milvus 集合 {config.kb_collection}：{client.get_collection_stats(config.kb_collection)['row_count']} 条")

    # 检索自测：中文 / 英文 / 中英混杂
    print("\n检索自测（top 2）：")
    for q in ["How much is the overseas TT fee?", "汇款到中国要几天？", "我的 PIN 被 lock 了怎么办",
              "我要怎么更新地址？", "ATM 吞了我的卡", "can I open account if I'm 15"]:
        hits = hybrid_search(client, q, generate_dense_embedding([q])[0], top_k=2)
        print(f"  {q}")
        for h in hits:
            print(f"      {h['score']:.4f}  《{h['title'].split(' / ')[0]}》{h['section']}")


if __name__ == "__main__":
    main()
