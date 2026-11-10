

class BankAssistantError(Exception):
    """提供统一的异常处理逻辑"""
    def __init__(self, message: str, node_name: str="", cause: Exception=None) -> None:
        super().__init__(message)
        self.node_name = node_name
        self.cause = cause

    def __str__(self) -> str:
        parts = []
        if self.node_name:
            parts.append(f"Node:{self.node_name}")  # 节点信息
        parts.append(super().__str__())  # message信息
        if self.cause:
            parts.append(f"Cause:{self.cause}")  # 错误原因
        return " ".join(parts)  # 将所有的内容统一返回

class ConfigError(BankAssistantError):
    pass

class ValidationError(BankAssistantError):
    pass

class LLMError(BankAssistantError):
    pass

class ModelError(BankAssistantError):
    pass

class EmbeddingError(BankAssistantError):
    pass

class ToolError(BankAssistantError):
    pass

class StorageError(BankAssistantError):
    pass

class MySQLError(StorageError):
    pass

class MilvusError(StorageError):
    """milvus向量数据库的异常"""
    pass

class Neo4jError(StorageError):
    pass



if __name__ == "__main__":
    try:
        raise MilvusError("milvus连接错误", "spell_check_node")
    except MilvusError as e:
        print(e)