# Markdown 切块：MinerU 清洗后的文档按标题切块，表格不拆开，记下每块的标题路径
# 切块的核心代码（Block 到 split_markdown）来自课程 03-rag 的 07-md_split_by_handler.py，改了两处：
#   1. 一节比 min_chars 还短时，课程代码会报错，这里整节作为一块（_partition_group）
#   2. 去掉了"块不能短于 min_chars"的检查
# 加了三部分：
#   1. 太大的表格按行拆成几个小表格，每个小表格都带上表头（split_large_tables）
#   2. MinerU 认错了的表格先不入库（UNVERIFIED_TABLES）
#   3. 把切好的块转成知识库的字段：doc、title、section、text（process）
# 在项目根目录运行测试：uv run python -m src.utils.md_split_utils
from __future__ import annotations

import bisect
import hashlib
import heapq
import logging
import re
from dataclasses import dataclass
from html.parser import HTMLParser
from pathlib import Path

logger = logging.getLogger(__name__)

MIN_CHARS = 300
MAX_CHARS = 1500  # HTML 表格的标签很占字数，比课程的 2000 小一点，reranker 也只看前 512 个 token


# ===== 下面这部分来自课程的 07-md_split_by_handler.py =====

ATX = re.compile(r"^ {0,3}(#{1,6})(?:[ \t]+(.*?)|[ \t]*)$")
SETEXT = re.compile(r"^ {0,3}(=+|-+)[ \t]*$")
FENCE = re.compile(r"^ {0,3}(`{3,}|~{3,})(.*)$")
TABLE_KINDS = {"html_table", "pipe_table"}
ATOMIC_KINDS = TABLE_KINDS | {"code", "heading"}
SENTENCE_END = set("。！？；.!?;\n")


class SplitError(ValueError):
    """原文格式错误，或者约束之间存在冲突。"""


@dataclass
class Block:
    start: int
    end: int
    kind: str
    level: int = 0
    title: str = ""
    # (标题级别, 标题文字, 标题起始偏移)，偏移用来区分同名章节。
    path: tuple[tuple[int, str, int], ...] = ()


def line_offsets(text: str) -> list[int]:
    return [0] + [m.end() for m in re.finditer("\n", text)]


def fenced_blocks(text: str) -> list[Block]:
    """先识别顶层围栏代码块，避免代码里的 <table> 被当作实际表格。"""
    lines = text.splitlines(keepends=True)
    offsets = [0]
    for line in lines:
        offsets.append(offsets[-1] + len(line))
    result = []
    i = 0
    while i < len(lines):
        match = FENCE.match(lines[i].rstrip("\r\n"))
        if not match or (match[1][0] == "`" and "`" in match[2]):
            i += 1
            continue
        marker = match[1]
        close = re.compile(r"^ {0,3}" + re.escape(marker[0])
                           + "{" + str(len(marker)) + r",}[ \t]*$")
        j = i + 1
        while j < len(lines) and not close.match(lines[j].rstrip("\r\n")):
            j += 1
        # Markdown 允许代码围栏在 EOF 结束。
        end = min(j + 1, len(lines))
        result.append(Block(offsets[i], offsets[end], "code"))
        i = end
    return result


class TableLocator(HTMLParser):
    """定位最外层 table 原文范围，不重建 HTML，不丢 rowspan/colspan。"""

    def __init__(self, text: str, base: int):
        super().__init__(convert_charrefs=False)
        self.text = text
        self.base = base
        self.lines = line_offsets(text)
        self.depth = 0
        self.begin = 0
        self.blocks: list[Block] = []

    def token_offset(self) -> int:
        line, column = self.getpos()
        return self.lines[line - 1] + column

    def handle_starttag(self, tag, attrs):
        if tag == "table":
            if self.depth == 0:
                self.begin = self.token_offset()
            self.depth += 1

    def handle_startendtag(self, tag, attrs):
        if tag == "table":
            raise SplitError("发现自闭合 <table/>；请先修复表格结构。")

    def handle_endtag(self, tag):
        if tag != "table":
            return
        if self.depth == 0:
            raise SplitError("发现没有对应起始标签的 </table>。")
        self.depth -= 1
        if self.depth == 0:
            end = self.text.index(">", self.token_offset()) + 1
            self.blocks.append(Block(self.base + self.begin, self.base + end,
                                     "html_table"))


