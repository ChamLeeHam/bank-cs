"""Run the live card-delivery multi-turn regression against the local API.

Usage: uv run python tests/live_multiturn_regression.py
Optional: BANKCS_API_BASE=http://127.0.0.1:8003 uv run python tests/live_multiturn_regression.py
"""
import json
import os
import re
import time
import urllib.request
from pathlib import Path


BASE = os.getenv("BANKCS_API_BASE", "http://127.0.0.1:8002").rstrip("/")
RESULTS = []


def request(path, payload=None, timeout=180):
    data = None if payload is None else json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(
        BASE + path, data=data, method="POST" if payload is not None else "GET",
        headers={"Content-Type": "application/json"},
    )
    started = time.perf_counter()
    with urllib.request.urlopen(req, timeout=timeout) as response:
        body = json.loads(response.read())
        status = response.status
    return status, body, round((time.perf_counter() - started) * 1000, 1)


def has_any(text, alternatives):
    text = text.lower()
    return any(value.lower() in text for value in alternatives)


DELIVERY_SOURCE = ("card delivery time", "新卡寄送时间")
SUPPLEMENTARY_SOURCE = ("replacement and supplementary cards", "补发实体卡与附属卡")

SCENARIOS = [
    ("连续追问补发卡与超时处理", [
        ("新卡一般几天寄到？", ("5–7", "5-7", "5 to 7"), DELIVERY_SOURCE),
        ("那补发卡也一样吗？", ("5–7", "5-7", "5 to 7"), DELIVERY_SOURCE),
        ("超过10个工作日还没收到呢？", ("10", "联系", "补寄", "resend"), DELIVERY_SOURCE),
    ]),
    ("切换主题后回到寄送问题", [
        ("我申请的新卡还没到，大概多久？", ("5–7", "5-7", "5 to 7"), DELIVERY_SOURCE),
        ("顺便问下，PayNow转账收费吗？", ("免费", "不收费", "free", "no fee"), ("paynow",)),
        ("那新卡超过10个工作日还没收到怎么办？", ("10", "联系", "补寄", "resend"), DELIVERY_SOURCE),
    ]),
    ("插入无关问题后追问寄送期限", [
        ("补办的银行卡几天能寄到？", ("5–7", "5-7", "5 to 7"), DELIVERY_SOURCE),
        ("谢谢。顺便问，借记卡可以申请附属卡吗？", ("不提供", "cannot", "does not offer"), SUPPLEMENTARY_SOURCE),
        ("刚才那张补办卡如果超过10个工作日呢？", ("10", "联系", "补寄", "resend"), DELIVERY_SOURCE),
    ]),
    ("地址限制后追问处理时限", [
        ("新卡可以寄到新加坡以外吗？", ("不支持", "cannot", "outside singapore"), DELIVERY_SOURCE),
        ("那超过10个工作日没收到呢？", ("10", "联系", "补寄", "resend"), DELIVERY_SOURCE),
    ]),
    ("英文多轮追问", [
        ("How long does a replacement card take to arrive?", ("5–7", "5-7", "5 to 7"), DELIVERY_SOURCE),
        ("What if it still has not arrived after 10 working days?", ("10", "contact", "resend"), DELIVERY_SOURCE),
    ]),
]


def main():
    for scenario, turns in SCENARIOS:
        status, body, _ = request("/api/sessions", {"customer_id": "C001"})
        if status != 200 or not body.get("session_id"):
            raise RuntimeError(f"Could not create demo session: HTTP {status}")
        session_id = body["session_id"]

        for question, expected_answer, expected_source in turns:
            status, body, elapsed_ms = request(
                f"/api/sessions/{session_id}/messages", {"text": question}
            )
            answer = str(body.get("answer", ""))
            sources = body.get("sources") or []
            source_text = " ".join(
                " ".join(str(source.get(key, "")) for key in ("title", "section", "label"))
                for source in sources if isinstance(source, dict)
            )
            answer_ok = has_any(answer, expected_answer)
            source_ok = has_any(source_text, expected_source)
            pending = body.get("pending")
            passed = status == 200 and answer_ok and source_ok and pending is None
            result = {
                "scenario": scenario,
                "question": question,
                "answer": answer,
                "sources": sources,
                "status": status,
                "elapsed_ms": elapsed_ms,
                "answer_ok": answer_ok,
                "source_ok": source_ok,
                "pending": pending,
                "passed": passed,
            }
            RESULTS.append(result)
            print(json.dumps(result, ensure_ascii=False), flush=True)

    total = len(RESULTS)
    passed = sum(result["passed"] for result in RESULTS)
    report = {
        "base_url": BASE,
        "scenario_count": len(SCENARIOS),
        "turn_count": total,
        "passed": passed,
        "failed": total - passed,
        "mean_latency_ms": round(sum(item["elapsed_ms"] for item in RESULTS) / total, 1),
        "results": RESULTS,
    }
    output_dir = Path("data/evaluation/results")
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "multiturn_followup_20261115.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    lines = [
        "# 多轮对话实时回归（新卡寄送 FAQ）", "",
        f"- 独立会话：{len(SCENARIOS)}", f"- 总轮次：{total}",
        f"- 通过：{passed}/{total}", f"- 接口平均耗时：{report['mean_latency_ms']:.0f} ms",
        f"- 测试环境：本机 FastAPI、真实检索和回答链路（`{BASE}`）",
        "- 测试只询问 FAQ，没有执行账户冻结或工单创建等写操作。", "",
        "| 对话 | 用户问题 | 耗时 | 答案 | 来源 | 结果 |", "|---|---|---:|---|---|---|",
    ]
    for item in RESULTS:
        lines.append(
            f"| {item['scenario']} | {item['question']} | {item['elapsed_ms']:.0f} ms "
            f"| {'通过' if item['answer_ok'] else '失败'} "
            f"| {'通过' if item['source_ok'] else '失败'} "
            f"| {'通过' if item['passed'] else '失败'} |"
        )
    lines += ["", "## 回复与来源"]
    for index, item in enumerate(RESULTS, 1):
        lines += [
            "", f"### {index}. {item['scenario']}：{item['question']}", "",
            item["answer"], "", "**来源：** " + json.dumps(item["sources"], ensure_ascii=False),
        ]
    (output_dir / "multiturn_followup_20261115.md").write_text(
        "\n".join(lines) + "\n", encoding="utf-8"
    )
    print("SUMMARY " + json.dumps({
        "scenarios": len(SCENARIOS), "turns": total, "passed": passed,
        "failed": total - passed, "mean_latency_ms": report["mean_latency_ms"],
    }, ensure_ascii=False), flush=True)
    raise SystemExit(0 if passed == total else 1)


if __name__ == "__main__":
    main()
