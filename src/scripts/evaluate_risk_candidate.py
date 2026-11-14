"""预冻结的合成风险样本：同一输入比较 Qwen 和 Jev 的路由，不执行工单或账户操作。"""
import hashlib
import json
import time
from pathlib import Path

from src.nodes.intent_router import IntentRouterNode
from src.nodes.jev_intent import JevError, classify
from src.nodes.stub_nodes import EntityExtractionNode, GateNode, InputGuardNode
from src.schema.state import create_default_state
from src.scripts.evaluate_quality import PROJECT_ROOT, RESULT_DIR


DATA = PROJECT_ROOT / "data/evaluation/risk_candidate_20261115.jsonl"
OUTPUT = RESULT_DIR / "risk_candidate_20261115_results.json"


class JevRouter(IntentRouterNode):
    def _model_ready(self):
        return True

    def _classify(self, text):
        for attempt in range(3):
            try:
                result = classify(text)
                break
            except JevError as exc:
                transient = any(s in str(exc) for s in ("TimeoutError", "URLError", "HTTP 429", "HTTP 500", "HTTP 502", "HTTP 503", "HTTP 504"))
                if not transient or attempt == 2:
                    raise
                time.sleep(2 ** attempt)
        score = result.get("confidence")
        if not isinstance(score, (int, float)) or not 0 <= score <= 1:
            raise JevError("Jev 未返回有效 confidence，无法运行相同 Gate 阈值")
        return {"intent": result["intent"], "sub_intent": "", "intent_score": score,
                "router_backend": "jev"}


def route(question, router):
    state = create_default_state(question=question, customer_id="C001")
    state.update(InputGuardNode().process(state))
    if state["blocked"]:
        return {"intent": "blocked", "route": "blocked", "priority": None}
    state.update(router.process(state))
    state.update(EntityExtractionNode().process(state))
    state.update(GateNode().process(state))
    return {"intent": state["intent"], "route": state["route"],
            "priority": "urgent" if state["route"] == "handoff" and state["intent"] == "fraud_report" else None,
            "backend": state["router_backend"], "score": state["intent_score"]}


def save(result):
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    temp = OUTPUT.with_suffix(".tmp")
    temp.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    temp.replace(OUTPUT)


def main():
    content = DATA.read_bytes()
    digest = hashlib.sha256(content).hexdigest()
    rows = [json.loads(line) for line in content.decode("utf-8").splitlines() if line.strip()]
    if OUTPUT.exists():
        result = json.loads(OUTPUT.read_text(encoding="utf-8"))
        if result["dataset_sha256"] != digest or len(result["predictions"]) > len(rows):
            raise SystemExit("数据集不匹配，不能续跑。")
        if any(a["id"] != b["id"] or a["text"] != b["text"] for a, b in zip(result["predictions"], rows)):
            raise SystemExit("续跑样本不一致。")
    else:
        result = {"dataset_sha256": digest, "dataset": str(DATA), "predictions": []}
    qwen, jev = IntentRouterNode(), JevRouter()
    print(f"样本 {len(rows)}，数据 SHA-256 {digest}，已完成 {len(result['predictions'])} 条", flush=True)
    for i in range(len(result["predictions"]), len(rows)):
        row = rows[i]
        try:
            qwen_result = route(row["text"], qwen)
            jev_result = route(row["text"], jev)
        except JevError as exc:
            save(result)
            raise SystemExit(f"第 {i + 1} 条 Jev 失败：{exc}；已保存前面的结果。")
        result["predictions"].append({**row, "qwen": qwen_result, "jev": jev_result})
        save(result)
        print(f"{i + 1}/{len(rows)} {row['id']}", flush=True)
    print(f"完整结果：{OUTPUT}")


if __name__ == "__main__":
    main()