def pipe_cells(line: str) -> list[str]:
    """按未转义的 | 分列；保留空单元格，支持可选的首尾竖线。"""
    value = line.strip()
    cells = []
    start = 0
    i = 0
    while i < len(value):
        if value[i] == "\\":
            i += 2
            continue
        if value[i] == "|":
            cells.append(value[start:i])
            start = i + 1
        i += 1
    cells.append(value[start:])
    if value.startswith("|"):
        cells.pop(0)
    if value.endswith("|") and cells and cells[-1] == "":
        cells.pop()
    return cells


def is_pipe_start(lines: list[str], i: int) -> bool:
    if i + 1 >= len(lines) or "|" not in lines[i]:
        return False
    header = pipe_cells(lines[i])
    delimiter = pipe_cells(lines[i + 1])
    return (len(header) == len(delimiter) and bool(delimiter)
            and all(re.fullmatch(r":?-+:?", cell.strip()) for cell in delimiter))


def plain_blocks(text: str, base: int) -> list[Block]:
    """解析顶层 ATX/Setext 标题、管道表格、段落和空白。"""
    lines = text.splitlines(keepends=True)
    offsets = [base]
    for line in lines:
        offsets.append(offsets[-1] + len(line))
    result = []
    i = 0
    while i < len(lines):
        raw = lines[i].rstrip("\r\n")
        heading = ATX.match(raw)
        if not raw.strip():
            j = i + 1
            while j < len(lines) and not lines[j].strip():
                j += 1
            block = Block(offsets[i], offsets[j], "space")
        elif heading:
            j = i + 1
            title = re.sub(r"[ \t]+#+[ \t]*$", "", heading[2] or "").strip()
            block = Block(offsets[i], offsets[j], "heading", len(heading[1]), title)
        elif is_pipe_start(lines, i):
            j = i + 2
            while (j < len(lines) and lines[j].strip()
                   and not ATX.match(lines[j].rstrip("\r\n"))):
                j += 1
            block = Block(offsets[i], offsets[j], "pipe_table")
        elif (i + 1 < len(lines)
              and SETEXT.match(lines[i + 1].rstrip("\r\n"))):
            j = i + 2
            level = 1 if lines[i + 1].lstrip().startswith("=") else 2
            block = Block(offsets[i], offsets[j], "heading", level, raw.strip())
        else:
            j = i + 1
            while j < len(lines) and lines[j].strip():
                if (ATX.match(lines[j].rstrip("\r\n"))
                        or is_pipe_start(lines, j)
                        or (j + 1 < len(lines)
                            and SETEXT.match(lines[j + 1].rstrip("\r\n")))):
                    break
                j += 1
            block = Block(offsets[i], offsets[j], "paragraph")
        result.append(block)
        i = j
    return result


def parse_blocks(text: str) -> list[Block]:
    fences = fenced_blocks(text)
    protected = list(fences)
    cursor = 0
    for fence in fences + [Block(len(text), len(text), "sentinel")]:
        segment = text[cursor:fence.start]
        parser = TableLocator(segment, cursor)
        parser.feed(segment)
        parser.close()
        if parser.depth:
            raise SplitError("存在未闭合的 <table>，请先修复源文档。")
        protected.extend(parser.blocks)
        cursor = fence.end
    protected.sort(key=lambda block: block.start)
    result = []
    cursor = 0
    for block in protected:
        result.extend(plain_blocks(text[cursor:block.start], cursor))
        result.append(block)
        cursor = block.end
    result.extend(plain_blocks(text[cursor:], cursor))
    stack = []
    for block in result:
        if block.kind == "heading":
            while stack and stack[-1][0] >= block.level:
                stack.pop()
            stack.append((block.level, block.title, block.start))
        block.path = tuple(stack)
    return result


def groups_for(blocks: list[Block], scope: str) -> list[list[Block]]:
    """section: 最深标题；chapter: 最外层标题。文首内容归入首个分组。"""
    if not blocks:
        return []
    index = -1 if scope == "section" else 0
    first_key = next((b.path[index][2] for b in blocks if b.path), -1)
    result: list[list[Block]] = []
    previous = None
    for block in blocks:
        key = block.path[index][2] if block.path else first_key
        if not result or key != previous:
            result.append([])
        result[-1].append(block)
        previous = key
    return result


