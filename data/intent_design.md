# 意图设计

代码中的唯一来源：`src/intents.py`（本文件是说明文档）。

## 设计原则

1. **按"系统怎么处理"划分意图，而不是按话题。** 同样是转账：
   "TT 一般多久到账？"（一般问题 → 查知识库）和"我昨天的 TT 到了没？"（本人数据 → OTP + 账户工具）处理方式不同，所以是两个意图。
2. 每个意图只对应一个分支（account / rag / handoff / refuse）。
3. 标注员 5 秒内能判断出意图；判断不了就合并意图或写清边界规则。
4. **两级意图**：一级意图（12 个）决定分支；二级意图决定细节（RAG 检索哪一节、看板统计、评估）。gate 只看一级意图。
5. **"要求转人工"不是意图**，而是独立信号 `human_requested`（见下文）。

## 两级意图

- **一级意图**：下面的意图表，共 12 个
- **二级意图**：
  - 从 Banking77 映射来的意图：二级意图 = 原始 Banking77 标签（数据自带，不需要额外标注）
  - Banking77 中没有的意图：手动定义（见下表），LLM 按二级意图分别生成数据
  - 翻译成中文 / 中英混杂的样本沿用英文原句的二级意图
- **微调模型输出格式**：`一级意图/二级意图`，例如 `card_service/pin_blocked`
  - 输出以一级意图开头，所以前几个 token 的概率就是一级意图的置信度（gate 需要的分数）
  - 二级意图判断错但一级正确时，分支仍然正确，只影响回答细节

| 一级意图 | 手动定义的二级意图 |
|---|---|
| balance_inquiry | check_balance |
| card_lost_freeze | lost_or_stolen_card（与 Banking77 同名，LLM 补充的数据也用它） |
| complaint | waiting_time（等待太久）、staff_attitude（服务态度）、app_problem（App 问题）、unfair_fee（收费不合理） |
| out_of_scope | investment_advice（投资建议）、chitchat（闲聊）、other_company（其他公司）、non_banking（与银行无关） |
| greeting | hello |

**降级方案**：如果时间不够，只用一级意图训练；数据中保留了二级意图，之后可以直接补上，不需要重新标注。

共 12 个一级意图、61 个二级意图（51 个来自 Banking77，10 个手动定义）。

**与课程方案（day04 BERT 意图分类）的对应**：课程把 12 个二级意图放在同一个分类器里（扁平分类），一级意图由二级查表得到，
避免"先分一级、再分二级"的级联误差。本项目同理：61 个标签是一个扁平列表，一级意图就是标签的前缀。
不同之处是本项目用生成式模型（Qwen + LLaMA Factory），所以：
- 模型可能输出不在列表中的标签 → 路由节点必须用 `ALL_LABELS` 校验，不合法时降级（规则 / 追问）
- 输出的是标签文本而不是编号，标签顺序不影响模型；但仍需把标签列表和版本号与模型一起保存，避免模型和代码的标签不一致

## 转人工请求（human_requested）

"我的卡丢了，帮我转人工"既是 `card_lost_freeze`，又要求人工。如果把"要求人工"做成一个意图（之前的 `complaint/want_human`），
模型只能二选一，客户原本要办的事就丢了（课程 day04 1.1.1 指出这是最常见的建模错误）。

- 由 `input_guard` 用关键词规则判断（`src/intents.py` 的 `HUMAN_REQUEST_RE`），写入 `human_requested`
- 只放几乎不会误判的说法：转人工 / 人工客服 / 找人工 / 真人客服 / human agent / live agent / speak to a person …
  （单独的"人工"不算，会命中"人工智能"；"客服人员"不算，会命中"投诉你们的客服人员"）
- 意图照常识别，gate 看到 `human_requested` 直接转人工；工单里仍记录客户原本的意图
- 只说"转人工"、没有其他内容的句子由规则处理，**不放进训练数据**

## 追问后的回复（上下文拼接）

课程的分类器输入是"改写后的完整问句"，因为客户的回复常常只是碎片（"第二笔"、"那张信用卡"）。
本项目用简化做法：上一轮是追问（`clarify_count > 0`）时，把上一条客户消息和本条拼起来再分类
（`src/nodes/stub_nodes.py` 的 `router_text()`，Day 3 微调模型复用）。
连续追问 2 次仍不明确时转人工（《需求文档》F9）。

