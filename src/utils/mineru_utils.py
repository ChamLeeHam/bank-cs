# MinerU 生成的 Markdown：找到文件、读出来、清洗
# PDF 不在 Python 里解析，先在终端手动运行 MinerU，输出放在 data/kb/mineru_output
# 在项目根目录运行测试：uv run python -m src.utils.mineru_utils
import html
import json
import logging
import re
from collections import Counter
from pathlib import Path

from src.schema.config import get_config
from src.schema.exceptions import ToolError

logger = logging.getLogger(__name__)

# 可能是小节标题的短行：少于 60 个字，末尾不是标点
MAX_TITLE_CHARS = 60
SENTENCE_END = ("。", "：", ":", ".", "；", ";", "，", ",")


# 1. 找到 MinerU 生成的所有 Markdown 文件
# 目录结构：mineru_output/文件名/auto/文件名.md
def find_markdown_files(output_dir):
    output_dir = Path(output_dir)
    if not output_dir.exists():
        raise ToolError(f"找不到 MinerU 的输出目录：{output_dir}，请先在终端运行 MinerU", "mineru_utils")

    md_files = []
    for doc_dir in sorted(output_dir.iterdir()):
        if not doc_dir.is_dir():
            continue
        # 只要 MinerU 自己生成的那个文件，同目录下别的 .md 不要
        md_path = doc_dir / "auto" / f"{doc_dir.name}.md"
        if md_path.exists():
            md_files.append(md_path)
        else:
            logger.warning(f"{doc_dir.name} 没有找到 Markdown 文件，这个 PDF 可能转换失败了")
    return md_files


# 2. 读一个 Markdown 文件
def read_markdown(md_path):
    md_path = Path(md_path)
    content = md_path.read_text(encoding="utf-8")
    return {
        "file_title": f"{md_path.stem}.pdf",  # 原来的 PDF 文件名，和课程的 file_title 一样
        "md_path": str(md_path),
        "content": content,
    }


# 3. 读出所有文档，空文件跳过
def load_mineru_documents(output_dir=None):
    output_dir = output_dir or get_config().mineru_output_dir
    docs = []
    for md_path in find_markdown_files(output_dir):
        doc = read_markdown(md_path)
        if not doc["content"].strip():
            logger.warning(f"{doc['file_title']} 的 Markdown 是空的，跳过")
            continue
        docs.append(doc)
    logger.info(f"读到 {len(docs)} 个 MinerU 文档")
    return docs


# 4. 清洗 MinerU 生成的 Markdown
# 表格的结构不动，只清理格式；切块交给后面的 md_split_utils
def clean_markdown(text):
    text = text.replace("\r\n", "\n")

    # 4.1 去掉第一个标题之前的封面和目录
    match = re.search(r"^#{1,6} ", text, flags=re.MULTILINE)
    if match:
        text = text[match.start():]

    # 4.2 去掉 <sub> <sup> 标签，保留里面的文字
    text = re.sub(r"</?su[bp]>", "", text)

    # 4.3 HTML 实体转回字符（&#x27; -> '），&lt; &gt; 不转，免得把表格的 HTML 弄乱
    text = re.sub(r"&(?!lt;|gt;)#?\w+;", lambda m: html.unescape(m.group()), text)
    # 去掉 Markdown 的转义：S\$30 -> S$30，\*The fee -> *The fee
    text = re.sub(r"\\([$*_#])", r"\1", text)

    # 4.4 去掉行尾空格
    lines = [line.rstrip() for line in text.split("\n")]

    # 4.5 补标题：紧挨在表格前面的一行短文字，其实是小节标题
    lines = _promote_table_titles(lines)

    # 4.6 连续多个空行合并成一个
    text = "\n".join(lines)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip() + "\n"


