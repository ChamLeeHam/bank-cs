# 银行客服评估集

V1 将项目已有的两份人工整理数据固定为可复现基准，通过评估脚本在本机实际模型、RAG 服务和 FastAPI 上运行：

- 意图分类：`data/intent/reviewed/test.jsonl`，640 条留出样本（英文 216、中英混合 220、中文 204）。人工复核字段为 `reviewed: true`。
- FAQ 来源：`data/kb/eval_questions.jsonl`，41 条（英文 19、中英混合 9、中文 13）；含知识库外问题和两条带对话历史的追问。
- API 时延与答复引用：使用单独维护的 `data/evaluation/api_faq_questions.jsonl`，一般性 FAQ 中英混合各 6 条；可通过命令行调整。

数据文件 SHA-256 会写入每次报告，固定回答评估具体使用的样本版本。不要把测试集用于训练或调参后再将同一批结果作为独立测试集报告。

## 运行

### Jev 对照组（智增增）

`src/nodes/jev_intent.py` 是独立的 Jev 一级意图分类器，不参与当前客服主图。它通过智增增的 `/v1/systemone` 使用 Choice 类型，分类选项与目前 640 条意图留出集相同。请在项目 `.env` 或当前进程环境中配置 `TYPESAFE_API_KEY`（智增增密钥）、`TYPESAFE_BASE_URL=https://api.zhizengzeng.com/v1` 和 `TYPESAFE_MODEL=jev-latest`；不要提交密钥。也接受不带 `/v1` 的域名或完整 `/v1/systemone` 地址。

```powershell
# 仅校验数据与基线，不访问 API
uv run python -m src.scripts.compare_jev_intent --dry-run

# 小样本联通检查：默认 12 条，将向智增增发送这些演示样本并产生 API 请求
uv run python -m src.scripts.compare_jev_intent

# 完整 640 条对照；请先确认接口、余额与调用费用
uv run python -m src.scripts.compare_jev_intent --limit 0

# 若中途请求失败，用输出的部分结果续跑，不重复成功样本
uv run python -m src.scripts.compare_jev_intent --limit 0 --resume data/evaluation/results/jev_comparison_时间戳.json
```

默认基线是 `evaluation_20261114_234123.json`，可通过 `--baseline` 指定其他同一测试集的报告。脚本验证数据哈希和样本内容，只比较同一批样本的一级意图，输出 Markdown 和 JSON。接口错误会中断，部分结果会标明未完成，不会用其他模型冒充 Jev。每条请求会把演示样本文本发送到第三方服务；不要使用真实客户数据。

Qwen 基线是本机微调模型**加规则**，Jev 是纯模型分类；Jev 耗时包含网络和服务调用，本地耗时不含加载。小样本只适合检查连通性；历史留出集部分错分样本已用于调规则，PPT 中应说明并另准备未参与调整的独立测试集。Jev 返回类型符合规定不代表分类一定正确。

先启动本地服务（默认 API 为 8002），并确保意图模型、Milvus、嵌入/重排模型及 RAG 生成配置可用：

```powershell
uv run uvicorn src.api:app --host 127.0.0.1 --port 8002
```

另开终端运行全量评估：

```powershell
uv run python src/scripts/evaluate_quality.py
```

结果保存到 `data/evaluation/results/evaluation_<时间戳>.md` 和 `.json`。只重跑单项可使用 `--skip-intent`、`--skip-retrieval` 或 `--skip-api`；例如只测 API 时延：

```powershell
uv run python src/scripts/evaluate_quality.py --skip-intent --skip-retrieval
```

## 指标定义

- 意图一级准确率：预测意图与人工标签完全相同的比例。另报一级 + 二级标签完全准确率、Macro-F1、语言分组和模型单条延迟。模型加载/预热耗时单独记录，不混进分类时延。
- FAQ 预期来源召回率：最终保留章节至少命中一个人工标注 `expected` 的可回答问题比例。Top-1 准确率要求第一条保留章节命中标注。对 `expected: null` 样本计算拒答准确率。
- API 端到端平均响应时间：从发送消息到 FastAPI 返回回答的墙钟时间，不包含创建新会话。来源引用准确率检查答案引用区是否包含预期章节的对应语言标题。

FAQ 检索命中和引用标题匹配不证明回答内容完全正确；如需答辩展示事实性，还应人工复核典型输出或单独使用有依据的答案级标注集。API 测试只选一般性公开 FAQ，不包含个人账户、交易查询、投诉或办理语句；每次仍会新建演示会话，且正式复跑前应先检查 API 样本集不包含会触发 OTP、工单、邮件或账户操作的内容。