def partition(text: str, blocks: list[Block], min_chars: int, max_chars: int,
              allow_oversize: bool) -> list[tuple[int, int]]:
    """动态规划找合法分区，避免贪心切分留下无法合并的小尾块。

    dp[i] 表示处理完前 i 个字符的最佳代价；上一块起点 j 必须满足
    i-max_chars <= j <= i-min_chars。滑动窗口堆避免逐一枚举全部 j。
    代价依次比较：语义切口罚分、超大原子附带字符数、块数。
    """
    base, stop = blocks[0].start, blocks[-1].end
    body = text[base:stop]
    n = len(body)
    allowed = bytearray(b"\x01") * (n + 1)
    penalty = [32] * (n + 1)
    for i in range(1, n):
        if body[i - 1] in SENTENCE_END:
            penalty[i] = 4
        elif body[i - 1].isspace():
            penalty[i] = 8
    large = []
    for block in blocks:
        a, b = block.start - base, block.end - base
        penalty[a] = 0 if block.kind == "heading" else min(penalty[a], 1)
        penalty[b] = min(penalty[b], 1)
        if block.kind in ATOMIC_KINDS:
            allowed[a + 1:b] = b"\x00" * max(0, b - a - 1)
            if b - a > max_chars:
                if not allow_oversize or block.kind == "heading":
                    raise SplitError(f"{block.kind} 原子块有 {b-a} 字符，超过 {max_chars}。")
                large.append((a, b))
    penalty[0] = penalty[n] = 0
    costs: list[tuple[int, int, int] | None] = [None] * (n + 1)
    previous = [-1] * (n + 1)
    costs[0] = (0, 0, 0)
    window = []
    starts = {a: (k, b) for k, (a, b) in enumerate(large)}
    jumps = {}
    active = None
    for i in range(n + 1):
        # 普通块：长度在闭区间 [min_chars, max_chars] 内。
        j = i - min_chars
        if j >= 0 and costs[j] is not None:
            heapq.heappush(window, (*costs[j], j))
        while window and window[0][3] < i - max_chars:
            heapq.heappop(window)
        if i in jumps:
            active = jumps[i]
        if active and i > active[2]:
            active = None
        if i and allowed[i]:
            choices = []
            if window:
                p, extra, count, j = window[0]
                choices.append(((p + penalty[i], extra, count + 1), j))
            # 显式例外：跨过一个超大原子；其前后最多各附带 max_chars。
            # 边界仍避开表格内部，小尾块可以附着在超大表格上。
            if active:
                j, atomic_length, _ = active
                p, extra, count = costs[j]
                choices.append(((p + penalty[i], extra + i - j - atomic_length,
                                 count + 1), j))
            if choices:
                costs[i], previous[i] = min(choices)
        if i in starts:
            k, b = starts[i]
            lower = max(0, i - max_chars, large[k - 1][1] if k else 0)
            candidates = [j for j in range(lower, i + 1) if costs[j] is not None]
            if candidates:
                j = min(candidates, key=lambda x: (costs[x][0], costs[x][1] - x,
                                                   costs[x][2], x))
                upper = min(n, b + max_chars,
                            large[k + 1][0] if k + 1 < len(large) else n)
                jumps[b] = (j, b - i, upper)
    if costs[n] is None:
        title = " / ".join(item[1] for item in blocks[0].path) or "文档开头"
        raise SplitError(
            f"分组『{title}』共 {n} 字符，无法在当前合并范围内切成 "
            f"[{min_chars}, {max_chars}] 且不拆原子块；"
            "可能是整节过短，或相邻不可拆块的长度组合无解。"
            "请检查 --merge-scope、长度阈值或表格表示。")
    result = []
    end = n
    while end:
        begin = previous[end]
        result.append((base + begin, base + end))
        end = begin
    return result[::-1]


def inspect_document(text: str, blocks: list[Block], max_chars: int) -> dict:
    offsets = line_offsets(text)
    atoms = []
    tables = []
    for block in blocks:
        item = {"kind": block.kind, "start_char": block.start, "end_char": block.end,
                "chars": block.end - block.start,
                "line": bisect.bisect_right(offsets, block.start),
                "section": [entry[1] for entry in block.path]}
        if block.kind in TABLE_KINDS:
            tables.append(item)
        if block.kind in ATOMIC_KINDS and item["chars"] > max_chars:
            atoms.append(item)
    return {"source_chars": len(text), "table_count": len(tables),
            "tables": tables, "oversized_atoms": atoms}


