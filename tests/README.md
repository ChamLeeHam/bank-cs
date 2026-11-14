# 自动化测试

在项目根目录运行全部测试：

```powershell
uv run python -m unittest discover -s tests -v
```

运行单个测试文件，例如：

```powershell
uv run python -m unittest discover -s tests -p "test_transfer_status.py" -v
uv run python -m unittest discover -s tests -p "test_handoff.py" -v
```

## 覆盖范围

| 测试文件 | 主要检查内容 |
| --- | --- |
| `test_api.py` | 页面和会话接口、暂停后恢复、流式响应、新问题取消汇款选择 |
| `test_agent_auth.py` | 坐席登录、退出、会话过期、锁定和访问限制 |
| `test_agent_workbench.py` | 工单筛选、回复、状态变更及数据库异常 |
| `test_customer_tickets.py` | 演示 OTP、客户工单访问隔离和错误验证码限制 |
| `test_handoff.py` | 投诉和盗刷分流、工单摘要、通知认领及重复认领 |
| `test_transfer_status.py` | 汇款筛选、多笔追问、多轮选择、客户隔离和未验证阻止查询 |
| `test_privacy.py` | 敏感信息脱敏、账户历史和私密输入保护 |
| `test_audit.py` | 审计记录中的敏感信息、暂停与恢复、写入失败处理 |

2026-11-14 本机执行结果：**56 项通过**。异常处理测试会有意打印模拟的数据库错误；只要对应测试显示 `ok` 且最后是 `OK`，这些日志本身不代表测试失败。

后续新增 `test_answer_grounding.py`，完整套件为 **58 项通过**；独立运行的真实链路测试结果见 `真实链路测试记录.md`。

### 连接真实本机服务（不用 mock）

先在一个终端启动 API，再在项目根目录运行：

```powershell
uv run python tests/live_api_regression.py
```

脚本使用真实 FastAPI 和演示 MySQL，覆盖路由/RAG、OTP、工单访问权限及流式接口；不会创建工单、发邮件或修改银行卡。可用 `BANKCS_API_BASE` 指向其他本机端口。它不属于 `unittest discover` 自动发现的 `test_*.py`。

2026-11-14 本轮执行：**24 项真实 API 检查通过**，发现并修复同一会话 OTP 输错 3 次后可重新发起验证的绕过问题。复测记录见 `真实链路测试记录.md`。

## 还需要的验证

现有测试大量使用模拟对象，主要检查代码分支和接口行为；通过不等于真实数据库、模型、知识库服务、邮件或浏览器端到端流程均通过。

- 按需求分别用中文、英文、中英混合各至少两段对话，检查意图路由和 FAQ 来源。
- 用演示数据检查 OTP 前无法查账户、验证码失败、冻结前确认及取消。
- 验证投资建议拒答、提示注入拦截和实际审计记录中的脱敏效果。
- 在客户页面和坐席页面验证工单创建、通知、处理及客户查看的完整链路。
- 在 n8n 验证通知失败、超时提醒和重复执行；真实邮件和定时任务不由上述单元测试发送或启动。
- 日报目前已验证手动发送；按日期去重和定时发送仍待实现或验证。
