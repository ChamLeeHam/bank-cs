# 知识库

狮城银行的 FAQ 文档。里面的费用、规则和时间都是编的，但和模拟数据库（src/datasync/seed.sql）里的数据对得上，
比如海外电汇手续费是 SGD 10，TT 一般 1 到 3 个工作日到账。

## 文档

| 文件 | 一级意图 | 章节数 | 内容 |
|---|---|---|---|
| fees_rates.md | fees_rates | 7 | 转账手续费、汇率、外币刷卡费、ATM 费用、账单上的费用、卡的年费 |
| transfer_guide.md | transfer_howto | 7 | 转账方式、到账时间、怎么汇款到海外、怎么收款、收款人限制、限额、转账失败的原因 |
| card_services.md | card_service | 8 | 激活、寄卡、PIN、卡被拒、ATM 吞卡、卡到期、补卡、Visa 还是 Mastercard |
| account_services.md | account_service | 7 | 开户条件、身份验证、改资料、忘记密码、销户、多币种账户 |

每个文件开头写 title 和 intent。每个 "## " 章节是一块，中文一段、英文一段放在一起。
`<!-- sub_intents: ... -->` 写这一节对应哪些二级意图，build_kb.py 会检查这些标签是不是存在。

## 建库

```bash
uv run python src/scripts/build_kb.py          # 重建 Milvus 集合 lioncity_faq，再测一下检索
uv run python src/scripts/build_kb.py --check  # 只检查文档格式
```

向量用本地的 bge-m3，中文和英文在同一个向量空间里，客户的问题不会发到外面。
和课程 03-rag 一样，bge-m3 同时生成稠密向量和稀疏向量（generate_hybrid_embedding），
Milvus 里两个向量各建一个索引（dense_vector 用 HNSW，sparse_vector 用倒排索引），检索时两路结果用 RRF 合并。
向量用 pymilvus 的 BGEM3EmbeddingFunction 生成（里面是 FlagEmbedding），返回的稀疏向量是 CSR 格式，再转成 {token_id: 权重}。
模型目录里要同时有 sparse_linear.pt 和 colbert_linear.pt。少了一个 FlagEmbedding 不会报错，
但稀疏向量那一层会用随机参数，检索结果就不对了，所以 get_bge_m3_embedding_model() 会先检查这两个文件。
集合名是 lioncity_faq（BANK_KB_COLLECTION），和 Milvus 里其他项目的集合分开。

## 回答一个问题的流程

```
问题重写 -> 混合检索 ─┐
                      ├-> RRF 合并 -> reranker 精排 -> 生成回答 -> 检查数字、加上来源
           HyDE 检索 ─┘
```

写法参考了课程的 03-rag，每一步是 src/utils 下的一个文件，都有一个 process() 函数，返回一个字典。
rag_answer.py 这个节点按顺序调用它们。

| 步骤 | 文件 | 做什么 |
|---|---|---|
| 问题重写 | rewrite_query_utils.py | 结合前两轮对话，把"那 FAST 呢？"改成"FAST 转账的手续费是多少？"。第一轮不用改 |
| 混合检索 | search_embedding_utils.py | bge-m3 的稠密向量加稀疏向量，RRF 合并 |
| HyDE 检索 | hyde_utils.py | 先让 LLM 写一个参考答案，再用问题和参考答案一起检索。参考答案只用来检索 |
| RRF | rrf_utils.py | 合并两路结果，问题检索的权重是 1.0，HyDE 是 0.7 |
| 精排 | reranker_utils.py | 用 bge-reranker-base 重新排序，留 1 到 3 个章节。最高分低于 0.01 就当作知识库里没有答案 |
| 生成回答 | response_utils.py | 只能根据留下来的章节回答 |
| 评估 | src/scripts/eval_rag.py | 检索的 Hit@k 和 RAGAS 的四个指标 |