def _promote_table_titles(lines):
    """表格前面的短行改成三级标题，比如 "Cross Border Remittances [1]" -> "### Cross Border Remittances" """
    result = list(lines)
    for i, line in enumerate(lines):
        if not _looks_like_title(line):
            continue
        # 下一个非空行是表格才算
        next_line = next((l for l in lines[i + 1:] if l.strip()), "")
        if next_line.startswith("<table"):
            title = re.sub(r"\s*\[\d+\]$", "", line.strip())  # 去掉标题后面的脚注编号
            result[i] = f"### {title}"
    return result


def _looks_like_title(line):
    line = line.strip()
    if not line or len(line) > MAX_TITLE_CHARS:
        return False
    # 已经是标题、脚注 [1]、列表 •、表格都不算
    if line.startswith(("#", "[", "•", "-", "*", "<")):
        return False
    return not line.endswith(SENTENCE_END)


# 5. 补回小节标题：有的 PDF 把小节标题放在页眉，MinerU 会把页眉当成 header 丢掉，不写进 .md
# 从 _content_list.json 里找回来：每页都有的页眉（比如银行的标语）不要，只出现一次的才是小节标题
def restore_section_headers(md_path, text):
    md_path = Path(md_path)
    content_list_path = md_path.with_name(f"{md_path.stem}_content_list.json")
    if not content_list_path.exists():
        return text
    items = json.loads(content_list_path.read_text(encoding="utf-8"))

    # 5.1 只出现一次的页眉，按页码记下来
    headers = [i for i in items if i["type"] == "header" and (i.get("text") or "").strip()]
    counts = Counter(h["text"].strip() for h in headers)
    titles = {h["page_idx"]: h["text"].strip() for h in headers if counts[h["text"].strip()] == 1}

    # 5.2 找每一页第一块内容在 .md 里的位置
    # 同一句话可能出现好几次（比如每节开头都一样），所以从上一块的位置往后找
    cursor, page_start = 0, {}
    for item in items:
        if item["type"] not in ("text", "table"):
            continue
        # 表格有标题（table_caption）的话，.md 里标题在表格前面，要用标题来找
        caption = (item.get("table_caption") or [""])[0]
        key = (item.get("text") or caption or item.get("table_body") or "").strip()[:60]
        pos = text.find(key, cursor) if key else -1
        if pos < 0:
            continue
        page_start.setdefault(item["page_idx"], pos)
        cursor = pos + 1

    # 5.3 在那一页的开头插入标题，从后往前插，前面的位置就不会变
    for page in sorted(titles, reverse=True):
        if page in page_start and titles[page] not in text:
            pos = page_start[page]
            text = f"{text[:pos]}## {titles[page]}\n\n{text[pos:]}"
            logger.info(f"{md_path.stem} 第 {page + 1} 页补回标题：{titles[page]}")
    return text


# 6. 读出所有 MinerU 文档，补标题、清洗，返回 [{"file_title", "md_path", "content"}, ...]
def process(output_dir=None):
    docs = load_mineru_documents(output_dir)
    for doc in docs:
        text = restore_section_headers(doc["md_path"], doc["content"])
        doc["content"] = clean_markdown(text)
    return docs


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    docs = load_mineru_documents()
    print(f"共 {len(docs)} 个文档")
    for doc in docs:
        raw = doc["content"]
        cleaned = clean_markdown(restore_section_headers(doc["md_path"], raw))
        print(f"\n===== {doc['file_title']}")
        print(f"清洗前 {len(raw)} 字符，清洗后 {len(cleaned)} 字符")
        for name, pattern in [("<sub>/<sup>", r"</?su[bp]>"), ("HTML 实体", r"&#?\w+;"), ("转义 \\$", r"\\\$")]:
            print(f"  {name:<12} {len(re.findall(pattern, raw))} -> {len(re.findall(pattern, cleaned))}")
        print(f"  表格         {raw.count('<table')} -> {cleaned.count('<table')}")
        print("标题：")
        for line in cleaned.split("\n"):
            if line.startswith("#"):
                print("   ", line)
