# 工单通知演示

- `ticket-triage.json`：查询待通知工单，调用本地接口认领后发送邮件，并记录发送结果。
- `ticket-timeout-check.json`：查找超时的处理中通知，发送人工核对提醒并记录提醒时间。
- `ticket-query-subworkflow.json`：只读查询待通知工单的子流程。
- `ticket-notification-main.json`：调用查询子流程，并认领、发送和记录结果的测试主流程。
- `ticket-send-subworkflow.json`：接收工单并认领、发送、记录结果的子流程。
- `ticket-orchestrator.json`：依次调用查询子流程和通知子流程的测试主流程。
- `ticket-send-result-subworkflow.json`：逐条认领和通知，并读取实际工单通知状态的候选子流程。
- `ticket-result-orchestrator.json`：逐条调用通知子流程并汇总实际状态的候选主流程。
- `unified-main.json`：银行客服自动化统一入口，默认只读预览。
- `scheduled-main.json`：与本机已发布总控对齐；工单通知、超时核对和首次响应 SLA 定时触发已启用，日报触发器停用。
- `unified-ticket-subworkflow.json`、`unified-timeout-subworkflow.json`、`unified-daily-subworkflow.json`：统一入口的工单通知、超时核对和日报子流程。
- `unified-sla-subworkflow.json`：按首次响应时限逐档发送坐席与主管提醒的 SLA 子流程。
- `daily-report-query.json`：日报查询子流程，统计数据库前一天的会话数据。
- `daily-report-main.json`：日报主流程，手动调用查询子流程并发送邮件。

其他通用导出文件默认停用；`scheduled-main.json` 按本机已发布总控状态导出（`active: true`，日报触发器单独停用）。该文件导入后可能启动定时流程，请先确认这是预期行为。导出文件已移除本机凭证引用并将邮箱地址替换为示例地址。导入后需要重新选择 MySQL、SMTP 和 Header Auth 凭证，并填写发件人与收件邮箱。Header Auth 的名称为 `X-Internal-Token`，值与后端环境变量 `N8N_INTERNAL_TOKEN` 一致；不要将令牌写入工作流文件。

日报工作流导出文件不包含凭证和实际邮箱地址。旧版 `daily-report-main.json` 仍是未加去重的测试版，不要将它设为定时流程。统一总控使用的 `unified-daily-subworkflow.json` 已加入按 `report_date` 原子认领：同一天已发送、正在发送或并发重复执行时不会再次发信；发送失败会记为 `failed`，后续执行可以重试。若流程在邮件发送后、成功记账前中断，会保留 `processing` 并阻止自动重发，需先人工核对邮箱再处理，避免重复邮件。数据库迁移已执行，n8n 本机已发布“总控子流程 - 日报（去重）”，总控中的日报调用已切换到新子流程；日报 09:00 触发器保持停用。2026-11-17 手动验证：模拟 SMTP 故障后流程将记录置为 `failed`；改用 `qq account`，修正发件/收件地址后，邮件节点成功且数据库状态为 `sent`；同一统计日期再次执行时，认领校验无输出，邮件节点未运行。QQ 邮箱网页当前要求登录，因此只确认 SMTP 接受发送且数据库成功记账，尚未目视确认收件箱。日报统计使用 MySQL 的日期和时区；进入自助分支不代表问题已解决。

拆分后的工单通知流程请先导入 `ticket-query-subworkflow.json`，再导入 `ticket-notification-main.json`，并在主流程中重新选择导入后的子流程和所需凭证。手动执行主流程会认领待通知工单并实际发送邮件。原 `ticket-triage.json` 保留作为独立流程备份，避免两个主流程同时运行。

如使用两个子流程的协同版本，请先导入 `ticket-query-subworkflow.json` 和 `ticket-send-subworkflow.json`，再导入 `ticket-orchestrator.json`；在协同主流程的两个调用节点中重新选择导入后的子流程，并配置子流程的凭证及发件、收件地址。请只手动运行所需版本，避免同时启用多个通知流程。

如使用带结果汇总的候选版，先导入 `ticket-query-subworkflow.json` 和 `ticket-send-result-subworkflow.json`，再导入 `ticket-result-orchestrator.json` 并重新选择两个子流程及相关凭证。候选版逐条读取数据库中的通知状态并汇总，不将节点执行成功等同于邮件发送成功；手动运行会发送邮件。已验证成功与失败路径，导出文件尚未验证重新导入，定时触发保持关闭。

## 统一入口

`unified-main.json` 的“选择任务”节点默认 `preview`，只读查询工单状态；`notify`、`timeout`、`daily` 分别执行工单通知、超时提醒和日报，后三项会实际发送邮件。流程默认停用，没有定时触发。预览已验证，三个业务分支的统一入口调用及导出文件重新导入尚待验证。