另外几个工具文件：llm_util.py、bge_m3_embedding_utils.py、bge_rerank_utils.py、milvus_utils.py、history_utils.py。

和课程代码有几个地方不一样：

- 课程的 HyDE 提示词那里写成了 `HYDE_PROMPT_TEMPLATE, format(query)`，问题没有填进去；
  RRF 读的环境变量名和 .env 里的对不上；精排出错时会返回空列表。这三处在这里都改过了。
- 每个章节是中英双语的，整段拿去给 reranker 打分的话，英文问题的分数会低很多，所以只用和问题同一种语言的那一段来打分。
- 回答生成以后检查一下里面的数字，资料里没有的数字就不用模型的回答，直接给资料原文。来源是代码加上去的，不靠模型自己写。

回答用哪个模型由 RAG_GENERATOR 决定。默认是 deepseek，发过去的只有脱敏后的问题、对话和公开的 FAQ 内容，
没有客户 ID 和账户数据。改成 local 就用本地的 Qwen2.5-1.5B，数据完全不出本机。
DeepSeek 调用失败会自动换成本地 Qwen，再不行就直接给资料原文。Milvus 连不上的话会提示客户稍后再试或者转人工。

## 评估结果

测试集是 eval_questions.jsonl，一共 36 条：30 条知识库里有答案（问法和文档不一样，有中文、英文和中英混杂），
4 条知识库里没有，2 条要结合上一轮对话的追问。

检索的结果（eval/retrieval_eval.json）：

| 阶段 | Hit@1 | Hit@3 | MRR |
|---|---|---|---|
| A 只用混合检索 | 78.1% | 87.5% | 0.843 |
| B 加上问题重写 | 78.1% | 90.6% | 0.851 |
| C 再加上 HyDE 和 RRF | 81.2% | 90.6% | 0.879 |
| D 再加上 reranker（完整流程） | 84.4% | 96.9% | 0.907 |

完整流程下，有答案的问题 93.8% 留下了正确的章节，没答案的 4 个问题判断出来 3 个。平均留 1.4 个章节，检索大概 4.5 秒。
没判断出来的是 "How do I apply for a home loan?"，留下了"开户条件"，reranker 分数 0.0201，刚好比 0.01 高一点。
问题重写和 HyDE 每次生成的内容会有一点不一样，所以 B、C、D 每次跑出来的数字会有小的变化
（上一次跑 C 是 78.1% / 93.8% / 0.860，留下正确章节的是 90.6%），A 只用向量，每次都一样。

- 问题重写和 HyDE 让 Hit@1 从 78.1% 提高到 81.2%，MRR 从 0.843 提高到 0.879。
- 这一版 reranker 是有用的：Hit@1 从 81.2% 提高到 84.4%，Hit@3 到了 96.9%，前面几步排错的章节被它排回来了一部分。
- reranker 还用来判断知识库里有没有答案，但是像"home loan"和"开户条件"这种沾点边的问题，分数会超过 0.01。
- 以后可以试试 bge-reranker-v2-m3，它对多语言的支持更好。

### 稀疏向量：bge-m3 和 BM25 的比较

一开始的稀疏检索用的是 Milvus 自带的 BM25（jieba 分词），后来改成和课程一样用 bge-m3 的稀疏向量。
有答案的 32 条问题，只看第一步混合检索（不重写、不用 HyDE）：

| 检索方式 | Hit@1 | Hit@3 | MRR |
|---|---|---|---|
| 只用稠密向量 | 90.6% | 96.9% | 0.942 |
| 只用 bge-m3 稀疏向量 | 68.8% | 84.4% | 0.766 |
| 稠密 + bge-m3 稀疏，RRF（现在用的） | 78.1% | 87.5% | 0.843 |
| 稠密 + bge-m3 稀疏，加权 0.7 / 0.3 | 87.5% | 96.9% | 0.919 |
| 稠密 + BM25，RRF（之前用的） | 87.5% | 93.8% | 0.914 |

