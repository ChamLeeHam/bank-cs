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
- **混合检索**（`src/utils/milvus_utils.py`）：稠密向量（语义）+ Milvus 内置 BM25（关键词，jieba 分词 + 小写），RRF 融合
- **集合**：`lioncity_faq`（用 `BANK_KB_COLLECTION` 配置），不会影响 Milvus 中其他项目的集合

## RAG 流程（结构同课程 `03-rag`）

```
问题重写 -> 混合检索（BGE-M3 稠密 + BM25）─┐
                                          ├-> 加权 RRF -> bge-reranker 精排 + 断崖截断 -> 生成回答 -> 数字核对 + 引用
        HyDE 检索（问题 + 假设答案）────────┘
```

| 步骤 | 文件 | 对应课程文件 | 说明 |
|---|---|---|---|
| 问题重写 | `src/utils/rewrite_query_utils.py` | `13-rewritten_query.py` | 结合最近 2 轮对话，"那 FAST 呢？" → "FAST 转账的手续费是多少？"；第一轮不调用 |
| 混合检索 | `src/utils/milvus_utils.py`、`bge_m3_embedding_utils.py` | `12-search_embedding.py` | BGE-M3 稠密 + Milvus 内置 BM25（课程用 BGE-M3 的稀疏向量，作用相同） |
| HyDE 检索 | `src/utils/hyde_utils.py` | `search_embedding_HyDE.py` | LLM 先写一段假设答案，用"问题 + 假设答案"再检索一次；假设答案只用于检索 |
| 加权 RRF | `src/utils/rrf_utils.py` | `rrf.py` | 问题检索权重 1.0，HyDE 检索权重 0.7 |
| 精排 + 断崖截断 | `src/utils/bge_rerank_utils.py` | `reranker.py` | bge-reranker-base，保留 1–3 个章节；最高分 < 0.01 视为知识库中没有答案 |
| 生成 | `src/nodes/rag_answer.py` | `15-response.py` | 只能根据保留的章节回答，带上最近对话 |
| 评估 | `src/scripts/eval_rag.py` | `18/20-rag_*_eval.py`、`17-ragas-eval.py` | 检索 Hit@k + RAGAS 四项指标 |

与课程不同的地方：
- 修正了课程代码中的 3 个 bug：HyDE 提示词写成了元组（`HYDE_PROMPT_TEMPLATE, format(query)`），问题没有填进去；
  RRF 读取的环境变量名 `RRF_RESULT` 与 `.env` 中的 `RRF_MAX_RESULT` 不一致，结果从不截断；精排出错时返回空列表，所有检索结果丢失
- reranker 只用与问题语言相同的段落打分（章节是中英双语，整段送入时英文问题的得分明显偏低）
- 分数经过 sigmoid 归一化；参数使用 `BANK_` 前缀，不与 `.env` 中课程项目的 `RERANKER_*` 冲突
- 回答后增加数字核对，来源由代码追加

生成回答由 `RAG_GENERATOR` 选择：
- `deepseek`（默认）：DeepSeek API。问题重写、HyDE、回答都**只发送脱敏后的问题 / 对话和公开的 FAQ 片段**，不发送客户 ID 和账户数据
- `local`：本地 Qwen2.5-1.5B-Instruct（与意图模型共用权重，关闭 LoRA），数据完全不出本机
- DeepSeek 失败时自动改用本地 Qwen，再失败返回资料原文；Milvus 不可用 → 道歉并建议转人工

## 评估结果（`data/kb/eval_questions.jsonl`，36 条）

测试集：30 条知识库内的问题（问法与文档不同，中 / 英 / 中英混杂），4 条知识库外的问题，2 条依赖上一轮对话的追问。

**检索**（`data/kb/eval/retrieval_eval.json`）：

| 阶段 | Hit@1 | Hit@3 | MRR |
|---|---|---|---|
| A 混合检索 | 87.5% | 93.8% | 0.914 |
| B + 问题重写 | 90.6% | 96.9% | 0.938 |
| C + HyDE + RRF | **93.8%** | 96.9% | **0.956** |
| D + reranker（完整流程） | 84.4% | **100%** | 0.917 |

完整流程：知识库内的问题 **93.8%** 保留了正确章节；知识库外的问题 **100%** 正确判为"没有答案"；平均保留 1.4 个章节，检索约 3.7 秒。

- 问题重写和 HyDE 都有提升（Hit@1 87.5% → 93.8%）
- reranker 让 Hit@3 达到 100%，但 Hit@1 下降：bge-reranker-base 对短的中英双语章节判断不够准，
  例如"PayNow 转账要收钱吗？"把"如何收款"排在"本地转账费用"前面。断崖截断通常保留 1–3 个章节，所以回答仍能拿到正确章节
- reranker 的价值主要在**判断"没有答案"**：知识库外问题的最高分都 ≤ 0.0043，知识库内问题的正确章节几乎都 > 0.01
- 可改进：换成多语言更强的 bge-reranker-v2-m3

**RAGAS**（评判模型 DeepSeek，向量 BGE-M3，31 条回答，`data/kb/eval/ragas_eval.json`）：

| 指标 | 含义 | 分数 |
|---|---|---|
| ContextRelevance | 检索内容是否与问题相关 | 1.000 |
| AnswerRelevancy | 回答是否切题 | 0.827 |
| Faithfulness | 回答中的事实能否被检索内容支撑 | 0.853 |
| ResponseGroundedness | 回答是否由检索内容支撑 | 0.903 |

Faithfulness 最低的几条：一条是检索拿错章节（"朋友在美国要汇钱给我" 检索到"如何汇款到海外"而不是"如何收款"）；
其他是回答多加了一句资料中没有的"建议转人工客服协助"，评判模型按规则扣分。

运行评估：

```bash
uv run python src/scripts/eval_rag.py            # 检索评估
uv run python src/scripts/eval_rag.py --ragas    # 检索评估 + 生成回答 + RAGAS
uv run python src/scripts/eval_rag.py --rescore  # 用已保存的回答重新跑 RAGAS
```

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

- 首次提问需要加载 Qwen、BGE-M3 和 reranker（约 30 秒），之后每个回答约 5–7 秒（问题重写、HyDE、生成共 3 次 DeepSeek 调用）
- 断崖截断有时会多保留一个不太相关的章节，回答下方的来源也会列出它
