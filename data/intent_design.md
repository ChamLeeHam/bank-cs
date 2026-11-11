# 意图设计

代码里的意图定义在 src/intents.py，这个文件是说明。

## 怎么划分意图

1. 按系统要怎么处理来分，不按话题分。同样是转账，"TT 一般多久到账？"是一般性的问题，查知识库就行；
   "我昨天的 TT 到了没？"要查客户自己的数据，得先验证 OTP 再查账户。处理方式不一样，所以是两个意图。
2. 每个意图只对应一个分支（account、rag、handoff、refuse）。
3. 标注的人应该几秒钟就能判断出是什么意图，判断不了就把意图合并，或者把规则写清楚。
4. 分两级：一级意图（12 个）决定走哪个分支，二级意图用在检索、统计和评估。gate 只看一级意图。
5. "要求转人工"不算意图，单独用 human_requested 表示，下面会讲。

## 两级意图

一级意图就是下面意图表里的 12 个。二级意图：

- 从 Banking77 来的意图，二级意图直接用 Banking77 原来的标签，数据本身就带着，不用再标
- Banking77 里没有的意图，二级意图是自己定的（见下表），生成数据的时候按二级意图分别生成
- 翻译成中文和中英混杂的句子，二级意图和英文原句一样

微调模型输出的格式是"一级意图/二级意图"，比如 card_service/pin_blocked。一级意图在前面，
所以前面几个 token 的概率就可以当作一级意图的置信度。二级意图错了但一级意图对的话，还是会走对分支，只影响回答的细节。

| 一级意图 | 自己定的二级意图 |
|---|---|
| balance_inquiry | check_balance |
| card_lost_freeze | lost_or_stolen_card（和 Banking77 的标签同名） |
| complaint | waiting_time（等太久）、staff_attitude（服务态度）、app_problem（App 有问题）、unfair_fee（收费不合理） |
| out_of_scope | investment_advice（投资建议）、chitchat（闲聊）、other_company（其他公司）、non_banking（和银行无关） |
| greeting | hello |

一共 12 个一级意图，61 个二级意图，其中 51 个来自 Banking77，10 个是自己定的。
如果时间不够，也可以只用一级意图来训练，数据里的二级意图留着以后再用。

课程 day04 的做法是把所有二级意图放在一个分类器里一起分，一级意图从二级意图查出来，这样不会出现一级分错、二级跟着错的情况。
这里也一样，61 个标签是一个列表，一级意图就是标签前面那部分。
不一样的是这里用的是生成式的模型（Qwen），它有可能输出列表里没有的标签，所以意图识别节点要检查一下输出的标签在不在 ALL_LABELS 里。

## 转人工（human_requested）

"我的卡丢了，帮我转人工"既是挂失，又要求转人工。如果把"要求转人工"当成一个意图（之前有个 complaint/want_human），
模型只能选一个，客户本来要办的事就被丢掉了。所以改成：

- input_guard 用关键词判断（src/intents.py 里的 HUMAN_REQUEST_RE），结果写到 human_requested
- 只放不容易误判的说法：转人工、人工客服、找人工、真人客服、human agent、live agent、speak to a person 等。
  单独一个"人工"不行，"人工智能"也会匹配上；"客服人员"也不行，"投诉你们的客服人员"会被当成要转人工
- 意图照常识别，gate 看到 human_requested 就直接转人工，工单里还能看到客户原本要办什么
- 只说了"转人工"、没有其他内容的句子用规则处理，不放进训练数据

## 追问后客户的回复

客户回答追问的时候经常只说半句，比如"第二笔"、"那张信用卡"，单独看这句话判断不出意图。
课程的做法是先把问题改写成完整的句子再分类，这里简化了一下：上一轮是追问的话，把上一条消息和这一条拼起来再判断（stub_nodes.py 里的 router_text）。
连续追问两次还是不清楚，就转人工。

## 意图表

| 意图 | 中文名 | 说明 | 分支 | 必填信息 | 例子 |
|---|---|---|---|---|---|
| balance_inquiry | 余额查询 | 查自己账户的余额 | account | 无 | 我的余额还有多少？ / How much is in my account? |
| transfer_status | 汇款进度 | 自己的某笔转账到没到、为什么失败 | account | 无（有好几笔的话账户智能体会问） | 我昨天给妈妈的 TT 到了没？ |
| card_lost_freeze | 挂失冻结 | 卡丢了或者被偷了，要冻结 | account | 卡号后四位（有好几张卡的时候） | 我的卡不见了，帮我 freeze |
| fees_rates | 费用汇率 | 手续费、汇率、为什么收这个钱 | rag | 无 | How much is the overseas TT fee? |
| transfer_howto | 转账指南 | 怎么转账、怎么收款、一般多久到 | rag | 无 | TT 一般多久到账？ |
| card_service | 银行卡服务 | 激活、寄卡、PIN、换卡、卡用不了、吞卡 | rag | 无 | How do I activate my card? |
| account_service | 账户服务 | 开户、身份验证、改资料、忘记密码、销户 | rag | 无 | 我要怎么更新地址？ |
| fraud_report | 盗刷举报 | 有不是自己做的交易 | handoff（紧急） | 无 | There's a payment I didn't make! |
| dispute_refund | 争议退款 | 是自己做的交易，但是有问题 | handoff | 无 | 同一笔被扣了两次钱 |
| complaint | 投诉 | 主要是表达不满 | handoff | 无 | 你们的服务太差了，我要投诉 |
| out_of_scope | 超出范围 | 投资建议、闲聊、和银行无关的问题 | refuse | 无 | Which stock should I buy? |
| greeting | 问候 | 打招呼，用规则识别 | refuse（回欢迎语） | 无 | 你好 / Hello |