坐席管理员页面也可通过这个统一入口触发工单通知。导入 `unified-main.json` 后，在“管理员页面触发”Webhook 节点选择 Header Auth 凭证：Header 名称为 `X-Internal-Token`，值与后端 `.env` 中的 `N8N_INTERNAL_TOKEN` 相同。后端 `.env` 的 `N8N_AUTOMATION_WEBHOOK_URL` 填写 n8n 的生产 Webhook 地址，例如 `http://127.0.0.1:5678/webhook/bank-cs/admin-automation`；不要使用 `/webhook-test/` 地址。需先重新选择三个总控子流程、MySQL 和 SMTP 凭证并检查示例邮箱地址。启用统一入口并配置该地址及令牌后，主管页面才会显示“Webhook 参数已填写”并开放手动触发按钮。后端会限制 Webhook 地址为本机 HTTP 地址；令牌只保存在后端环境变量和 n8n 凭证中。页面的队列预览只读，触发邮件前还需在页面二次确认。

工单通知子流程会分别发送“坐席待办”和“主管知会”两封邮件：坐席邮件发给工单当前分配的坐席，主管邮件发给主管账号。主管可在工作台的“坐席管理”中分别维护邮箱；演示时可给两类账号填写同一个地址，主题仍会区分。创建新坐席时需填写通知邮箱。Webhook 认领接口只向 n8n 返回通知所需的工单编号、分类、优先级和收件邮箱，不返回客户对话内容。两封邮件都发送成功后，通知状态才记为 `sent`。

受控失败重试使用 `ticket-send-result-subworkflow.json`：坐席和主管送达时间分别记账，任一邮件明确失败时工单记为 `failed`，之后主管可在自动化页面确认重新排队。仅新流程创建且 `notification_retry_safe=1` 的工单可重试；每次至少间隔 10 分钟，最多 3 次，只会补发尚未成功送达的角色。历史失败工单默认不允许重试，需先人工核对收件箱，避免重复邮件。API 启动迁移会添加对应字段；导入时还要在总控子流程里重新选择更新后的查询与发送子流程，并为新增 HTTP 节点重新选择 Header Auth 凭证。

超时提醒子流程也分别发送“坐席超时提醒”和“主管超时知会”，收件人读取工单当前分配坐席及主管的通知邮箱，演示时可共用同一邮箱，邮件主题仍可区分。提醒标记只会在两封邮件都发送成功后写入；若收件人配置缺失或发送失败，执行会报错并留待后续人工核对。

首次响应 SLA 子流程每分钟检查一次仍处于“待处理”的工单：普通工单在 24 分钟进入“准备超时”、30 分钟进入“超时”、60 分钟进入“严重超时”；紧急工单在 4 分钟进入“准备超时”，满 5 分钟仍未开始处理则进入“严重超时”。每个档位升级时，分别发邮件给当前坐席和主管；同邮箱演示时主题会标明角色及档位。坐席点击“开始处理”后停止首次响应计时。每个档位分别记录坐席和主管的发送结果：若一方发送失败，10 分钟后重试只补发失败的一封，不重复发送已成功的一封。首次启用时已存在的演示工单仍按实际等待时间显示档位，但不补发历史提醒；新建工单才进入邮件 SLA 监控，避免启用时一次性发送历史邮件。

本机已启动 n8n 并发布管理员 Webhook；仓库中的 `unified-main.json` 仍保持停用状态，`.env.example` 中的地址也留空，便于其他环境按上文配置。设置联动时没有调用 Webhook。启用后，`notify` 会执行真实邮件发送，并修改工单通知状态。

`scheduled-main.json` 对齐本机已发布的“银行客服自动化 - 定时总控”：每 5 分钟检查待通知工单、每 10 分钟检查超时通知、每分钟检查首次响应 SLA 均已启用；每天 09:00 的日报触发器保持停用。导入此文件可能启动定时流程，请先确认目标环境和凭证配置。每分钟 SLA 监控通过内部接口原子认领档位，分别记账坐席与主管发送成功状态；SMTP 成功后才完成对应角色记账，失败认领 10 分钟后可重试且跳过已发送成功的角色。导入后需重新选择对应子流程、MySQL、SMTP 和 Header Auth 凭证。导出中的 `active: true` 与本机总控的运行状态一致。

重新导入时，先导入 `ticket-query-subworkflow.json`、`ticket-send-result-subworkflow.json`、`daily-report-query.json`，再导入三个 `unified-*-subworkflow.json`，最后导入 `unified-main.json`。需要在各调用节点重新选择对应的新子流程：统一入口分别选择三个总控子流程；工单通知子流程选择待通知工单查询和认领与通知子流程；日报子流程选择日报查询子流程。重新设置 MySQL、SMTP、Header Auth 和邮箱地址；除 `scheduled-main.json` 外，其他流程导入后保持停用，避免同时启用旧流程。只有确认需要运行定时通知时才导入并启用 `scheduled-main.json`。

工单通知流程调用 `http://127.0.0.1:8002` 上的后端；如使用其他部署方式，需要修改接口地址并确认本地访问限制。数据库需包含 `notification_status`、`notification_claimed_at`、`notification_alerted_at` 等字段，参见 `src/datasync/schema.sql`。

手动执行可能发送邮件并修改工单。`ticket-triage.json` 是旧版备份，其中固定 ticket #11 的重置节点未连接，不要将其接入主流程。新受控重试需使用上文分角色记账的子流程；导入和验证前先停用旧版通知发送流程，避免同一队列被两个版本同时处理。

认领可减少同一工单被并发发送的情况，但邮件发送后、记录结果前发生中断时仍可能重复。超时提醒也不能保证并发下只发送一次；异常工单须人工核对后再决定是否重试。
