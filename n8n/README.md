# 工单通知演示

- `ticket-triage.json`：查询待通知工单，调用本地接口认领后发送邮件，并记录发送结果。
- `ticket-timeout-check.json`：查找超时的处理中通知，发送人工核对提醒并记录提醒时间。

两个导出文件默认停用，已移除本机凭证引用并将邮箱地址替换为示例地址。导入后需要重新选择 MySQL、SMTP 和 Header Auth 凭证，并填写发件人与收件邮箱。Header Auth 的名称为 `X-Internal-Token`，值与后端环境变量 `N8N_INTERNAL_TOKEN` 一致；不要将令牌写入工作流文件。

工单通知流程调用 `http://127.0.0.1:8002` 上的后端；如使用其他部署方式，需要修改接口地址并确认本地访问限制。数据库需包含 `notification_status`、`notification_claimed_at`、`notification_alerted_at` 等字段，参见 `src/datasync/schema.sql`。

手动执行可能发送邮件并修改工单。`ticket-triage.json` 中有一个未连接的重试测试节点，使用前须检查其 SQL，不要将其直接接入主流程。调试完成后再考虑启用定时触发。

认领可减少同一工单被并发发送的情况，但邮件发送后、记录结果前发生中断时仍可能重复。超时提醒也不能保证并发下只发送一次；异常工单须人工核对后再决定是否重试。
