"""Create the idempotency table used by the scheduled daily report workflow.

Run with: uv run python -m src.datasync.migrate_daily_report_delivery
This additive migration preserves all existing application data.
"""

from src.utils.mysql_utils import get_mysql_connection


def migrate():
    with get_mysql_connection() as connection:
        with connection.cursor() as cursor:
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS daily_report_delivery (
                    report_date DATE PRIMARY KEY
                        COMMENT '统计日期；同一天只允许一条有效发送流程',
                    claim_token VARCHAR(80) NOT NULL
                        COMMENT 'n8n 执行编号，用于隔离并发认领与回写',
                    status ENUM('processing','sent','failed') NOT NULL DEFAULT 'processing',
                    attempt_count INT NOT NULL DEFAULT 1,
                    claimed_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    sent_at DATETIME NULL,
                    updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
                        ON UPDATE CURRENT_TIMESTAMP,
                    INDEX idx_daily_report_delivery_status (status, updated_at)
                ) COMMENT='客服日报每日发送记录，避免同一统计日重复发邮件'
            """)
            print("daily_report_delivery is ready")


if __name__ == "__main__":
    migrate()
