"""智增增 Jev 意图分类对照组；不接入客户服务主图。"""
import json
import os
from urllib import error, request
from urllib.parse import urlsplit

from dotenv import load_dotenv

from src.schema.config import ROOT_DIR


# 与当前留出集的一级标签一致；不包含留出集没有的新增意图。
INTENT_CRITERIA = {
    "balance_inquiry": "Customer asks for the balance of their own account.",
    "transfer_status": "Customer asks about the status, arrival, failure or delay of their own transfer.",
    "card_lost_freeze": "Customer reports a lost or stolen card or asks to freeze, block or replace it for security.",
    "fees_rates": "Customer asks about fees, charges, exchange rates or how a fee is calculated.",
    "transfer_howto": "Customer asks how to send or receive transfers, general timing or transfer limits.",
    "card_service": "Customer asks about card activation, delivery, PIN, use, replacement or card features.",
    "account_service": "Customer asks about opening, verifying, updating, accessing or closing accounts.",
    "fraud_report": "Customer reports an unauthorized or unrecognized transaction, withdrawal or card misuse.",
    "dispute_refund": "Customer disputes a transaction, duplicate charge, incorrect amount or missing refund.",
    "complaint": "Customer complains about service or requests complaint handling.",
    "out_of_scope": "The request is outside supported banking services, such as unrelated topics or investment advice.",
}


class JevError(RuntimeError):
    """请求或响应异常；不泄露密钥或上游请求内容。"""


def _endpoint(base_url):
    base = base_url.strip().rstrip("/")
    url = urlsplit(base)
    if url.scheme != "https" or not url.hostname or url.username or url.password or url.query or url.fragment:
        raise JevError("TYPESAFE_BASE_URL 必须是无凭据、无查询参数的 HTTPS 地址")
    if base.endswith("/v1/systemone"):
        return base
    if base.endswith("/v1"):
        return base + "/systemone"
    return base + "/v1/systemone"


def classify(text, *, api_key=None, base_url=None, model=None, timeout=30):
    """返回一级意图和 Jev 原始概率；失败直接报错，不偷偷回退其他模型。"""
    load_dotenv(ROOT_DIR / ".env", override=False)
    key = api_key if api_key is not None else os.getenv("TYPESAFE_API_KEY", "")
    if not key:
        raise JevError("未配置 TYPESAFE_API_KEY；请在本地环境变量或项目 .env 中设置")
    if not text or not text.strip():
        raise ValueError("问题不能为空")
    base = base_url or os.getenv("TYPESAFE_BASE_URL", "https://api.zhizengzeng.com/v1")
    model = model or os.getenv("TYPESAFE_MODEL", "jev-latest")
    payload = {
        "model": model,
        "state": {"customer_message": text},
        "questions": {"intent": {"type": "choice", "instructions":
            "Select the single best banking customer intent. Prefer fraud when the customer says a transaction was not authorized. "
            "Distinguish a specific personal transfer from general transfer instructions.", "criteria": INTENT_CRITERIA}},
    }
    req = request.Request(_endpoint(base), data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
                          headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
                          method="POST")
    try:
        with request.urlopen(req, timeout=timeout) as response:
            result = json.load(response)
    except error.HTTPError as exc:
        raise JevError(f"Jev 接口返回 HTTP {exc.code}；请检查密钥、模型和接口配置") from None
    except (error.URLError, OSError, ValueError) as exc:
        raise JevError(f"Jev 请求失败：{type(exc).__name__}") from None
    try:
        answer = result["answers"]["intent"]
        choice = answer["choice"]
        probabilities = answer.get("probabilities", {})
        if choice not in INTENT_CRITERIA or not isinstance(probabilities, dict):
            raise ValueError
        return {"intent": choice, "probabilities": probabilities, "confidence": answer.get("confidence"),
                "model": result.get("model", model), "usage": result.get("usage", {})}
    except (TypeError, KeyError, ValueError):
        raise JevError("Jev 返回格式无效或意图标签不在允许列表中") from None
