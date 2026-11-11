"""
本地 BGE-M3 稠密向量（1024 维，中英文同一个向量空间，所以中文问题也能检索到英文段落）

只用 BGE-M3 的稠密向量；关键词匹配交给 Milvus 内置的 BM25（见 kb_store.py），不使用 BGE-M3 的 sparse / colbert 输出。
客户问题只在本机计算向量，不发送到外部服务（《需求文档》P2）。
"""
import logging
import threading
import time

from src.schema.config import get_config
from src.schema.exceptions import EmbeddingError

logger = logging.getLogger("bank.embedder")
EMBED_DIM = 1024


class BgeM3Embedder:
    """进程内只加载一次（约 2 秒）；GPU 上用 fp16"""

    _instance = None
    _lock = threading.Lock()

    def __init__(self):
        import torch
        from transformers import AutoModel, AutoTokenizer

        path = get_config().embed_model
        if not path.exists():
            raise EmbeddingError(f"找不到 BGE-M3 模型：{path}", "embedder")
        self.torch = torch
        self.device = "cuda" if torch.cuda.is_available() else "cpu"
        start = time.perf_counter()
        self.tokenizer = AutoTokenizer.from_pretrained(path)
        dtype = torch.float16 if self.device == "cuda" else torch.float32
        self.model = AutoModel.from_pretrained(path, dtype=dtype).to(self.device).eval()
        logger.info("BGE-M3 已加载，设备 %s，用时 %.1fs", self.device, time.perf_counter() - start)

    @classmethod
    def get(cls) -> "BgeM3Embedder":
        with cls._lock:
            if cls._instance is None:
                cls._instance = cls()
            return cls._instance

    def encode(self, texts: list[str], batch_size: int = 16, max_length: int = 512) -> list[list[float]]:
        """BGE-M3 稠密向量 = [CLS] 位置的隐藏状态，再做 L2 归一化（之后用内积 = 余弦相似度）"""
        torch = self.torch
        vectors: list[list[float]] = []
        for i in range(0, len(texts), batch_size):
            batch = self.tokenizer(texts[i:i + batch_size], padding=True, truncation=True, max_length=max_length,
                                   return_tensors="pt").to(self.device)
            with torch.no_grad():
                cls = self.model(**batch).last_hidden_state[:, 0]
                cls = torch.nn.functional.normalize(cls.float(), dim=-1)
            vectors += cls.cpu().tolist()
        return vectors
