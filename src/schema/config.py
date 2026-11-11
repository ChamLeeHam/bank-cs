import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional
from dotenv import load_dotenv, find_dotenv

ROOT_DIR = Path(__file__).resolve().parent.parent.parent
load_dotenv()

@dataclass
class Config:
    """管理整个项目的所有配置内容"""
    # 定义项目目录结构
    pre_train_dir = ROOT_DIR / "pretrained"  #预训练模型目录
    data_dir: Path = ROOT_DIR / "data"    #数据目录
    log_dir: Path = ROOT_DIR / "logs" # 日志目录
    checkpoint_dir: Path = ROOT_DIR / "checkpoint"   #checkpoint目录


    # 定义项目配置信息
    openai_api_key:str = field(default_factory=lambda: os.getenv("OPENAI_API_KEY",""))
    openai_api_base:str = field(default_factory=lambda: os.getenv("OPENAI_BASE_URL",""))
    default_model:str = field(default_factory=lambda: os.getenv("DEFAULT_MODEL","gpt-4o"))

    # DeepSeek 用于生成 / 翻译训练数据
    deepseek_api_key: str = field(default_factory=lambda: os.getenv("DEEPSEEK_API_KEY", ""))
    deepseek_base_url: str = field(default_factory=lambda: os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com"))
    deepseek_model: str = field(default_factory=lambda: os.getenv("DEEPSEEK_MODEL_NAME", "deepseek-chat"))

    # MYSQL数据库的配置
    mysql_host:str = field(default_factory=lambda: os.getenv("MYSQL_HOST","localhost"))
    mysql_user:str = field(default_factory=lambda: os.getenv("MYSQL_USER","root"))
    mysql_password:str = field(default_factory=lambda: os.getenv("MYSQL_PASSWORD",""))
    mysql_port: int = field(default_factory=lambda: int(os.getenv("MYSQL_PORT", "3306")))
    mysql_database:str = field(default_factory=lambda: os.getenv("MYSQL_DATABASE","lioncity_bank"))
    mysql_charset:str = field(default_factory=lambda: os.getenv("MYSQL_CHARSET","utf8mb4"))

    #NEO4J的配置
    neo4j_uri: str = field(default_factory=lambda: os.getenv("NEO4J_URI",""))
    neo4j_user:str = field(default_factory=lambda: os.getenv("NEO4J_USERNAME","neo4j"))
    neo4j_password:str = field(default_factory=lambda: os.getenv("NEO4J_PASSWORD",""))
    neo4j_database: str = field(default_factory=lambda: os.getenv("NEO4J_DATABASE","neo4j"))

    intent_threshold: float = field(default_factory=lambda: float(os.getenv("INTENT_THRESHOLD", "0.5")))
    clarify_threshold: float = field(default_factory=lambda: float(os.getenv("CLARIFY_THRESHOLD", "0.3")))

    # 意图识别模型。finetuned 用微调的 Qwen，keyword 只用关键词规则
    intent_backend: str = field(default_factory=lambda: os.getenv("INTENT_BACKEND", "finetuned"))
    intent_base_model: Path = field(default_factory=lambda: ROOT_DIR / os.getenv("INTENT_BASE_MODEL", "pretrained/Qwen2.5-1.5B-Instruct"))
    intent_adapter: Path = field(default_factory=lambda: ROOT_DIR / os.getenv("INTENT_ADAPTER", "checkpoint/intent_lora"))

    # 知识库
    # 集合名用 BANK_KB_COLLECTION，.env 里的 MILVUS_COLLECTION 是别的项目的
    milvus_uri: str = field(default_factory=lambda: os.getenv("MILVUS_URL", "http://localhost:19530"))
    milvus_token: str = field(default_factory=lambda: os.getenv("MILVUS_TOKEN", ""))
    kb_collection: str = field(default_factory=lambda: os.getenv("BANK_KB_COLLECTION", "lioncity_faq"))
    kb_dir: Path = field(default_factory=lambda: ROOT_DIR / os.getenv("BANK_KB_DIR", "data/kb"))
    embed_model: Path = field(default_factory=lambda: ROOT_DIR / os.getenv("EMBED_MODEL", "pretrained/bge-m3"))
    kb_top_k: int = field(default_factory=lambda: int(os.getenv("BANK_KB_TOP_K", "3")))
    # 用哪个模型生成回答：deepseek 或者 local（本地 Qwen）
    rag_generator: str = field(default_factory=lambda: os.getenv("RAG_GENERATOR", "deepseek"))

    # RAG 的参数，加 BANK_ 前缀是因为 .env 里已经有别的项目的 RERANKER_* 配置
    rag_rewrite: bool = field(default_factory=lambda: os.getenv("BANK_RAG_REWRITE", "true").lower() == "true")
    rag_hyde: bool = field(default_factory=lambda: os.getenv("BANK_RAG_HYDE", "true").lower() == "true")
    rag_candidates: int = field(default_factory=lambda: int(os.getenv("BANK_RAG_CANDIDATES", "10")))  # 每一路取多少条
    hyde_weight: float = field(default_factory=lambda: float(os.getenv("BANK_HYDE_WEIGHT", "0.7")))  # HyDE 那一路的权重
    reranker_model: Path = field(default_factory=lambda: ROOT_DIR / os.getenv("BANK_RERANKER_MODEL", "pretrained/bge-reranker-base"))
    rerank_min_topk: int = field(default_factory=lambda: int(os.getenv("BANK_RERANK_MIN_TOPK", "1")))
    rerank_max_topk: int = field(default_factory=lambda: int(os.getenv("BANK_RERANK_MAX_TOPK", "3")))
    rerank_gap_abs: float = field(default_factory=lambda: float(os.getenv("BANK_RERANK_GAP_ABS", "0.3")))
    rerank_ratio: float = field(default_factory=lambda: float(os.getenv("BANK_RERANK_RATIO", "0.5")))
    rerank_min_score: float = field(default_factory=lambda: float(os.getenv("BANK_RERANK_MIN_SCORE", "0.01")))  # 最高分低于这个就当作没找到

    #schema的配置
    schema:list = field(default_factory=lambda: [s.strip() for s in os.getenv("SCHEMA","").split(",") if s.strip()])

    #abstractmethod 抽象方法，之类必须实现
    @classmethod #类方法，不需要实例化就可以调用的方法
    def from_env(cls):
        """通过类构建对象，从环境中获取配置信息"""
        return cls()

_config:Optional[Config] = None

def get_config() -> Config:
    """获取配置的单例对象"""
    global _config
    if _config is None:
        _config = Config.from_env()
    return _config

if __name__ == "__main__":
    cfg = get_config()
    # print(cfg.openai_api_key)
    # print(cfg.openai_api_base)