def split_markdown(text: str, min_chars: int = 500, max_chars: int = 2000,
                   merge_scope: str = "chapter", allow_oversize: bool = False,
                   source: str = "") -> list[dict]:
    """返回可直接用于 RAG 的 page_content + metadata 字典列表。

    chapter 允许同一最外层标题内的相邻小节合并；优先在小节边界切。
    section 更严格，只允许同一最深标题下的内容合并。
    保持原文顺序、完整覆盖、不重叠；LF 规范化后可以逐字符复原。
    """
    if not 1 <= min_chars <= max_chars:
        raise ValueError("必须满足 1 <= min_chars <= max_chars。")
    if merge_scope not in {"chapter", "section"}:
        raise ValueError("merge_scope 必须是 chapter 或 section。")
    text = text.removeprefix("\ufeff").replace("\r\n", "\n").replace("\r", "\n")
    if not text.strip():
        return []
    blocks = parse_blocks(text)
    report = inspect_document(text, blocks, max_chars)
    conflicts = [atom for atom in report["oversized_atoms"]
                 if not allow_oversize or atom["kind"] == "heading"]
    if conflicts:
        details = "; ".join(f"第 {a['line']} 行 {a['kind']}={a['chars']} 字符"
                            for a in conflicts)
        raise SplitError(f"不可拆块超过 {max_chars} 字符：{details}。"
                         "保持原文时，长度上限与完整保留存在冲突。"
                         "如接受超限，请显式传入 --allow-oversize。")
    ranges = []
    for group in groups_for(blocks, merge_scope):
        ranges.extend(_partition_group(text, group, min_chars, max_chars, allow_oversize))
    offsets = line_offsets(text)
    fingerprint = hashlib.sha256(text.encode("utf-8")).hexdigest()[:12]
    tables = [block for block in blocks if block.kind in TABLE_KINDS]
    result = []
    for index, (begin, end) in enumerate(ranges, 1):
        overlapping = [block for block in blocks
                       if block.start < end and block.end > begin]
        sections = []
        section_keys = set()
        for block in overlapping:
            key = tuple(entry[2] for entry in block.path)
            if block.kind != "space" and key not in section_keys:
                section_keys.add(key)
                sections.append([entry[1] for entry in block.path])
        content = text[begin:end]
        metadata = {"source": source, "chunk_index": index, "chars": len(content),
                    "start_char": begin, "end_char": end,
                    "start_line": bisect.bisect_right(offsets, begin),
                    "end_line": bisect.bisect_right(offsets, end - 1),
                    "sections": sections,
                    "table_ids": [i for i, t in enumerate(tables, 1)
                                  if begin <= t.start and t.end <= end],
                    "oversized": len(content) > max_chars,
                    "oversized_atoms": [a for a in report["oversized_atoms"]
                                        if begin <= a["start_char"] and a["end_char"] <= end]}
        result.append({"id": f"{fingerprint}-{index:04d}",
                       "page_content": content, "metadata": metadata})
    # 最终验收：没有漏文、重复；每个表格在唯一块内完整出现。短块是允许的（见 _partition_group）
    if "".join(item["page_content"] for item in result) != text:
        raise AssertionError("切分后无法完整复原原文。")
    for item in result:
        meta = item["metadata"]
        if meta["oversized"] and (not allow_oversize or not meta["oversized_atoms"]):
            raise AssertionError("出现没有明确原子块原因的超限。")
    for table in tables:
        if sum(begin <= table.start and table.end <= end for begin, end in ranges) != 1:
            raise AssertionError("表格未完整归属于唯一块。")
    return result

# ===== 下面是加的部分 =====


def _partition_group(text, group, min_chars, max_chars, allow_oversize):
    """一节的长度不够 min_chars，或者凑不出合适的切法时，放宽下限再切"""
    try:
        return partition(text, group, min_chars, max_chars, allow_oversize)
    except SplitError:
        return partition(text, group, 1, max_chars, allow_oversize)