## 容易分错的几组

| 哪两个容易混 | 怎么区分 |
|---|---|
| fraud_report 和 dispute_refund | 客户没做过这笔交易，是盗刷；做过但有问题，是争议 |
| fraud_report 和 card_lost_freeze | 卡丢了，而且有不认识的交易，算盗刷，更紧急 |
| transfer_status 和 transfer_howto | 说到"我的"、"昨天"、具体金额、某个收款人，是查进度；一般性地问怎么转、多久到、限额多少，是转账指南。问自己已经发出去的某笔转账要多久（"我的转账要多久才成功"），也算查进度 |
| dispute_refund 和 out_of_scope | 商家的退款没到、被扣了两次钱，是争议退款，就算商家是 Grab、foodpanda 也一样；只是问别的公司自己的规定（套餐价格、改机票的费用），才是 out_of_scope/other_company。生成数据里 other_company 提到退款的句子都删掉了 |
| fees_rates 和 dispute_refund | 问收费标准、为什么有这笔费用，是费用汇率；说你们扣错了、汇率给错了，是争议 |
| complaint 和其他 | 主要内容是不满才算投诉。"帮我冻结卡，转人工"还是挂失，再加上要求转人工 |

## Banking77 的标签怎么对应

77 个标签用了 51 个，26 个没用。

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
| balance_inquiry、complaint、out_of_scope、greeting | Banking77 里没有 | 0 | 0 |

没用的标签：
- 电子钱包充值，我们银行没有这个业务：top_up 那一系列、topping_up_by_card、verify_top_up、pending_top_up、automatic_top_up、apple_pay_or_google_pay、supported_cards_and_currencies
- 虚拟卡，不在这次的范围里：disposable_card_limits、get_disposable_virtual_card、getting_virtual_card、virtual_card_not_working
- 系统里没有对应的功能：cancel_transfer、card_linking、exchange_via_app、pending_card_payment、pending_cash_withdrawal、balance_not_updated_after_cheque_or_cash_deposit
- 意思不清楚，放进来会让意图之间的边界变模糊：reverted_card_payment?、verify_source_of_funds、country_support、lost_or_stolen_phone

有几个标签里混着两种意图，标注的时候要一条条看：
transfer_not_received_by_recipient、transfer_timing（查进度还是转账指南），card_payment_fee_charged、extra_charge_on_statement（费用还是争议）

## 数据

计划：
1. 每个意图最多 300 条，在 Banking77 的标签之间平均抽，免得大类太多
2. 用 LLM 生成：card_lost_freeze 补大概 200 条；balance_inquiry、complaint、out_of_scope 各生成 200 到 300 条
3. 所有意图都加上中文和中英混杂的句子，因为 Banking77 只有英文
4. greeting 用规则识别，不生成数据

做出来的数据（src/scripts/build_intent_dataset.py，用 DeepSeek 生成和翻译）：

| 数据集 | 条数 | 英文 / 中文 / 中英混杂 | Banking77 / LLM 生成 | 要重点检查的 |
|---|---|---|---|---|
| train | 3285 | 1097 / 1066 / 1122 | 2175 / 1110 | 1345 |
| test | 648 | 219 / 206 / 223 | 453 / 195 | 242 |

- 11 个意图（greeting 不训练），每个大概 300 条，测试集每个大概 60 条
- 每个意图里三分之一保留英文，三分之一翻译成中文，三分之一改成新加坡那种中英混着说的。翻译出来还留着普通英文单词的"中文句"，改成算中英混杂
- 去掉了重复的句子（不管大小写和标点），测试集和训练集没有重复的，提到别的银行的句子也删掉了（other_company 除外）
- 要重点检查的是 LLM 生成的句子，还有上面说的那 4 个容易混的 Banking77 标签

检查的结果（src/scripts/apply_review.py，结果在 data/intent/reviewed/）：
- 删掉了 25 条和 dispute_refund 冲突的"其他公司退款"的句子，删完以后 train 3265 条，test 643 条
- 测试集里要重点检查的 237 条，是 AI（Claude）一条条检查的：229 条没问题，5 条改了标签，3 条意思不清楚删掉了。
  每一条的处理和原因都记在 data/intent/review/test_review_claude.jsonl 里，最后 test 剩 640 条