## 意图表（12 个）

| 意图 | 中文名 | 说明 | 分支 | 必填槽位 | 示例 |
|---|---|---|---|---|---|
| balance_inquiry | 余额查询 | 查询本人账户余额 | account | — | 我的余额还有多少？ / How much is in my account? |
| transfer_status | 汇款进度 | 本人某笔转账是否到账、为什么失败 | account | —（有多笔时账户智能体追问） | 我昨天给妈妈的 TT 到了没？ |
| card_lost_freeze | 挂失冻结 | 卡丢失/被盗，需要冻结 | account | card_last4（客户有多张卡时） | 我的卡不见了，帮我 freeze |
| fees_rates | 费用汇率 | 手续费、汇率、为什么收费 | rag | — | How much is the overseas TT fee? |
| transfer_howto | 转账指南 | 怎么转账/收款、一般多久到账 | rag | — | TT 一般多久到账？ |
| card_service | 银行卡服务 | 激活、寄送、PIN、换卡、卡不能用、吞卡 | rag | — | How do I activate my card? |
| account_service | 账户服务 | 开户、身份验证、改资料、忘记密码、销户 | rag | — | 我要怎么更新地址？ |
| fraud_report | 盗刷举报 | 有不是本人做的交易 | handoff（urgent） | — | There's a payment I didn't make! |
| dispute_refund | 争议退款 | 本人做的交易但有问题 | handoff | — | 同一笔被扣了两次钱 |
| complaint | 投诉 | 主要表达不满（要求人工用 human_requested 判断） | handoff | — | 你们的服务太差了，我要投诉 |
| out_of_scope | 超出范围 | 投资建议、闲聊、无关问题 | refuse | — | Which stock should I buy? |
| greeting | 问候 | 打招呼（规则识别） | refuse（欢迎语） | — | 你好 / Hello |

## 边界规则

| 容易混淆 | 规则 |
|---|---|
| fraud_report vs dispute_refund | 客户**没有做过**这笔交易 → fraud；**做过**但有问题 → dispute |
| fraud_report vs card_lost_freeze | 卡丢了**并且**有陌生交易 → fraud（更紧急） |
| transfer_status vs transfer_howto | 提到**我的 / 昨天 / 具体金额 / 某个收款人** → status；一般性的"怎么 / 多久 / 限额" → howto。问**已经发出的某笔**转账要多久（"我的转账要多久才成功"）也是 status |
| dispute_refund vs out_of_scope | 商家退款**没到账 / 被扣两次** → dispute_refund（即使商家是 Grab、foodpanda）；只问其他公司自己的政策（套餐价格、改机票费用）→ out_of_scope/other_company。生成的 other_company 数据中涉及退款的句子已全部删除 |
| fees_rates vs dispute_refund | "收费标准是什么 / 为什么有这笔费用" → fees；"你们扣错了 / 汇率给错了" → dispute |
| complaint vs 其他 | 只有当主要内容是不满时才是 complaint；"帮我冻结卡，转人工"仍是 card_lost_freeze + human_requested |

## Banking77 映射（51 个标签使用，26 个丢弃）

| 意图 | Banking77 标签 | 训练集 | 测试集 |
|---|---|---|---|
| card_service | activate_my_card, atm_support, card_about_to_expire, card_acceptance, card_arrival, card_delivery_estimate, card_not_working, card_swallowed, change_pin, contactless_not_working, declined_card_payment, declined_cash_withdrawal, get_physical_card, getting_spare_card, order_physical_card, pin_blocked, visa_or_mastercard | 1960 | 680 |
| dispute_refund | Refund_not_showing_up, request_refund, transaction_charged_twice, wrong_amount_of_cash_received, card_payment_wrong_exchange_rate, wrong_exchange_rate_for_cash_withdrawal | 1016 | 240 |
| fees_rates | card_payment_fee_charged, cash_withdrawal_charge, exchange_charge, exchange_rate, extra_charge_on_statement, transfer_fee_charged | 935 | 240 |
| account_service | age_limit, edit_personal_details, fiat_currency_support, passcode_forgotten, terminate_account, unable_to_verify_identity, verify_my_identity, why_verify_identity | 897 | 320 |
| transfer_status | balance_not_updated_after_bank_transfer, declined_transfer, failed_transfer, pending_transfer, transfer_not_received_by_recipient | 760 | 200 |
| fraud_report | card_payment_not_recognised, cash_withdrawal_not_recognised, compromised_card, direct_debit_payment_not_recognised | 596 | 160 |
| transfer_howto | beneficiary_not_allowed, receiving_money, transfer_into_account, transfer_timing | 492 | 160 |
| card_lost_freeze | lost_or_stolen_card | 82 | 40 |
| balance_inquiry, complaint, out_of_scope, greeting | （Banking77 中没有） | 0 | 0 |

