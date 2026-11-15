"""Load and run the local inference components once before accepting customer traffic."""
import logging
import time

from src.schema.config import get_config

logger = logging.getLogger(__name__)


def warmup_runtime():
    config = get_config()
    started = time.perf_counter()
    probe_question = "通过 PayNow 进行的新加坡本地转账收费吗？"
    warmed = []
    failures = []

    def attempt(name, operation):
        try:
            operation()
            warmed.append(name)
        except Exception as exc:
            failures.append(name)
            logger.warning("启动预热 %s 失败，首次请求时会重试：%s", name, exc)

    if config.intent_backend == "finetuned":
        from src.nodes.intent_router import predict

        attempt("intent", lambda: predict(probe_question))

    def warm_rag_pipeline():
        from src.nodes.rag_answer import RagAnswerNode

        node = RagAnswerNode()
        result = node.answer(probe_question, [], "zh")
        expected_generator = "deepseek" if config.rag_generator == "deepseek" else "local"
        actual_generator = node.last_run.get("generator")
        if result.get("error"):
            raise RuntimeError(f"RAG warmup retrieval failed: {result['error']}")
        if not node.last_run.get("kept") or actual_generator != expected_generator:
            raise RuntimeError(f"RAG warmup incomplete: generator={actual_generator}")

    # Exercise the same embedding -> Milvus -> reranker -> citation-format generator path
    # used by a normal FAQ response, so the first customer turn does not pay cold-start cost.
    attempt("rag_pipeline", warm_rag_pipeline)

    elapsed = time.perf_counter() - started
    logger.info("服务启动预热完成：已预热 %s；失败 %s；用时 %.1f 秒",
                ", ".join(warmed) or "无", ", ".join(failures) or "无", elapsed)
    return {"warmed": warmed, "failed": failures, "elapsed_seconds": elapsed}
