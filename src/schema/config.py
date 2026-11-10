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
    checkpoint_dir: Path = ROOT_DIR / "checkpoints"   #checkpoint目录


    # 定义项目配置信息
    openai_api_key:str = field(default_factory=lambda: os.getenv("OPENAI_API_KEY",""))
    openai_api_base:str = field(default_factory=lambda: os.getenv("OPENAI_BASE_URL",""))
    default_model:str = field(default_factory=lambda: os.getenv("DEFAULT_MODEL","gpt-4o"))

    # MYSQL数据库的配置
    mysql_host:str = field(default_factory=lambda: os.getenv("MYSQL_HOST","localhost"))
    mysql_user:str = field(default_factory=lambda: os.getenv("MYSQL_USER","root"))
    mysql_password:str = field(default_factory=lambda: os.getenv("MYSQL_PASSWORD",""))
    mysql_port:str = field(default_factory=lambda: os.getenv("MYSQL_PORT","3306"))
    mysql_database:str = field(default_factory=lambda: os.getenv("MYSQL_DATABASE","mall"))
    mysql_charset:str = field(default_factory=lambda: os.getenv("MYSQL_CHARSET","utf8"))

    #NEO4J的配置
    neo4j_uri: str = field(default_factory=lambda: os.getenv("NEO4J_URI",""))
    neo4j_user:str = field(default_factory=lambda: os.getenv("NEO4J_USERNAME","neo4j"))
    neo4j_password:str = field(default_factory=lambda: os.getenv("NEO4J_PASSWORD",""))
    neo4j_database: str = field(default_factory=lambda: os.getenv("NEO4J_DATABASE","neo4j"))

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
    print(cfg.openai_api_key)
    print(cfg.openai_api_base)