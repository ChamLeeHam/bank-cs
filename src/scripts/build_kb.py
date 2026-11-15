"""
建知识库：读 data/kb/ 里的文档，按章节切块，用 bge-m3 转成向量，存进 Milvus

每个 "## " 章节是一块，中文和英文两段放在同一块里
- 章节标题就是回答里显示的来源
- <!-- sub_intents: a, b --> 写这个章节对应哪些二级意图
- 文件开头写文档标题和一级意图

运行：
    uv run python src/scripts/build_kb.py          # 重建集合，再测一下检索
    uv run python src/scripts/build_kb.py --check  # 只检查文档格式
    uv run python src/scripts/build_kb.py --pdf    # 建 PDF 的集合（先在终端用 MinerU 解析 data/kb/pdf 里的 PDF）
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
                       # 存的文本带上章节标题，标题里的词（比如 PIN、TT）也能搜到
                       "text": f"{section.strip()}\n{body}"})
    return chunks


def load_pdf_chunks() -> list[dict]:
    """Load the isolated external-bank corpus: MinerU PDFs plus curated official-page summaries."""
    from src.utils import md_split_utils, mineru_utils

    docs = mineru_utils.process()
    reference_dir = PROJECT_ROOT / "data" / "research" / "bank_reference_docs"
    for path in sorted(reference_dir.glob("*.md")):
        content = path.read_text(encoding="utf-8").strip()
        if content:
            docs.append({"file_title": f"{path.stem}.md", "md_path": str(path), "content": content})

    chunks = md_split_utils.process(docs)
    # 集合的字段和 FAQ 一样；PDF 没有意图标签，intent 留空
    return [{"doc": _truncate_utf8(c["doc"], 64), "title": _truncate_utf8(c["title"], 200),
             "section": _truncate_utf8(c["section"], 200), "intent": "",
             "sub_intents": [], "text": _truncate_utf8(c["text"], 8192)} for c in chunks]


def load_unified_chunks() -> list[dict]:
    """Build the single demo collection from curated Lion City FAQ and adapted reference material."""
    faq_paths = [p for p in sorted(config.kb_dir.glob("*.md")) if p.name.lower() != "readme.md"]
    faq_chunks = [chunk for path in faq_paths for chunk in parse_doc(path)]
    for chunk in faq_chunks:
        chunk["doc"] = f"lcb__faq__{chunk['doc']}"

    reference_chunks = load_pdf_chunks()
    for chunk in reference_chunks:
        original_doc = chunk["doc"]
        chunk["doc"] = f"lcb__ref__{original_doc}"
        # Keep provenance in the stored text, while customer-facing answers use the fictional demo bank voice.
        chunk["title"] = "狮城银行演示知识库 / Lion City Bank Demo Knowledge Base"
        chunk["text"] = re.sub(
            r"^Source institution:[^\n]*\nOfficial source: ([^\n]*)\nDocument: [^\n]*\n",
            r"Lion City Bank demonstration knowledge. Adapted for this local demo; preserve documented conditions.\n"
            r"Official source: \1\nInternal reference: " + original_doc + "\n",
            chunk["text"], count=1,
        )
        chunk["doc"] = _truncate_utf8(chunk["doc"], 64)
        chunk["title"] = _truncate_utf8(chunk["title"], 200)
        chunk["text"] = _truncate_utf8(chunk["text"], 8192)
    return faq_chunks + reference_chunks


def _truncate_utf8(value: str, max_bytes: int) -> str:
    """Milvus VARCHAR lengths count UTF-8 bytes, not Python characters."""
    return value.encode("utf-8")[:max_bytes].decode("utf-8", errors="ignore")


def save_to_milvus(chunks: list[dict], collection_name: str) -> None:
    from src.utils.bge_m3_embedding_utils import generate_hybrid_embedding
    from src.utils.milvus_utils import create_kb_collection, get_milvus_client

    # 1. 向量化：bge-m3 同时生成稠密向量和稀疏向量
    start = time.perf_counter()
    embeddings = generate_hybrid_embedding([c["text"] for c in chunks])
    print(f"向量化完成：{len(chunks)} 条，用时 {time.perf_counter() - start:.1f}s")

    # 2. 重建集合并写入
    client = get_milvus_client()
    create_kb_collection(client, collection_name)
    rows = [{**c, "dense_vector": dense, "sparse_vector": sparse}
            for c, dense, sparse in zip(chunks, embeddings["dense"], embeddings["sparse"])]
    client.insert(collection_name, rows)
    client.flush(collection_name)
    print(f"已写入 Milvus 集合 {collection_name}：{client.get_collection_stats(collection_name)['row_count']} 条")


def search_test(questions: list[str], collection_name: str) -> None:
    from src.utils import search_embedding_utils
    from src.utils.reference_bank_utils import detect_reference_bank

    print("\n检索自测（top 2）：")
    for q in questions:
        bank = detect_reference_bank(q) if collection_name == config.kb_collection else None
        prefix = bank["doc_prefix"] if bank else ("lcb__faq__" if collection_name == config.kb_collection else None)
        hits = search_embedding_utils.process(q, collection_name=collection_name,
                                              doc_prefix=prefix)["embedding_chunks"][:2]
        print(f"  {q}")
        for h in hits:
            print(f"      {h['score']:.4f}  《{h['title'].split(' / ')[0]}》{h['section']}")


def main() -> None:
    parser = argparse.ArgumentParser(description="构建知识库")
    parser.add_argument("--check", action="store_true", help="只解析和校验文档")
    parser.add_argument("--pdf", action="store_true", help="仅重建旧的 PDF 参考集合（回退用途）")
    parser.add_argument("--faq-only", action="store_true", help="只重建本行 FAQ；默认构建合并后的演示知识库")
    args = parser.parse_args()

    if args.pdf:
        chunks = load_pdf_chunks()
        collection_name = config.pdf_collection
        questions = ["DBS e-payment fraud unauthorized transaction what should customer do?",
                     "Citi Singapore dispute a card transaction documents required",
                     "Standard Chartered Singapore credit card unauthorized transaction terms",
                     "Deutsche Bank Singapore wealth management service agreement complaint contact",
                     "UOB lost stolen card questionnaire what information to report",
                     "Maybank Singapore dispute card transaction evidence form",
                     "Bank of America personal banking account fees schedule",
                     "UBS Switzerland dispute transaction pending reserved posted amount",
                     "How much is the account fee for a Business Multi-Currency Account?",
                     "What is the fee for an outward telegraphic transfer?",
                     "How much does a cheque book cost?", "coin deposit charge"]
    elif args.faq_only:
        docs = [p for p in sorted(config.kb_dir.glob("*.md")) if p.name.lower() != "readme.md"]  # README 是说明文档，不入库
        chunks = [c for path in docs for c in parse_doc(path)]
        for chunk in chunks:
            chunk["doc"] = f"lcb__faq__{chunk['doc']}"
        collection_name = config.kb_collection
        questions = ["How much is the overseas TT fee?", "汇款到中国要几天？", "我的 PIN 被 lock 了怎么办",
                     "我要怎么更新地址？", "ATM 吞了我的卡", "can I open account if I'm 15"]
    else:
        chunks = load_unified_chunks()
        collection_name = config.kb_collection
        questions = ["How much is the overseas TT fee?", "汇款到中国要几天？", "我的 PIN 被 lock 了怎么办",
                     "UOB lost stolen card questionnaire what information to report",
                     "Maybank Singapore dispute card transaction evidence form",
                     "Bank of America personal banking account fees schedule",
                     "UBS Switzerland dispute transaction pending reserved posted amount"]

    if not chunks:
        hint = "先在终端用 MinerU 解析 data/kb/pdf 里的 PDF" if args.pdf else f"{config.kb_dir} 里没有文档"
        print(f"没有可以入库的内容：{hint}")
        return

    for doc in sorted({c["doc"] for c in chunks}):
        n = sum(c["doc"] == doc for c in chunks)
        print(f"  {doc:<18} {n} 个块")
    print(f"共 {len(chunks)} 个块，最长 {max(len(c['text']) for c in chunks)} 字符")
    if args.check:
        return

    save_to_milvus(chunks, collection_name)
    search_test(questions, collection_name)


if __name__ == "__main__":
    main()
