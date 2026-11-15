"""Additive, repeatable migrations for ticket transaction links and status history.

Run with: uv run python -m src.datasync.migrate_ticket_transaction
Existing tickets are preserved; only their verifiable creation event is backfilled.
"""

from src.utils.mysql_utils import get_mysql_connection


def migrate():
    with get_mysql_connection() as connection:
        with connection.cursor() as cursor:
            cursor.execute("SELECT DATABASE() AS database_name")
            database = cursor.fetchone()["database_name"]
            for name, definition in (
                ("related_transaction_id", "VARCHAR(20) NULL COMMENT '客户核查工单关联的账单交易编号'"),
                ("related_transaction", "JSON NULL COMMENT '创建工单时的账单交易快照（金额、币种、状态等）'"),
            ):
                cursor.execute("""
                    SELECT 1 FROM information_schema.columns
                    WHERE table_schema = %s AND table_name = 'tickets' AND column_name = %s
                """, (database, name))
                if cursor.fetchone() is None:
                    cursor.execute(f"ALTER TABLE tickets ADD COLUMN {name} {definition}")
                    print(f"added tickets.{name}")
                else:
                    print(f"tickets.{name} already exists")

            cursor.execute("""
                SELECT 1 FROM information_schema.statistics
                WHERE table_schema = %s AND table_name = 'tickets' AND index_name = 'idx_tk_related_transaction'
            """, (database,))
            if cursor.fetchone() is None:
                cursor.execute("ALTER TABLE tickets ADD INDEX idx_tk_related_transaction (related_transaction_id)")
                print("added index idx_tk_related_transaction")
            else:
                print("index idx_tk_related_transaction already exists")

            cursor.execute("""
                SELECT column_type AS column_type FROM information_schema.columns
                WHERE table_schema = %s AND table_name = 'tickets' AND column_name = 'status'
            """, (database,))
            status_column = cursor.fetchone()
            if status_column and "in_progress" not in status_column["column_type"]:
                cursor.execute("""
                    ALTER TABLE tickets MODIFY COLUMN status
                    ENUM('open','in_progress','replied','closed') NOT NULL DEFAULT 'open'
                """)
                print("added tickets.status=in_progress")
            else:
                print("tickets.status already supports in_progress")

            cursor.execute("""
                CREATE TABLE IF NOT EXISTS ticket_events (
                    event_id BIGINT AUTO_INCREMENT PRIMARY KEY,
                    ticket_id INT NOT NULL,
                    event_type ENUM('submitted','started','replied','closed') NOT NULL,
                    actor_type ENUM('system','agent') NOT NULL,
                    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    CONSTRAINT fk_ticket_event_ticket FOREIGN KEY (ticket_id)
                        REFERENCES tickets(ticket_id) ON DELETE CASCADE,
                    INDEX idx_ticket_event_time (ticket_id, created_at, event_id)
                ) COMMENT='工单状态流转时间线'
            """)
            cursor.execute("""
                INSERT INTO ticket_events (ticket_id, event_type, actor_type, created_at)
                SELECT t.ticket_id, 'submitted', 'system', t.created_at
                FROM tickets t
                WHERE NOT EXISTS (
                    SELECT 1 FROM ticket_events e
                    WHERE e.ticket_id = t.ticket_id AND e.event_type = 'submitted'
                )
            """)
            print(f"backfilled {cursor.rowcount} verifiable submission events")

            triggers = {
                "trg_ticket_event_insert": """
                    CREATE TRIGGER trg_ticket_event_insert
                    AFTER INSERT ON tickets FOR EACH ROW
                    INSERT INTO ticket_events (ticket_id, event_type, actor_type, created_at)
                    VALUES (NEW.ticket_id, 'submitted', 'system', NEW.created_at)
                """,
                "trg_ticket_event_status": """
                    CREATE TRIGGER trg_ticket_event_status
                    AFTER UPDATE ON tickets FOR EACH ROW
                    INSERT INTO ticket_events (ticket_id, event_type, actor_type, created_at)
                    SELECT NEW.ticket_id,
                           CASE NEW.status WHEN 'in_progress' THEN 'started'
                                WHEN 'replied' THEN 'replied' ELSE 'closed' END,
                           'agent', CURRENT_TIMESTAMP
                    WHERE OLD.status <> NEW.status
                """,
            }
            for name, statement in triggers.items():
                cursor.execute("""
                    SELECT 1 FROM information_schema.triggers
                    WHERE trigger_schema = %s AND trigger_name = %s
                """, (database, name))
                if cursor.fetchone() is None:
                    cursor.execute(statement)
                    print(f"created trigger {name}")
                else:
                    print(f"trigger {name} already exists")


if __name__ == "__main__":
    migrate()
