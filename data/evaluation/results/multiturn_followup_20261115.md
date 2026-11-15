# 多轮对话实时回归（新卡寄送 FAQ）

- 独立会话：5
- 总轮次：13
- 通过：13/13
- 接口平均耗时：3836 ms
- 测试环境：本机 FastAPI、真实检索和回答链路（`http://127.0.0.1:8002`）
- 测试只询问 FAQ，没有执行账户冻结或工单创建等写操作。

| 对话 | 用户问题 | 耗时 | 答案 | 来源 | 结果 |
|---|---|---:|---|---|---|
| 连续追问补发卡与超时处理 | 新卡一般几天寄到？ | 3679 ms | 通过 | 通过 | 通过 |
| 连续追问补发卡与超时处理 | 那补发卡也一样吗？ | 5126 ms | 通过 | 通过 | 通过 |
| 连续追问补发卡与超时处理 | 超过10个工作日还没收到呢？ | 4091 ms | 通过 | 通过 | 通过 |
| 切换主题后回到寄送问题 | 我申请的新卡还没到，大概多久？ | 3418 ms | 通过 | 通过 | 通过 |
| 切换主题后回到寄送问题 | 顺便问下，PayNow转账收费吗？ | 2704 ms | 通过 | 通过 | 通过 |
| 切换主题后回到寄送问题 | 那新卡超过10个工作日还没收到怎么办？ | 5346 ms | 通过 | 通过 | 通过 |
| 插入无关问题后追问寄送期限 | 补办的银行卡几天能寄到？ | 4322 ms | 通过 | 通过 | 通过 |
| 插入无关问题后追问寄送期限 | 谢谢。顺便问，借记卡可以申请附属卡吗？ | 3584 ms | 通过 | 通过 | 通过 |
| 插入无关问题后追问寄送期限 | 刚才那张补办卡如果超过10个工作日呢？ | 4176 ms | 通过 | 通过 | 通过 |
| 地址限制后追问处理时限 | 新卡可以寄到新加坡以外吗？ | 3542 ms | 通过 | 通过 | 通过 |
| 地址限制后追问处理时限 | 那超过10个工作日没收到呢？ | 3252 ms | 通过 | 通过 | 通过 |
| 英文多轮追问 | How long does a replacement card take to arrive? | 3524 ms | 通过 | 通过 | 通过 |
| 英文多轮追问 | What if it still has not arrived after 10 working days? | 3100 ms | 通过 | 通过 | 通过 |

## 回复与来源

### 1. 连续追问补发卡与超时处理：新卡一般几天寄到？

新卡或补发的卡一般会在 5–7 个工作日内以挂号信寄到您登记的新加坡地址，您可以在 App 中查看寄送进度。如果超过 10 个工作日仍未收到，请联系我们补寄。

**来源：** [{"doc": "lcb__faq__card_services", "title": "银行卡服务 / Card services", "section": "新卡寄送时间 / Card delivery time", "rerank_score": 0.9968, "label": "《银行卡服务》新卡寄送时间"}]

### 2. 连续追问补发卡与超时处理：那补发卡也一样吗？

是的，补发的卡同样会在 5–7 个工作日内以挂号信寄到您登记的新加坡地址，您可以在 App 中查看寄送进度。若超过 10 个工作日仍未收到，请联系我们补寄。

**来源：** [{"doc": "lcb__faq__card_services", "title": "银行卡服务 / Card services", "section": "新卡寄送时间 / Card delivery time", "rerank_score": 0.991, "label": "《银行卡服务》新卡寄送时间"}]

### 3. 连续追问补发卡与超时处理：超过10个工作日还没收到呢？

建议您尽快联系我行客服申请补寄，同时可在 App 中查看寄送进度确认卡片状态。资料未说明其他处理方式。

**来源：** [{"doc": "lcb__faq__card_services", "title": "银行卡服务 / Card services", "section": "新卡寄送时间 / Card delivery time", "rerank_score": 0.9981, "label": "《银行卡服务》新卡寄送时间"}]

### 4. 切换主题后回到寄送问题：我申请的新卡还没到，大概多久？

新卡一般会在 5–7 个工作日内以挂号信寄到您登记的新加坡地址，您可以在 App 中查看寄送进度。如果超过 10 个工作日仍未收到，请联系我们安排补寄。

**来源：** [{"doc": "lcb__faq__card_services", "title": "银行卡服务 / Card services", "section": "新卡寄送时间 / Card delivery time", "rerank_score": 0.9137, "label": "《银行卡服务》新卡寄送时间"}]

### 5. 切换主题后回到寄送问题：顺便问下，PayNow转账收费吗？

