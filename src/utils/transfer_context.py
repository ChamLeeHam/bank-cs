import re
import threading

_pending_snapshots = {}
_snapshot_lock = threading.Lock()


def begin_transfer_resume(session_id, candidate_ids):
    """恢复时保留暂停前展示的编号；相同会话不能并发恢复。"""
    with _snapshot_lock:
        if session_id in _pending_snapshots:
            raise ValueError("该会话正在处理，请稍后重试")
        _pending_snapshots[session_id] = list(candidate_ids)


def pending_transfer_ids(session_id):
    with _snapshot_lock:
        ids = _pending_snapshots.get(session_id)
        return list(ids) if ids is not None else None


def end_transfer_resume(session_id):
    with _snapshot_lock:
        _pending_snapshots.pop(session_id, None)


def transfer_other(text):
    """识别对上一组结果的‘其他汇款’追问，不猜测收款人范围。"""
    return bool(re.fullmatch(r"(?:我)?(?:还)?有(?:没有|没)?(?:其他|别的|其它)(?:的)?(?:汇款|转账)(?:吗|么|呢|没有)?[？?。.!！\s]*|"
                             r"(?:any|are there any) other transfers[?!.\s]*", (text or "").strip(), re.I))


def transfer_ordinal(text):
    """只识别独立的序号追问，不把其他问题猜成交易选择。"""
    text = (text or "").strip().lower()
    m = re.fullmatch(r"(?:那|那么|请问|那是)?\s*第\s*([一二三四五六七八九十1-9]|10)\s*笔(?:呢|怎么样|到了吗|到没)?[？?。.!！\s]*", text)
    if m:
        return {"一": 1, "二": 2, "三": 3, "四": 4, "五": 5, "六": 6,
                "七": 7, "八": 8, "九": 9, "十": 10}.get(m.group(1), int(m.group(1)) if m.group(1).isdigit() else None)
    m = re.fullmatch(r"(?:and\s+|what about\s+|how about\s+)?(?:the\s+)?(first|second|third)(?:\s+(?:one|transfer))?[?!.\s]*", text)
    return {"first": 1, "second": 2, "third": 3}.get(m.group(1)) if m else None
