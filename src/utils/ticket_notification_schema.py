"""Add per-recipient delivery state for controlled notification retries."""

from src.utils.mysql_utils import execute, query


def ensure_ticket_notification_schema():
    columns = {row["Field"] for row in query("SHOW COLUMNS FROM tickets")}
    additions = {
        # Existing failed rows remain blocked until a supervisor reviews them.
        "notification_retry_safe": (
            "ALTER TABLE tickets ADD COLUMN notification_retry_safe "
            "TINYINT(1) NOT NULL DEFAULT 0"
        ),
        "notification_agent_sent_at": "ALTER TABLE tickets ADD COLUMN notification_agent_sent_at DATETIME NULL",
        "notification_supervisor_sent_at": "ALTER TABLE tickets ADD COLUMN notification_supervisor_sent_at DATETIME NULL",
    }
    for name, statement in additions.items():
        if name not in columns:
            execute(statement)

    indexes = {row["Key_name"] for row in query("SHOW INDEX FROM tickets")}
    if "idx_ticket_notification_retry" not in indexes:
        execute("ALTER TABLE tickets ADD INDEX idx_ticket_notification_retry "
                "(notification_status, notification_retry_safe, notification_attempts)")
