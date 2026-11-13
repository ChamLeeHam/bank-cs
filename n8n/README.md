# 工单通知演示

- `ticket-triage.json`：查询待通知工单，调用本地接口认领后发送邮件，并记录发送结果。
- `ticket-timeout-check.json`：查找超时的处理中通知，发送人工核对提醒并记录提醒时间。
- `daily-report-query.json`：日报查询子流程，统计数据库前一天的会话数据。
- `daily-report-main.json`：日报主流程，手动调用查询子流程并发送邮件。

两个导出文件默认停用，已移除本机凭证引用并将邮箱地址替换为示例地址。导入后需要重新选择 MySQL、SMTP 和 Header Auth 凭证，并填写发件人与收件邮箱。Header Auth 的名称为 `X-Internal-Token`，值与后端环境变量 `N8N_INTERNAL_TOKEN` 一致；不要将令牌写入工作流文件。

日报工作流也默认停用且不包含凭证和实际邮箱地址。请先导入 `daily-report-query.json`，再导入 `daily-report-main.json`，在主流程的调用节点重新选择导入后的日报子流程，避免引用旧环境的工作流 ID。日报统计使用 MySQL 的日期和时区；进入自助分支不代表问题已解决。当前日报没有按日期去重，重复执行会重复发送邮件，不要直接启用定时发送。

工单通知流程调用 `http://127.0.0.1:8002` 上的后端；如使用其他部署方式，需要修改接口地址并确认本地访问限制。数据库需包含 `notification_status`、`notification_claimed_at`、`notification_alerted_at` 等字段，参见 `src/datasync/schema.sql`。

手动执行可能发送邮件并修改工单。`ticket-triage.json` 中有一个未连接的重试测试节点，使用前须检查其 SQL，不要将其直接接入主流程。调试完成后再考虑启用定时触发。

认领可减少同一工单被并发发送的情况，但邮件发送后、记录结果前发生中断时仍可能重复。超时提醒也不能保证并发下只发送一次；异常工单须人工核对后再决定是否重试。