**丢弃的标签及原因：**
- 电子钱包充值（本行没有该业务）：top_up_* 系列、topping_up_by_card、verify_top_up、pending_top_up、automatic_top_up、apple_pay_or_google_pay、supported_cards_and_currencies
- 虚拟卡（不在范围内）：disposable_card_limits、get_disposable_virtual_card、getting_virtual_card、virtual_card_not_working
- 系统没有对应功能：cancel_transfer、card_linking、exchange_via_app、pending_card_payment、pending_cash_withdrawal、balance_not_updated_after_cheque_or_cash_deposit
- 含义不清、会模糊边界：reverted_card_payment?、verify_source_of_funds、country_support、lost_or_stolen_phone

**需要在 Label Studio 中逐条检查的标签**（同一标签里混有两种意图）：
transfer_not_received_by_recipient、transfer_timing（status / howto）；card_payment_fee_charged、extra_charge_on_statement（fees / dispute）

## 数据计划

1. 每个意图最多取 300 条（在意图内部按 Banking77 标签均匀抽样），避免大类压倒小类
2. LLM 生成：card_lost_freeze 补约 200 条；balance_inquiry、complaint、out_of_scope 各 200–300 条
3. 所有意图都补充中文和中英混杂的样本（Banking77 只有英文）
4. greeting 用规则识别，不生成训练数据

**构建结果**（`src/scripts/build_intent_dataset.py`，DeepSeek 生成 / 翻译，结果缓存在 `data/intent/cache/`）：

| 数据集 | 条数 | en / zh / mixed | Banking77 / LLM 生成 | 需重点检查 |
|---|---|---|---|---|
| train | 3285 | 1097 / 1066 / 1122 | 2175 / 1110 | 1345 |
| test | 648 | 219 / 206 / 223 | 453 / 195 | 242 |

- 每个一级意图约 300 条（测试集约 60 条），11 个意图（greeting 用规则，不训练）
- 每个意图内 1/3 保持英文、1/3 翻译成中文、1/3 改写成新加坡式中英混杂；翻译时保留了普通英文单词的"中文句"改标为 mixed
- 清洗：去重（忽略大小写和标点）、测试集与训练集不重复、删除提到其他银行的句子（other_company 除外）
- "需重点检查"：LLM 生成的句子 + 容易混淆的 4 个 Banking77 标签；Label Studio 中按 `review` 字段筛选

**检查结果**（`src/scripts/apply_review.py` 应用到 `data/intent/reviewed/`，按句子原文匹配，数据重建后仍有效）：
- 重建后 train 3265 条、test 643 条（删除了 25 条与 dispute_refund 冲突的"其他公司退款"句子）
- 测试集 237 条需重点检查的句子由 AI（Claude）逐条检查：229 条正确，5 条改标签（4 条"我的转账要多久" howto → status，1 条二级意图修正），3 条意图不明确删除；
  决定记录在 `data/intent/review/test_review_claude.jsonl`（每条都有原因），最终 test 640 条
- 翻译质量：抽查的中文 / 中英混杂句子没有发现改变原意的翻译
- 训练集未逐条检查（1345 条），只用规则清洗；Label Studio 导出的 JSON 放进 `data/intent/review/` 后重新运行脚本即可合并

## 微调结果（Day 3）

Qwen2.5-1.5B-Instruct + LoRA（rank 16，3 epoch，RTX 3080 Ti 训练 7–12 分钟），`configs/intent_lora_sft.yaml`；
测试集 640 条（`data/intent/reviewed/test.jsonl`），评分脚本 `src/scripts/score_intent.py`，结果在 `data/intent/eval/`。

三个模型都按**同一份修正后的测试集标签**评分（`intent_lora_v1` 的预测文件按新标签重新评分）：