- 抽查了一些中文和中英混杂的句子，没发现翻译改变了原意
- 训练集没有一条条检查，只用规则清洗了一下。以后如果在 Label Studio 里检查，把导出的 JSON 放到 data/intent/review/，再跑一下脚本就行

## 微调结果

Qwen2.5-1.5B-Instruct 加 LoRA，rank 16，训练 3 个 epoch，在 RTX 3080 Ti 上 7 到 12 分钟（configs/intent_lora_sft.yaml）。
测试集 640 条，用 src/scripts/score_intent.py 打分，结果在 data/intent/eval/。三个模型用的是同一份改过的测试集标签。

| 模型 | 二级准确率 | 一级准确率 | 二级宏 F1 | 一级宏 F1 | 无效输出 |
|---|---|---|---|---|---|
| 没微调的 Qwen（提示词里列出所有标签） | 45.8% | 65.0% | 0.351 | 0.665 | 18.0% |
| LoRA v1（Banking77 原来的标签） | 92.0% | 95.3% | 0.911 | 0.954 | 0.8% |
| LoRA v2（训练集按转账时间的规则改了 29 条） | 92.5% | 96.2% | 0.910 | 0.963 | 0.5% |

v2 分语言的一级准确率：英文 96.8%，中文 95.1%，中英混杂 96.8%，混着说的和纯英文差不多。

v1 到 v2 改了什么：客户已经有一笔转账在路上（"我的"、"已经发了"、"在等"、"the transfer"），算查进度；
一般性地问或者问还没发的转账要多久，算转账指南。Banking77 的 transfer_timing、pending_transfer 这些标签没按这个规则分，
所以训练集改了 29 条，测试集改了 7 条（记录在 data/intent/review/ 里名字带 timing_rule 的文件）。
改完以后，把查进度错分成转账指南的从 4 条降到 1 条，一级准确率高了 0.9 个百分点。
不过每个版本只训练了一次，只差 6 条，也可能是随机的波动。

v2 的错误分析：
- 一级 96.2%，二级 92.5%，差的 3.7 个百分点是分错了二级意图但一级是对的，比如 card_service 里面的几个二级意图分混了，不影响走哪个分支
- 走错分支的有 24 条，分散在盗刷、争议、费用这几个容易混的意图之间，gate 的置信度判断和追问可以兜一下
- 剩下的转账类的"错误"，很多其实是 Banking77 原来的标签有问题。比如"我刚才想做的账户转账失败了"标的是 beneficiary_not_allowed，
  "我的信用卡被拒了"标的是 declined_transfer，模型判断成 failed_transfer 和 card_service 反而更合理
- 有 3 条（0.5%）输出了不存在的标签，比如 complaint/other，所以意图识别节点要检查一下输出的标签
- 测试集和训练集来自一样的数据，翻译也是同一个 LLM 做的，真实客户的问题准确率应该会低一些

## 接到意图识别节点（src/nodes/intent_router.py）

参考课程 day04 的 IntentClassifyNode，先用规则判断，判断不了再用模型：

1. 问候（你好、hi）直接判断成 greeting；"嗯嗯"、"那个"、"ok"这种没有内容的消息判断成 unknown，
   因为训练数据里没有这种句子，交给模型的话它会随便选一个
2. 用微调的模型。先只看这一条消息，如果上一轮是追问、这一条又不太确定，就和上一条拼起来再判断一次，取更确定的那个。
   不直接拼起来，是因为客户追问以后可能会直接问一个新问题
3. 置信度是一级意图那几个 token 的概率乘起来，也就是模型生成这个一级意图的概率
4. 输出的标签不在 ALL_LABELS 里的话，一级意图对就保留一级，二级空着；一级也不对就当成 unknown，去追问
5. 没有 GPU 或者没有模型文件的时候，自动用关键词规则。INTENT_BACKEND=keyword 可以强制用规则

速度：模型第一次加载大概 8 秒，之后每条消息 0.3 到 0.7 秒。

阈值是在 640 条测试集上测出来的（data/intent/eval/test_confidence.json）：

| intent_threshold | 直接回答的比例 | 直接回答里答对的 | 答错的条数 | 本来答对了却被追问的 |
|---|---|---|---|---|
| 0.5（用这个） | 98.0% | 97.4% | 16 | 5 |
| 0.6 | 95.8% | 97.7% | 14 | 17 |
| 0.7（一开始的值） | 92.8% | 98.0% | 12 | 34 |

- 用 0.7 比 0.5 少答错 4 条，但是多追问了 29 条本来就对的问题。答错的影响不大，查账户还要 OTP，知识库的回答也带来源，所以用 0.5
- 置信度很高却"答错"的几条，很多是 Banking77 标签的问题，比如 "Can I freeze my card right now?" 标的是 fraud_report
- clarify_threshold 用 0.3，低于 0.3 的消息只有 0.5%，直接转人工
- 课程里提到还可以每个意图单独定阈值，比如盗刷的阈值低一点、更早转人工，有时间的话可以再做

数据来源：Banking77（Casanueva et al., 2020），PolyAI，CC-BY-4.0