# 1. 把 HTML 表格读成二维表，rowspan / colspan 展开，每个格子都填上文字
class _TableReader(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=False)
        self.rows = []  # 每行是 [(文字, rowspan, colspan), ...]
        self.cell = None

    def handle_starttag(self, tag, attrs):
        if tag == "tr":
            self.rows.append([])
        elif tag in ("td", "th"):
            attrs = dict(attrs)
            self.cell = {"text": [], "rowspan": int(attrs.get("rowspan") or 1),
                         "colspan": int(attrs.get("colspan") or 1)}

    def handle_data(self, data):
        if self.cell is not None:
            self.cell["text"].append(data)

    def handle_entityref(self, name):
        # &lt; 这些保持原样，免得表格里出现 "<"
        if self.cell is not None:
            self.cell["text"].append(f"&{name};")

    def handle_charref(self, name):
        if self.cell is not None:
            self.cell["text"].append(f"&#{name};")

    def handle_endtag(self, tag):
        if tag in ("td", "th") and self.cell is not None and self.rows:
            self.rows[-1].append(("".join(self.cell["text"]).strip(), self.cell["rowspan"], self.cell["colspan"]))
            self.cell = None


def _table_to_grid(table_html):
    reader = _TableReader()
    reader.feed(table_html)
    grid, carry = [], {}  # carry: 列号 -> (上面合并下来的文字, 还剩几行)
    for row in reader.rows:
        out, col = {}, 0
        for text, rowspan, colspan in row:
            # 先跳过上面几行合并下来的格子
            while col in carry:
                out[col] = carry[col][0]
                col += 1
            for _ in range(colspan):
                out[col] = text
                if rowspan > 1:
                    carry[col] = (text, rowspan)
                col += 1
        # 这一行最后面还有合并下来的格子
        for c in sorted(carry):
            out.setdefault(c, carry[c][0])
        # 合并的行数减一
        carry = {c: (t, left - 1) for c, (t, left) in carry.items() if left - 1 > 0}
        width = max(out) + 1 if out else 0
        cells = [out.get(i, "") for i in range(width)]
        if any(cells):
            grid.append(cells)
    # 第一行有 rowspan=2 的话，表头占两行
    header_rows = max((rowspan for _, rowspan, _ in reader.rows[0]), default=1) if reader.rows else 1
    return grid, header_rows


def _grid_to_html(rows):
    body = "".join("<tr>" + "".join(f"<td>{cell}</td>" for cell in row) + "</tr>" for row in rows)
    return f"<table>{body}</table>"


# 2. 太大的表格按行拆成几个小表格，每个小表格都带上表头
# 小表格不动（保留原来的 rowspan），只拆超过 max_chars 的
TABLE_RE = re.compile(r"<table>.*?</table>", re.DOTALL)


def split_large_tables(text, max_chars=MAX_CHARS):
    def split_one(match):
        table = match.group()
        if len(table) <= max_chars:
            return table
        grid, header_rows = _table_to_grid(table)
        header, body = grid[:header_rows], grid[header_rows:]
        if not body:
            return table
        pieces, current = [], []
        for row in body:
            if current and len(_grid_to_html(header + current + [row])) > max_chars:
                pieces.append(current)
                current = []
            current.append(row)
        if current:
            pieces.append(current)

        # 有的表格很宽（每列是一种账户），一行就超过 max_chars，再按列拆
        tables = []
        for piece in pieces:
            rows = header + piece
            if len(_grid_to_html(rows)) <= max_chars:
                tables.append(_grid_to_html(rows))
            else:
                tables.extend(_split_columns(rows, max_chars))
        logger.info(f"表格 {len(table)} 字符，拆成 {len(tables)} 个小表格")
        return "\n\n".join(tables)

    return TABLE_RE.sub(split_one, text)


def _split_columns(rows, max_chars):
    """按列拆，每个小表格都留着第一列（行名，比如 Account Fee、Service Charge）"""
    width = max(len(row) for row in rows)
    rows = [row + [""] * (width - len(row)) for row in rows]
    tables, cols = [], []
    for col in range(1, width):
        if cols and len(_grid_to_html([[row[0]] + [row[c] for c in cols + [col]] for row in rows])) > max_chars:
            tables.append(_grid_to_html([[row[0]] + [row[c] for c in cols] for row in rows]))
            cols = []
        cols.append(col)
    if cols:
        tables.append(_grid_to_html([[row[0]] + [row[c] for c in cols] for row in rows]))
    return tables


