# 知识库（RAG）

狮城银行（演示）的 FAQ 文档。**所有费用、规则、时间均为虚构**，与模拟业务库（`src/datasync/seed.sql`）保持一致，
例如海外电汇手续费 SGD 10、TT 1–3 个工作日到账。

## 文档

| 文件 | 一级意图 | 章节数 | 内容 |
|---|---|---|---|
| `fees_rates.md` | fees_rates | 7 | 本地 / 海外转账费、汇率、外币刷卡费、ATM 费、账单费用、卡费 |
| `transfer_guide.md` | transfer_howto | 7 | 转账方式、到账时间、海外汇款、收款、收款人限制、限额、失败原因 |
| `card_services.md` | card_service | 8 | 激活、寄送、PIN、卡被拒、吞卡、到期、补卡、卡组织 |
| `account_services.md` | account_service | 7 | 开户条件、身份验证、修改资料、忘记密码、销户、多币种 |

格式：开头 front matter 写 `title` 和 `intent`；每个 `## ` 章节是一个检索块，**中文段落 + 英文段落**放在同一块里；
`<!-- sub_intents: ... -->` 标注章节对应的二级意图（必须是 `src/intents.py` 中存在的标签，`build_kb.py` 会校验）。

## 构建与检索

```bash
uv run python src/scripts/build_kb.py          # 切块 -> BGE-M3 向量 -> 重建 Milvus 集合 lioncity_faq -> 检索自测
uv run python src/scripts/build_kb.py --check  # 只校验文档格式
```

- **向量**：本地 BGE-M3（`pretrained/bge-m3`），1024 维稠密向量，中英文在同一个向量空间，客户问题不发送到外部服务
- **混合检索**（`src/rag/kb_store.py`）：稠密向量（语义）+ Milvus 内置 BM25（关键词，jieba 分词 + 小写），RRF 融合
- **集合**：`lioncity_faq`（用 `BANK_KB_COLLECTION` 配置），不会影响 Milvus 中其他项目的集合

## 回答节点（`src/nodes/rag_answer.py`）

1. 检索 top 3 个章节（本地完成），与本轮二级意图对应的章节排在最前
2. 生成回答，由 `RAG_GENERATOR` 选择：
   - `deepseek`（默认）：DeepSeek API。**只发送脱敏后的问题和公开的 FAQ 片段**，不发送客户 ID、账户数据和对话历史
   - `local`：本地 Qwen2.5-1.5B-Instruct（与意图模型共用权重，关闭 LoRA），数据完全不出本机
   - DeepSeek 失败时自动改用本地 Qwen
3. **数字核对**：回答中每个数字都必须在资料或问题中出现过，否则改用资料原文（防止编造费用和天数）
4. **引用**：来源由代码追加，不依赖模型，例如"来源：《费用与汇率》海外电汇（TT）手续费"
5. **降级**：生成模型都不可用 → 返回最相关章节原文；Milvus 不可用 → 道歉并建议转人工

## 为什么回答用 DeepSeek

本地 1.5B 模型对"条件判断"类问题会给出错误结论：英文问 "Can I open an account if I'm 15?" 回答 "Yes"（资料写 16 岁以上），
而数字核对抓不到这类错误（所有数字都来自资料）。改用 DeepSeek 后，8 个测试问题全部正确，资料中没有的问题（如定期存款利率）
会说明不确定并建议转人工，不会编造。

| | 本地 Qwen2.5-1.5B | DeepSeek |
|---|---|---|
| 测试问题正确 | 7 / 8 | 8 / 8 |
| 每个回答用时 | 1–8 秒 | 1.5–4 秒 |
| 数据是否离开本机 | 否 | 只有脱敏后的问题 + 公开 FAQ 片段 |

意图识别、向量检索、账户查询仍全部在本地完成；客户身份和账户数据不会发送给 DeepSeek。

## 已知问题

- 首次提问需要加载 Qwen 和 BGE-M3（约 25–30 秒），之后正常
- 资料中没有答案时，回答下方仍会列出检索到的（不相关的）章节作为来源