PayNow 的手续费：免费

来源：产品资料（术语图谱）

**来源：** [{"doc": "neo4j", "title": "术语图谱 / Product graph", "section": "PayNow", "rerank_score": null}]

### 6. 切换主题后回到寄送问题：那新卡超过10个工作日还没收到怎么办？

超过 10 个工作日仍未收到新卡，请联系银行安排补寄。新卡或补发的卡会以挂号信寄到您登记的新加坡地址，您也可以在 App 中查看寄送进度。目前不支持寄送到新加坡以外的地址。

**来源：** [{"doc": "lcb__faq__card_services", "title": "银行卡服务 / Card services", "section": "新卡寄送时间 / Card delivery time", "rerank_score": 0.9891, "label": "《银行卡服务》新卡寄送时间"}]

### 7. 插入无关问题后追问寄送期限：补办的银行卡几天能寄到？

补办的新卡一般会在 5–7 个工作日内以挂号信寄到您登记的新加坡地址，您可在 App 查看寄送进度。若超过 10 个工作日仍未收到，请联系银行补寄；目前不支持寄到新加坡以外地址。

**来源：** [{"doc": "lcb__faq__card_services", "title": "银行卡服务 / Card services", "section": "新卡寄送时间 / Card delivery time", "rerank_score": 0.9977, "label": "《银行卡服务》新卡寄送时间"}]

### 8. 插入无关问题后追问寄送期限：谢谢。顺便问，借记卡可以申请附属卡吗？

借记卡不提供附属卡，且每个账户只能关联一张借记卡；如需为家人办附属卡，信用卡可为18岁以上家人申请，首两张免年费。

**来源：** [{"doc": "lcb__faq__card_services", "title": "银行卡服务 / Card services", "section": "补发实体卡与附属卡 / Replacement and supplementary cards", "rerank_score": 0.9472, "label": "《银行卡服务》补发实体卡与附属卡"}]

### 9. 插入无关问题后追问寄送期限：刚才那张补办卡如果超过10个工作日呢？

超过 10 个工作日仍未收到，请联系银行补寄。您也可以在 App 查看寄送进度；目前不支持将卡寄到新加坡以外的地址。

**来源：** [{"doc": "lcb__faq__card_services", "title": "银行卡服务 / Card services", "section": "新卡寄送时间 / Card delivery time", "rerank_score": 0.6914, "label": "《银行卡服务》新卡寄送时间"}]

### 10. 地址限制后追问处理时限：新卡可以寄到新加坡以外吗？

不可以，目前不支持把新卡寄到新加坡以外的地址。新卡或补发的卡会在 5–7 个工作日内以挂号信寄到您登记的新加坡地址，您可在 App 查看寄送进度。

**来源：** [{"doc": "lcb__faq__card_services", "title": "银行卡服务 / Card services", "section": "新卡寄送时间 / Card delivery time", "rerank_score": 0.9997, "label": "《银行卡服务》新卡寄送时间"}]

### 11. 地址限制后追问处理时限：那超过10个工作日没收到呢？

超过 10 个工作日仍未收到新卡或补发卡，请联系银行安排补寄。您也可以在 App 中查看寄送进度，以便确认卡片当前的寄送状态。

**来源：** [{"doc": "lcb__faq__card_services", "title": "银行卡服务 / Card services", "section": "新卡寄送时间 / Card delivery time", "rerank_score": 0.9981, "label": "《银行卡服务》新卡寄送时间"}]

### 12. 英文多轮追问：How long does a replacement card take to arrive?

A replacement card is sent by registered mail within 5–7 working days. If an ATM retained your card, the replacement is free and also arrives in 5–7 working days.

**来源：** [{"doc": "lcb__faq__card_services", "title": "银行卡服务 / Card services", "section": "新卡寄送时间 / Card delivery time", "rerank_score": 0.9337, "label": "Card services – Card delivery time"}, {"doc": "lcb__faq__card_services", "title": "银行卡服务 / Card services", "section": "ATM 吞卡 / Card retained by an ATM", "rerank_score": 0.7672, "label": "Card services – Card retained by an ATM"}]

### 13. 英文多轮追问：What if it still has not arrived after 10 working days?

If your card has not arrived after 10 working days, contact the bank so it can be resent. Delivery is normally by registered mail within 5–7 working days, and you can track it in the app.

**来源：** [{"doc": "lcb__faq__card_services", "title": "银行卡服务 / Card services", "section": "新卡寄送时间 / Card delivery time", "rerank_score": 0.9954, "label": "Card services – Card delivery time"}]