# 3. 一块的标题路径，写法参考课程 10-chunks_to_vector.py 的 _parse_chunk
# sections 是 [["Local Remittances", "Cheque Services"], ...]，返回 "Local Remittances > Cheque Services"
def _section_of(metadata):
    paths = [path for path in metadata.get("sections", []) if path]
    # 去掉已经有子路径的父路径
    paths = [path for path in paths if not any(
        len(other) > len(path) and other[:len(path)] == path for other in paths)]
    if not paths:
        return ""
    title = " / ".join(dict.fromkeys(path[-1] for path in paths))
    parent = []
    for level in zip(*(path[:-1] for path in paths)):
        if len(set(level)) > 1:
            break
        parent.append(level[0])
    return " > ".join(parent + [title])


# 4. MinerU 认错了的表格先不入库
# 这些表格里面还套着小表格，MinerU 拆出来以后数字放到了别的表头下面，和 PDF 原文对过才发现的。
# 用表格里一段只有它才有的文字来认；和 PDF 核对改好以后，就从这里删掉
UNVERIFIED_TABLES = {
    "dbs_pricing_guide": {
        "AverageDailyBalance": "01 节账户费用表，外币最低余额的小表套在里面，币种排到了 Account Types 下面",
        "Fund (e.g., Private": "03 节账户费用表，Service Charge 一格被拆成了好几行",
        "Account Closure Fee": "Account Service Fees，对账单和审计确认的小表套在里面，费用排到了销户费下面",
        "Cheque Express": "Local Remittances，最后一格 PayNow 的小表乱了",
        "Commission: 1/8%": "Cross Border Remittances，一格里的几条说明被拆成了几行",
        "ChequeDepositBag": "Cheque Services，套了 6 个小表，认成了 17 列",
    },
}
UNVERIFIED_NOTE = "（这个表格 MinerU 解析有误，和 PDF 原文核对之前没有收录）"


def remove_unverified_tables(stem, text):
    snippets = UNVERIFIED_TABLES.get(stem, {})
    found = set()

    def check(match):
        table = match.group()
        for snippet, reason in snippets.items():
            if snippet in table:
                found.add(snippet)
                logger.info(f"{stem} 去掉没核对的表格：{reason}")
                return UNVERIFIED_NOTE
        return table

    text = TABLE_RE.sub(check, text)
    # MinerU 重新解析以后表格可能变了，认不出来的要提醒一下
    for snippet in set(snippets) - found:
        logger.warning(f"{stem} 里没找到要去掉的表格：{snippet}，请检查 UNVERIFIED_TABLES")
    return text


# 5. 切块，转成知识库的字段
# docs 是 mineru_utils.process() 的结果：[{"file_title", "md_path", "content"}, ...]
def process(docs=None, min_chars=MIN_CHARS, max_chars=MAX_CHARS):
    if docs is None:
        from src.utils import mineru_utils
        docs = mineru_utils.process()

    chunks = []
    for doc in docs:
        stem = Path(doc["file_title"]).stem
        text = remove_unverified_tables(stem, doc["content"])
        text = split_large_tables(text, max_chars)
        items = split_markdown(text, min_chars, max_chars, merge_scope="chapter",
                               allow_oversize=True, source=doc["file_title"])
        for item in items:
            content = item["page_content"].strip()
            section = _section_of(item["metadata"]) or stem
            chunks.append({
                "doc": stem[:64],
                "title": doc["file_title"][:200],
                "section": section[:200],
                # 存的文本带上标题路径，和 build_kb.py 一样，标题里的词也能搜到
                "text": f"{section}\n{content}"[:8192],
                "chunk_id": item["id"],
                "chars": len(content),
            })
        logger.info(f"{doc['file_title']} 切成 {len(items)} 块")
    return chunks


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    from src.utils import mineru_utils

    docs = mineru_utils.process()
    chunks = process(docs)
    print(f"\n共 {len(chunks)} 块")
    sizes = [c["chars"] for c in chunks]
    print(f"长度：最短 {min(sizes)}，最长 {max(sizes)}，平均 {sum(sizes) // len(sizes)}")
    for c in chunks:
        print(f"  {c['chars']:>5}  表格 {c['text'].count('<table')}  {c['section']}")

    # 写一个预览文件，方便看切得对不对（在 mineru_output 里，不会提交）
    for doc in docs:
        stem = Path(doc["file_title"]).stem
        preview = [f"<!-- {c['chars']} 字符 | {c['section']} -->\n{c['text']}" for c in chunks if c["doc"] == stem]
        out = Path(doc["md_path"]).with_name(f"{stem}_chunks_preview.md")
        out.write_text("\n\n---\n\n".join(preview), encoding="utf-8")
        print(f"预览：{out}")
