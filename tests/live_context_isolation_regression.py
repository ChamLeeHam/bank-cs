"""Check that card-delivery follow-up context does not leak to unrelated turns.

Usage: uv run python tests/live_context_isolation_regression.py
"""
import json
import time
import urllib.request
from pathlib import Path


BASE = "http://127.0.0.1:8002"
RESULTS = []


def post(path, payload, timeout=180):
    req = urllib.request.Request(
        BASE + path,
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        method="POST",
        headers={"Content-Type": "application/json"},
    )
    started = time.perf_counter()
    with urllib.request.urlopen(req, timeout=timeout) as response:
        return response.status, json.loads(response.read()), round((time.perf_counter() - started) * 1000, 1)


def session():
    status, body, _ = post("/api/sessions", {"customer_id": "C001"})
    assert status == 200
    return body["session_id"]


def ask(sid, question):
    return post(f"/api/sessions/{sid}/messages", {"text": question})


def check(scenario, question, status, body, elapsed, *, expected_source=None, should_clarify=False,
          answer_terms=()):
    answer = body.get("answer", "")
    sources = body.get("sources") or []
    source_text = " ".join(
        " ".join(str(source.get(k, "")) for k in ("title", "section", "label"))
        for source in sources if isinstance(source, dict)
    ).lower()
    answer_l = answer.lower()
    if expected_source:
        source_ok = expected_source.lower() in source_text
    else:
        source_ok = True
    if should_clarify:
        clarification_ok = any(term in answer_l for term in (
            "具体说明", "再具体", "could you tell me", "could you clarify", "a bit more"
        ))
        no_assumed_delivery = "card delivery time" not in source_text and "新卡寄送时间" not in source_text
        answer_ok = clarification_ok and no_assumed_delivery and not sources
    else:
        clarification_ok = None
        answer_ok = all(term.lower() in answer_l for term in answer_terms)
    passed = status == 200 and source_ok and answer_ok and body.get("pending") is None
    item = {
        "scenario": scenario, "question": question, "answer": answer, "sources": sources,
        "status": status, "elapsed_ms": elapsed, "expected_source": expected_source,
        "answer_ok": answer_ok, "source_ok": source_ok,
        "clarification_ok": clarification_ok, "pending": body.get("pending"), "passed": passed,
    }
    RESULTS.append(item)
    print(json.dumps(item, ensure_ascii=False), flush=True)


def main():
    # A stale delivery topic must not be revived after PayNow becomes the current topic.
    sid = session()
    ask(sid, "新卡一般几天寄到？")
    ask(sid, "PayNow转账收费吗？")
    s, b, t = ask(sid, "超过10个工作日还没收到呢？")
    check("PayNow 后的省略追问", "超过10个工作日还没收到呢？", s, b, t, should_clarify=True)

    # A fresh session has no antecedent for the same ellipsis.
    sid = session()
    s, b, t = ask(sid, "超过10个工作日还没收到呢？")
    check("无上下文的省略追问", "超过10个工作日还没收到呢？", s, b, t, should_clarify=True)

    # A specific new PIN question overrides the older delivery topic.
    sid = session()
    ask(sid, "新卡一般几天寄到？")
    s, b, t = ask(sid, "我的银行卡 PIN 被锁了，怎么解锁？")
    check("寄送主题之后切换 PIN", "我的银行卡 PIN 被锁了，怎么解锁？", s, b, t,
          expected_source="Changing or unblocking your PIN", answer_terms=("pin", "解锁"))

    # PIN is now the immediate topic; the older delivery topic must not resolve this ellipsis.
    s, b, t = ask(sid, "超过10个工作日还没收到呢？")
    check("PIN 主题后的省略追问", "超过10个工作日还没收到呢？", s, b, t, should_clarify=True)

    # The phrase should not assume a card in English when no context exists.
    sid = session()
    s, b, t = ask(sid, "What if it still has not arrived after 10 working days?")
    check("无上下文英文省略追问", "What if it still has not arrived after 10 working days?",
          s, b, t, should_clarify=True)

    # Keep two customer sessions interleaved: B must not inherit A's card-delivery context,
    # while A must retain its own context after B has asked an unrelated question.
    sid_a = session()
    ask(sid_a, "补办的银行卡几天能寄到？")
    status, body, elapsed = post("/api/sessions", {"customer_id": "C002"})
    assert status == 200 and body.get("session_id")
    sid_b = body["session_id"]
    s, b, t = ask(sid_b, "超过10个工作日还没收到呢？")
    check("会话B不继承会话A的寄卡上下文", "超过10个工作日还没收到呢？", s, b, t,
          should_clarify=True)
    s, b, t = ask(sid_a, "超过10个工作日还没收到呢？")
    check("会话A保留自己的寄卡上下文", "超过10个工作日还没收到呢？", s, b, t,
          expected_source="新卡寄送时间", answer_terms=("10", "补寄"))

    passed = sum(item["passed"] for item in RESULTS)
    report = {"base_url": BASE, "checks": len(RESULTS), "passed": passed,
              "failed": len(RESULTS) - passed, "results": RESULTS}
    output_dir = Path("data/evaluation/results")
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "context_isolation_20261116.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    lines = ["# 多轮上下文隔离实测", "", f"- 检查项：{len(RESULTS)}", f"- 通过：{passed}/{len(RESULTS)}", "",
             "| 场景 | 问题 | 结果 | 回复 |", "|---|---|---|---|"]
    for item in RESULTS:
        lines.append(f"| {item['scenario']} | {item['question']} | {'通过' if item['passed'] else '失败'} | {item['answer']} |")
    (output_dir / "context_isolation_20261116.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("SUMMARY " + json.dumps({"checks": len(RESULTS), "passed": passed,
                                   "failed": len(RESULTS) - passed}, ensure_ascii=False), flush=True)
    raise SystemExit(0 if passed == len(RESULTS) else 1)


if __name__ == "__main__":
    main()
