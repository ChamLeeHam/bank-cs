"""Idempotent additive migration for first-response SLA email tracking."""

from src.utils.mysql_utils import execute, query


def ensure_ticket_sla_schema():
    columns = {row["Field"] for row in query("SHOW COLUMNS FROM tickets")}
    additions = {
        "sla_tracking_enabled": "ALTER TABLE tickets ADD COLUMN sla_tracking_enabled TINYINT(1) NOT NULL DEFAULT 1",
        "sla_notified_level": "ALTER TABLE tickets ADD COLUMN sla_notified_level VARCHAR(24) NULL",
        "sla_agent_notified_level": "ALTER TABLE tickets ADD COLUMN sla_agent_notified_level VARCHAR(24) NULL",
        "sla_supervisor_notified_level": "ALTER TABLE tickets ADD COLUMN sla_supervisor_notified_level VARCHAR(24) NULL",
        "sla_claimed_level": "ALTER TABLE tickets ADD COLUMN sla_claimed_level VARCHAR(24) NULL",
        "sla_claimed_at": "ALTER TABLE tickets ADD COLUMN sla_claimed_at DATETIME NULL",
    }
    for name, statement in additions.items():
        if name not in columns:
            execute(statement)

    # Before per-recipient tracking, sla_notified_level was only advanced after
    # both emails succeeded. Preserve that history for each recipient.
    execute("""
        UPDATE tickets
        SET sla_agent_notified_level = COALESCE(sla_agent_notified_level, sla_notified_level),
            sla_supervisor_notified_level = COALESCE(sla_supervisor_notified_level, sla_notified_level)
        WHERE sla_notified_level IS NOT NULL
    """)

    indexes = {row["Key_name"] for row in query("SHOW INDEX FROM tickets")}
    if "idx_ticket_sla_monitor" not in indexes:
        execute("ALTER TABLE tickets ADD INDEX idx_ticket_sla_monitor (status, priority, created_at)")

    execute("""
        CREATE TABLE IF NOT EXISTS ticket_sla_settings (
            setting_id TINYINT PRIMARY KEY,
            tracking_started_at DATETIME NOT NULL
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
    """)
    if not query("SELECT setting_id FROM ticket_sla_settings WHERE setting_id = 1"):
        # Historical demo backlog remains visible in the SLA UI, but must not
        # trigger a burst of escalation mail at rollout time.
        execute("""
            UPDATE tickets
            SET sla_tracking_enabled = 0, sla_claimed_level = NULL, sla_claimed_at = NULL
        """)
        execute("INSERT INTO ticket_sla_settings (setting_id, tracking_started_at) VALUES (1, NOW())")
