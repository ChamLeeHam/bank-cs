# 意图设计

代码中的唯一来源：`src/intents.py`（本文件是说明文档）。

## 设计原则

1. **按"系统怎么处理"划分意图，而不是按话题。** 同样是转账：
   "TT 一般多久到账？"（一般问题 → 查知识库）和"我昨天的 TT 到了没？"（本人数据 → OTP + 账户工具）处理方式不同，所以是两个意图。
2. 每个意图只对应一个分支（account / rag / handoff / refuse）。
3. 标注员 5 秒内能判断出意图；判断不了就合并意图或写清边界规则。
4. **两级意图**：一级意图（12 个）决定分支；二级意图决定细节（RAG 检索哪一节、看板统计、评估）。gate 只看一级意图。

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
| complaint | waiting_time（等待太久）、staff_attitude（服务态度）、app_problem（App 问题）、unfair_fee（收费不合理）、want_human（要求人工） |
| out_of_scope | investment_advice（投资建议）、chitchat（闲聊）、other_company（其他公司）、non_banking（与银行无关） |
| greeting | hello |

**降级方案**：如果时间不够，只用一级意图训练；数据中保留了二级意图，之后可以直接补上，不需要重新标注。

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
| complaint | 投诉 | 表达不满、要求人工 | handoff | — | 你们的服务太差了，我要投诉 |
| out_of_scope | 超出范围 | 投资建议、闲聊、无关问题 | refuse | — | Which stock should I buy? |
| greeting | 问候 | 打招呼（规则识别） | refuse（欢迎语） | — | 你好 / Hello |

## 边界规则

| 容易混淆 | 规则 |
|---|---|
| fraud_report vs dispute_refund | 客户**没有做过**这笔交易 → fraud；**做过**但有问题 → dispute |
| fraud_report vs card_lost_freeze | 卡丢了**并且**有陌生交易 → fraud（更紧急） |
| transfer_status vs transfer_howto | 提到**我的 / 昨天 / 具体金额 / 某个收款人** → status；一般性的"怎么 / 多久 / 限额" → howto |
| fees_rates vs dispute_refund | "收费标准是什么 / 为什么有这笔费用" → fees；"你们扣错了 / 汇率给错了" → dispute |
| complaint vs 其他 | 只有当主要内容是不满或要求人工时才是 complaint |

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

数据来源：Banking77（Casanueva et al., 2020），PolyAI，CC-BY-4.0
