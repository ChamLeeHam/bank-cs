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
检索的时候稠密向量和 Milvus 自带的 BM25 一起用，BM25 用 jieba 分词。
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
| 混合检索 | search_embedding_utils.py | 稠密向量加 BM25 |
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
| A 只用混合检索 | 87.5% | 93.8% | 0.914 |
| B 加上问题重写 | 90.6% | 96.9% | 0.938 |
| C 再加上 HyDE 和 RRF | 93.8% | 100% | 0.958 |
| D 再加上 reranker（完整流程） | 81.2% | 100% | 0.901 |

完整流程下，有答案的问题 93.8% 留下了正确的章节，没答案的 4 个问题都判断出来了。平均留 1.4 个章节，检索大概 4 秒。
问题重写和 HyDE 每次生成的内容会有一点不一样，所以每次跑出来的数字会有小的变化，比如 D 的 Hit@1 跑过 84.4% 也跑过 81.2%。

- 问题重写和 HyDE 都有帮助，Hit@1 从 87.5% 提高到 93.8%。
- 加了 reranker 以后 Hit@3 到了 100%，但 Hit@1 反而降了。bge-reranker-base 对这种短的中英双语章节判断得不太准，
  比如"PayNow 转账要收钱吗？"它把"如何收款"排在了"本地转账费用"前面。不过一般会留 1 到 3 个章节，回答的时候还是能拿到正确的那个。
- reranker 最大的用处是判断知识库里有没有答案。没答案的问题最高分都不超过 0.0043，有答案的问题正确章节基本都大于 0.01。
- 以后可以试试 bge-reranker-v2-m3，它对多语言的支持更好。

RAGAS 的结果（评判模型 DeepSeek，向量 bge-m3，31 条回答，eval/ragas_eval.json）：

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
