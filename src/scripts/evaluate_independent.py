"""Evaluate frozen synthetic routing and knowledge follow-ups on the local demo."""
import hashlib
import json
import subprocess
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parents[2]
DATA = ROOT / "data/evaluation"
DATE = "20261118"


def load(group):
    raw = (DATA / f"independent_{group}_{DATE}.jsonl").read_bytes()
    return [json.loads(line) for line in raw.splitlines()], hashlib.sha256(raw).hexdigest()


def single(rows):
    from src.nodes.intent_router import IntentRouterNode
    from src.nodes.stub_nodes import EntityExtractionNode, GateNode, InputGuardNode
    from src.schema.state import create_default_state

    guard, router, extract, gate = InputGuardNode(), IntentRouterNode(), EntityExtractionNode(), GateNode()
    predictions = []
    for row in rows:
        state = create_default_state(question=row["text"], customer_id="C001")
        state.update(guard.process(state))
        if state.get("blocked"):
            actual = {"intent": "blocked", "route": "blocked"}
        else:
            state.update(router.process(state))
            raw = {key: state.get(key) for key in ("intent", "intent_score", "router_backend")}
            state.update(extract.process(state))
            state.update(gate.process(state))
            actual = {"intent": state["intent"], "route": state["route"], **{"router": raw}}
        predictions.append({**row, "actual": actual})
        print(row["id"], actual["intent"], actual["route"], flush=True)
    return predictions


def post(path, payload):
    request = Request("http://127.0.0.1:8002" + path, json.dumps(payload).encode(),
                      {"Content-Type": "application/json"})
    with urlopen(request, timeout=45) as response:
        return json.load(response)


def multi_case(row):
    turns = []
    try:
        session = post("/api/sessions", {"customer_id": "C001"})["session_id"]
        for turn in row["turns"]:
            turns.append(post(f"/api/sessions/{session}/messages", {"text": turn}))
    except (OSError, KeyError, ValueError) as exc:
        return {**row, "responses": turns, "error": str(exc), "passed": False}
    last = turns[-1]
    source_text = json.dumps(last.get("sources", []), ensure_ascii=False).lower()
    passed = (bool(turns[0].get("sources")) and not last.get("pending")
              and row["answer_contains"].lower() in last.get("answer", "").lower()
              and row["source_contains"].lower() in source_text)
    print(row["id"], passed, flush=True)
    return {**row, "responses": turns, "passed": passed}


def matrix(rows, key):
    result = {}
    for r in rows:
        counts = result.setdefault(r[key], Counter())
        counts[r["actual"][key]] += 1
    return result


def metrics(rows):
    return {key: {"correct": sum(r[key] == r["actual"][key] for r in rows), "total": len(rows)}
            for key in ("intent", "route")}


def main():
    singles, single_hash = load("single")
    conversations, multi_hash = load("multiturn")
    with ThreadPoolExecutor(max_workers=1) as worker:
        future = worker.submit(lambda: [multi_case(row) for row in conversations])
        predictions = single(singles)
        multi = future.result()
    risky = [r["id"] for r in predictions if r["intent"] in {"fraud_report", "dispute_refund"}
             and r["actual"]["route"] == "rag"]
    backend_counts = Counter(r["actual"].get("router", {}).get("router_backend")
                             for r in predictions)
    result = {
        "commit": subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip(),
        "sha256": {"single": single_hash, "multiturn": multi_hash},
        "summary": metrics(predictions),
        "language": {lang: metrics([r for r in predictions if r["lang"] == lang])
                     for lang in ("zh", "en", "mixed")},
        "intent_matrix": matrix(predictions, "intent"),
        "route_matrix": matrix(predictions, "route"),
        "fraud_or_dispute_to_rag": risky,
        "high_risk": metrics([r for r in predictions if r["risk"] == "high"]),
        "router_backends": backend_counts,
        "multiturn_passed": sum(r["passed"] for r in multi),
        "predictions": predictions,
        "multiturn": multi,
    }
    path = DATA / "results" / f"independent_baseline_{DATE}.json"
    path.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(path)


if __name__ == "__main__":
    main()