| 模型 | 二级准确率 | 一级准确率 | 二级宏 F1 | 一级宏 F1 | 无效输出 |
|---|---|---|---|---|---|
| 基座模型（零样本，提示词列出全部标签） | 45.8% | 65.0% | 0.351 | 0.665 | 18.0% |
| LoRA v1（Banking77 原标签） | 92.0% | 95.3% | 0.911 | 0.954 | 0.8% |
| **LoRA v2（训练集按转账时间规则修正 29 条）** | **92.5%** | **96.2%** | **0.910** | **0.963** | **0.5%** |

按语言（v2，一级准确率）：en 96.8% / zh 95.1% / mixed 96.8%，中英混杂与纯英文持平。

**v1 → v2：统一转账时间规则**
- 规则：客户已经有一笔转账在途中（我的 / 已发出 / 在等 / the transfer）→ transfer_status；一般性或未来的转账问多久 → transfer_howto
- Banking77 的 transfer_timing 和 pending_transfer 等标签没有按这个规则区分，所以训练集改了 29 条、测试集改了 7 条
  （决定记录在 `data/intent/review/*timing_rule_claude.jsonl`）
- 效果：status → howto 的错误从 4 条降到 1 条，一级准确率 +0.9 个百分点；但每个版本只训练了一次，差距只有 6 条样本，不能排除随机波动

**分析（v2）**：
- 一级 96.2% vs 二级 92.5%：约 3.7 个百分点的错误留在同一分支内（例如 card_service 内部的二级意图混淆），不影响分流
- 进错分支的错误共 24 条，分散在 fraud_report / dispute_refund / fees_rates 等边界上；gate 的置信度分流和追问可以兜底
- 剩下的转账类"错误"多数是 Banking77 原始标签的问题：如"我刚才想做的账户转账失败了"标为 beneficiary_not_allowed、
  "我的信用卡被拒了"标为 declined_transfer，模型的判断（failed_transfer / card_service）更合理
- 无效输出 3 条（0.5%），如 `complaint/other`：路由节点必须用 `ALL_LABELS` 校验，一级意图有效时保留一级、否则追问
- 局限：测试集与训练集来自同样的数据源和同一个 LLM 翻译，真实客户消息的准确率会更低

## 接入意图路由节点（`src/nodes/intent_router.py`）

处理顺序（参考课程 day04 的 IntentClassifyNode：规则前置短路，模型兜底）：
1. **规则短路**：问候（你好 / hi）→ greeting；没有内容的消息（嗯嗯 / 那个 / ok）→ unknown（训练数据里没有这类句子，模型会硬选一个意图）
2. **微调模型**：先只分类本轮消息；上一轮是追问、且本轮单独看不够确定时，再拼接上一条消息分类一次，取更确定的结果
   （避免把追问后客户提出的完整新问题和上一句拼在一起而判错）
3. **置信度**：一级意图部分各 token 概率的乘积，即模型生成该一级意图的概率
4. **输出校验**：不在 `ALL_LABELS` 中时，一级合法则保留一级、二级置空；否则 unknown → 追问
5. **降级**：没有 torch / GPU / LoRA 文件时自动改用关键词规则；`INTENT_BACKEND=keyword` 可强制使用规则；`router_backend` 记录实际方式

速度（RTX 3080 Ti）：模型首次加载约 8 秒，之后每条消息 0.3–0.7 秒。

**阈值校准**：在 640 条测试集上记录置信度（`data/intent/eval/test_confidence.json`）：

| intent_threshold | 直接回答的比例 | 其中正确率 | 答错的条数 | 本来正确却被追问 |
|---|---|---|---|---|
| **0.5（采用）** | 98.0% | 97.4% | 16 | 5 |
| 0.6 | 95.8% | 97.7% | 14 | 17 |
| 0.7（Day 1 的默认值） | 92.8% | 98.0% | 12 | 34 |

- 0.7 比 0.5 少答错 4 条，却多追问 29 条本来正确的问题；答错的代价有限（账户操作仍需 OTP，RAG 回答附来源），所以采用 0.5
- 置信度最高的几条"错误"多是 Banking77 标签问题（如 "Can I freeze my card right now?" 标为 fraud_report）
- clarify_threshold 设为 0.3：低于 0.3 的消息只占 0.5%，直接转人工
- 课程提到更细的做法是按意图分别定阈值（如盗刷类更低的阈值、更早转人工），时间允许时可以补充

数据来源：Banking77（Casanueva et al., 2020），PolyAI，CC-BY-4.0
