# Bank CS 评估报告 20261114_summary

- 评估时间：2026-11-14T09:49:20+08:00
- 代码版本：`48e3d16`（dirty=True）
- 数据集 SHA-256：intent `7198219f2a920374ac6cc2f08f7b7aabad4c4c8aaff5e9d0cdc4f81327b014bb`；FAQ `7b043905978ecb0150f524a4b1e86e1bcaa11f1821f0bc795e795a392a01c649`；API FAQ `9b2d9066d832a7ae2e8d664a1c120cc0f25d2f1afe10a564ee92c7fb4e59eb1b`

阶段运行记录：
- 意图分类与 FAQ 检索：`20261114_093212`
- 安全版 FastAPI 端到端：`20261114_094920`

> 评估审计：首次端到端评估曾误含“账单上一笔看不懂的费用”这条投诉式样本，系统回复创建了演示工单 #32；另有个人汇款查询触发 OTP。已从 API 子集移除这两类话术。安全版 API 评估未创建工单、未触发 OTP。演示工单 #32 未由评估脚本删除。

## 意图分类（本机真实路由器）

- 一级意图准确率：96.2%；宏平均 F1：0.963
- 一级+二级标签完全准确率：92.2%
- 数据：640 条；实际后端：`{'finetuned': 640}`；模型预热/加载 10.576 秒（不计入单条延迟）
- 单条分类耗时：均值 473.39 ms，P50 445.75 ms，P95 713.81 ms

| 语言 | 条数 | 一级准确率 | 完全标签准确率 | Macro-F1 |
|---|---:|---:|---:|---:|
| en | 216 | 96.8% | 92.6% | 0.968 |
| mixed | 220 | 97.3% | 92.7% | 0.973 |
| zh | 204 | 94.6% | 91.2% | 0.948 |

一级意图主要混淆：
- `transfer_howto` → `transfer_status`：4 条
- `transfer_status` → `transfer_howto`：2 条
- `fraud_report` → `card_lost_freeze`：2 条
- `fraud_report` → `dispute_refund`：2 条
- `complaint` → `account_service`：2 条
- `balance_inquiry` → `transfer_howto`：1 条
- `transfer_status` → `card_service`：1 条
- `card_lost_freeze` → `complaint`：1 条

一级正确但二级标签错误/缺失：26 条；完全标签错误样本共 50 条。

## FAQ 来源检索（RAG 实际检索链）

- 预期来源召回率：94.6%（正确来源是否在最终保留章节中）
- Top-1 来源准确率：83.8%（首个保留章节是否正确）
- 知识库外拒答准确率：75.0%（4 条）
- 检索平均耗时：4115.66 ms；成功 41/41，失败 0 条

检索未命中预期来源：
- [zh] 我朋友在美国要汇钱给我，要给他什么资料？；预期 `如何收款`；实际 `如何汇款到海外`
- [en] Can I hold Japanese yen in my account?；预期 `多币种账户支持的货币`；实际 `无`

## FastAPI 端到端时延与引用

- 平均响应时间：5375.56 ms；P50 6334.23 ms；P95 10057.71 ms。计时只包括发送消息到收到答复，不含新建会话。
- API FAQ 来源引用准确率：55.6%；引用出现率：100.0%。实际请求 18/18。

| 语言 | 条数 | 来源引用准确率 | 平均响应时间 |
|---|---:|---:|---:|
| zh | 6/6 | 50.0% | 5299.44 ms |
| en | 6/6 | 50.0% | 5332.12 ms |
| mixed | 6/6 | 66.7% | 5495.13 ms |

未能引用预期 FAQ 来源的端到端样本：
- [zh] PayNow 转账通常会收取手续费吗？；预期 `本地转账费用`；答复片段：PayNow 的手续费：免费  来源：产品资料（术语图谱）
- [zh] 海外电汇有哪些常见手续费？；预期 `海外电汇（TT）手续费`；答复片段：海外电汇的手续费：每笔 SGD 10，中转行可能另扣 SGD 10–30；选 OUR 另加 SGD 20 可全额到账  来源：产品资料（术语图谱）
- [zh] FAST 转账一般多久到账？；预期 `转账一般多久到账`；答复片段：FAST 转账的到账时间：通常几分钟内，全天可用  来源：产品资料（术语图谱）
- [en] What's the usual fee for an overseas TT?；预期 `海外电汇（TT）手续费`；答复片段：Fee for Overseas TT: SGD 10 per transfer; intermediary banks may deduct SGD 10–30; add SGD 20 (OUR) for the full amount  Source: product dat
- [en] How does a Visa foreign currency transaction fee work?；预期 `外币兑换与外币刷卡费用`；答复片段：Foreign transaction fee for Visa Debit Card: 3.25%  Source: product data (knowledge graph)
- [en] How long does an overseas TT usually take?；预期 `转账一般多久到账`；答复片段：Arrival time for Overseas TT: Submitted before 3 pm on a business day: processed same day, usually 1–3 working days, up to 5 for some countr
- [mixed] FAST transfer 通常多久到账？；预期 `转账一般多久到账, 转账方式比较`；答复片段：FAST 转账的到账时间：通常几分钟内，全天可用  来源：产品资料（术语图谱）
- [mixed] PayNow transfer 有手续费吗？；预期 `本地转账费用`；答复片段：PayNow 的手续费：免费  来源：产品资料（术语图谱）

## 指标口径与限制

- 意图集是人工复核的留出集；准确率是一级意图完全相等的比例，Macro-F1 对每个意图等权。
- FAQ 来源指标检查检索保留章节与人工标注的 `expected` 是否相符；API 引用指标检查最终答复引用是否含预期章节标题。这些指标不等于完整的答案事实性评审。
- 混合语言问题按 FAQ 测试集的 `mixed` 标签归组；API 时延子集按每种语言各取固定前 N 条。模型/服务、网络、缓存和机器负载都会影响单次时延。
- 端到端 API 子集只使用一般性 FAQ 问法，不包含个人账户、交易查询、投诉或办理请求；每次请求仍会创建演示会话。
