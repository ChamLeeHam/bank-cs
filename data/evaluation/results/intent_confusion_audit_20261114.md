# 意图混淆样本人工复核

评估来源：`evaluation_20261114_184141.json`，本机微调路由器，留出集 640 条。这里只审查与汇款、盗刷相关的一级意图混淆；没有修改已复核测试集或模型。

## 优先级最高：疑似评估标签不一致

| ID | 输入 | 当前标注 | 模型输出 | 复核意见 |
|---|---|---|---|---|
| `test-00250` | 我刚才想做的账户转账失败了。 | `transfer_howto / beneficiary_not_allowed` | `transfer_status / failed_transfer` | 输入明确说某笔本人转账失败，现有 howto/收款人限制标签与意图定义不符。建议人工确认后改标为 `transfer_status / failed_transfer`。 |
| `test-00280` | 我有一笔加密货币兑换一直过不了，请帮我处理一下。 | `transfer_howto / beneficiary_not_allowed` | `transfer_status / balance_not_updated_after_bank_transfer` | 加密货币兑换不是银行转账或收款人限制问题；当前标注和预测都没有合理覆盖。应先决定该样本是否属于 `out_of_scope`，或从银行转账意图集移除。 |
| `test-00419` | I didn't get cash from an ATM, but the app says I did | `fraud_report / cash_withdrawal_not_recognised` | `dispute_refund / wrong_amount_of_cash_received` | “ATM 没吐现金但账户记账”描述的是未收到现金的提款争议，不足以证明盗刷。按当前意图定义，更像 `dispute_refund / wrong_amount_of_cash_received`；疑似标签错误。 |

## 输入含义有歧义，建议改写后再用于评估

| ID | 输入 | 当前标注 | 模型输出 | 复核意见 |
|---|---|---|---|---|
| `test-00090` | 为什么我转不了账？ | `transfer_status / declined_transfer` | `transfer_howto / beneficiary_not_allowed` | 没有具体交易编号或失败记录。可以理解为一般转账限制，也可以理解为某笔交易被拒；需在数据中明确“某笔交易被拒”或“怎样添加/使用收款人”。 |
| `test-00091` | 我好像一直做不到 successful transfer，你可以跟我讲我哪里做错了吗？ | `transfer_status / failed_transfer` | `transfer_howto / beneficiary_not_allowed` | “一直做不到”像一般操作/资格问题，而非单笔交易状态。建议明确写成“我刚刚这笔转账显示 failed”以测试 status；若想测 howto，则去掉已失败交易语境。 |
| `test-00104` | How long for money transfer to show? | `transfer_howto / transfer_timing` | `transfer_status / balance_not_updated_after_bank_transfer` | 缺少“通常多久”或“我这笔”的限定，通用耗时与个人到账查询都说得通。 |
| `test-00289` | 转账多久会显示在我的账户里？ | `transfer_howto / transfer_timing` | `transfer_status / balance_not_updated_after_bank_transfer` | “我的账户里”容易触发个人到账查询。若测试通用知识，建议改为“通常转账多久到账？”；若是个人交易，应补充“我刚才那笔”。 |

## 需要修正分类边界/路由的真实错误

| ID | 输入 | 当前标注 | 模型输出 | 风险 |
|---|---|---|---|---|
| `test-00439` | I didn't take out money from an ATM but my app statement shows that I did. How can I fix this? | `fraud_report / cash_withdrawal_not_recognised` | `dispute_refund / wrong_amount_of_cash_received` | 明确表示本人没有取现，应该按未授权交易进入盗刷举报；模型误判为提款金额/未收到现金争议。两个分支都转人工，但盗刷分支标记 `urgent`，所以仍是安全路由问题。 |
| `test-00445` | ...noticed a pretty large amount that was not completed by me... | `fraud_report / direct_debit_payment_not_recognised` | `dispute_refund / transaction_dispute` | “not completed by me”明确表示非本人交易；模型被“disputing a charge”带偏。该样本走 `keyword` 后端，说明需要检查未授权交易关键词/优先级规则。盗刷紧急级别可能丢失。 |
| `test-00449` | 有人偷了我的卡。虽然我的卡还在我身上，但刚刚有人提了500英镑现金。请尽快帮我。 | `fraud_report / cash_withdrawal_not_recognised` | `card_lost_freeze / lost_or_stolen_card` | “卡仍在本人身上”与字面上的丢卡冻结不同，未授权取现应优先进入盗刷举报。当前预测仍是账户类操作，可能要求补卡片信息/OTP，需确保不会覆盖紧急盗刷路径。 |
| `test-00413` | Can I freeze my card right now? | `fraud_report / compromised_card` | `card_lost_freeze / lost_or_stolen_card` | 单独询问冻结卡片没有说明盗刷或卡片泄露；模型的挂失冻结分类可能比现有标注更符合意图定义。建议把标注改为 `card_lost_freeze`，或补充“发现未授权扣款”来测盗刷。 |

## 路由影响与建议

- `transfer_howto` 走 RAG，`transfer_status` 走账户查询（需要 OTP）。对“通用转账时效”和“我的某笔转账状态”必须用明确措辞区分，避免把一般问题错误升级为身份验证流程。
- `fraud_report` 走紧急人工工单；`dispute_refund` 也转人工，但不是紧急级别。所有“不是我做的 / I did not make or authorize this transaction”应优先匹配盗刷；“我做了这笔但金额、现金或汇率不对”才归争议退款。
- 先经人工复核并确认上表疑似标注错误，再另行更新留出集并重跑评估。否则贸然改标签会让准确率变化无法区分是模型改进还是测试集口径变化。