完整流程的比较：

| | 稠密 + BM25（之前） | 稠密 + bge-m3 稀疏（现在） |
|---|---|---|
| 完整流程 Hit@1 / Hit@3 / MRR | 81.2% / 100% / 0.901 | 84.4% / 96.9% / 0.907 |
| 有答案的问题留下了正确章节 | 93.8% | 93.8%（另一次跑是 90.6%） |
| 没答案的问题判断出来 | 4 / 4 | 3 / 4 |

完整流程下两种差不多，因为问题重写、HyDE 和 reranker 会把第一步的差距补回来一部分，但是第一步混合检索差了很多。
在这个知识库上 bge-m3 的稀疏向量比 BM25 差，原因是它按 token 匹配，而同一个词在不同位置会被切成不同的 token。
比如问题 "overseas TT fee" 里的 TT 前面有空格，切出来是 `▁TT`，文档里写的是"（TT）"，切出来是不带 `▁` 的 `TT`，两个对不上。
"fee" 和 "fees" 也是两个不同的 token。BM25 用 jieba 分词，不管 TT 在哪里都切成一样的词，所以没有这个问题。
RRF 不管分数只看排名，两路的权重一样，稀疏向量这一路排错了就会把稠密向量排对的结果拉下来。
改成加权合并（课程里用的是 WeightedRanker）可以恢复大部分，这里还是按课程的思路用 RRF，把这个比较留着做参考。

RAGAS 的结果（评判模型 DeepSeek，向量 bge-m3，31 条回答，eval/ragas_eval.json）。
这是改成 bge-m3 稀疏向量之前（BM25 那一版）跑的，换了以后没有重新跑：

| 指标 | 意思 | 分数 |
|---|---|---|
| ContextRelevance | 检索到的内容和问题相不相关 | 1.000 |
| AnswerRelevancy | 回答有没有答到点上 | 0.827 |
| Faithfulness | 回答里的内容能不能在资料里找到 | 0.853 |
| ResponseGroundedness | 回答是不是基于资料 | 0.903 |

Faithfulness 分数低的几条里，有一条是检索错了（"朋友在美国要汇钱给我"找到的是"如何汇款到海外"，应该是"如何收款"），
其他几条是回答最后多说了一句"建议转人工客服"，资料里没有这句话，所以被扣了分。

```bash
uv run python src/scripts/eval_rag.py            # 检索评估
uv run python src/scripts/eval_rag.py --ragas    # 检索评估 + RAGAS
uv run python src/scripts/eval_rag.py --rescore  # 用上次保存的回答重新跑 RAGAS
```

## 为什么用 DeepSeek 生成回答

一开始用的是本地的 Qwen2.5-1.5B。测试的时候问它 "Can I open an account if I'm 15?"，它回答 "Yes"，
可是资料里写的是 16 岁以上。这种逻辑判断的错误，检查数字是查不出来的，因为数字都对。
换成 DeepSeek 以后 8 个测试问题都答对了，知识库里没有的问题（比如定期存款利率）它会说不确定，建议转人工，不会乱编。

| | 本地 Qwen2.5-1.5B | DeepSeek |
|---|---|---|
| 测试问题答对 | 7 / 8 | 8 / 8 |
| 每个回答用时 | 1 到 8 秒 | 1.5 到 4 秒 |
| 数据会不会发出去 | 不会 | 只发脱敏后的问题和公开的 FAQ |

意图识别、向量检索和查账户还是都在本地做，客户的身份和账户数据不会发给 DeepSeek。

## 还存在的问题

- 第一次提问要加载 Qwen、bge-m3 和 reranker，大概要 30 秒。之后每个回答 5 到 7 秒，因为要调三次 DeepSeek（问题重写、HyDE、生成回答）。
- 精排有时候会多留一个不太相关的章节，回答下面的来源里也会列出来。
