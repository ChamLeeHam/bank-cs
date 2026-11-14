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
- `scheduled-main-draft.json`：定时总控草稿，分别触发工单通知、超时核对和日报，默认停用。
- `unified-ticket-subworkflow.json`、`unified-timeout-subworkflow.json`、`unified-daily-subworkflow.json`：统一入口的工单通知、超时核对和日报子流程。
- `daily-report-query.json`：日报查询子流程，统计数据库前一天的会话数据。
- `daily-report-main.json`：日报主流程，手动调用查询子流程并发送邮件。

两个导出文件默认停用，已移除本机凭证引用并将邮箱地址替换为示例地址。导入后需要重新选择 MySQL、SMTP 和 Header Auth 凭证，并填写发件人与收件邮箱。Header Auth 的名称为 `X-Internal-Token`，值与后端环境变量 `N8N_INTERNAL_TOKEN` 一致；不要将令牌写入工作流文件。

日报工作流也默认停用且不包含凭证和实际邮箱地址。请先导入 `daily-report-query.json`，再导入 `daily-report-main.json`，在主流程的调用节点重新选择导入后的日报子流程，避免引用旧环境的工作流 ID。日报统计使用 MySQL 的日期和时区；进入自助分支不代表问题已解决。当前日报没有按日期去重，重复执行会重复发送邮件，不要直接启用定时发送。

拆分后的工单通知流程请先导入 `ticket-query-subworkflow.json`，再导入 `ticket-notification-main.json`，并在主流程中重新选择导入后的子流程和所需凭证。手动执行主流程会认领待通知工单并实际发送邮件。原 `ticket-triage.json` 保留作为独立流程备份，避免两个主流程同时运行。

如使用两个子流程的协同版本，请先导入 `ticket-query-subworkflow.json` 和 `ticket-send-subworkflow.json`，再导入 `ticket-orchestrator.json`；在协同主流程的两个调用节点中重新选择导入后的子流程，并配置子流程的凭证及发件、收件地址。请只手动运行所需版本，避免同时启用多个通知流程。

如使用带结果汇总的候选版，先导入 `ticket-query-subworkflow.json` 和 `ticket-send-result-subworkflow.json`，再导入 `ticket-result-orchestrator.json` 并重新选择两个子流程及相关凭证。候选版逐条读取数据库中的通知状态并汇总，不将节点执行成功等同于邮件发送成功；手动运行会发送邮件。已验证成功与失败路径，导出文件尚未验证重新导入，定时触发保持关闭。

## 统一入口

`unified-main.json` 的“选择任务”节点默认 `preview`，只读查询工单状态；`notify`、`timeout`、`daily` 分别执行工单通知、超时提醒和日报，后三项会实际发送邮件。流程默认停用，没有定时触发。预览已验证，三个业务分支的统一入口调用及导出文件重新导入尚待验证。

`scheduled-main-draft.json` 预设每 5 分钟检查工单、每 10 分钟检查超时、每天 09:00 发送前一天日报；导出文件保持停用。手动触发只读工单状态，已验证不发邮件。本机 n8n 中的定时总控已分别验证工单通知和超时提醒自动执行，连续检查未重复发送；日报分支已手动验证，定时发送尚未验证。本机启用状态不代表导出的文件已启用，也不代表重新导入后的配置已验证。导入后需重新选择三个总控子流程及 MySQL 凭证，并发布依赖子流程；启用前须处理日报重复发送、超时提醒并发去重和失败恢复问题。

重新导入时，先导入 `ticket-query-subworkflow.json`、`ticket-send-result-subworkflow.json`、`daily-report-query.json`，再导入三个 `unified-*-subworkflow.json`，最后导入 `unified-main.json`。需要在各调用节点重新选择对应的新子流程：统一入口分别选择三个总控子流程；工单通知子流程选择待通知工单查询和认领与通知子流程；日报子流程选择日报查询子流程。重新设置 MySQL、SMTP、Header Auth 和邮箱地址，保持所有流程停用；避免同时启用旧流程。

工单通知流程调用 `http://127.0.0.1:8002` 上的后端；如使用其他部署方式，需要修改接口地址并确认本地访问限制。数据库需包含 `notification_status`、`notification_claimed_at`、`notification_alerted_at` 等字段，参见 `src/datasync/schema.sql`。

手动执行可能发送邮件并修改工单。`ticket-triage.json` 中有一个未连接的重试测试节点，使用前须检查其 SQL，不要将其直接接入主流程。调试完成后再考虑启用定时触发。

认领可减少同一工单被并发发送的情况，但邮件发送后、记录结果前发生中断时仍可能重复。超时提醒也不能保证并发下只发送一次；异常工单须人工核对后再决定是否重试。
