"""Idempotent additive schema update for local ticket routing."""
from src.utils.mysql_utils import execute, query


def ensure_ticket_dispute_constraint():
    indexes = {row["Key_name"] for row in query("SHOW INDEX FROM tickets") if not row["Non_unique"]}
    if "uk_ticket_active_txn" in indexes:
        return
    columns = {row["Field"] for row in query("SHOW COLUMNS FROM tickets")}
    if "active_transaction_id" in columns:
        execute("ALTER TABLE tickets ADD UNIQUE KEY uk_ticket_active_txn (customer_id, active_transaction_id)")
    else:
        execute("""
            ALTER TABLE tickets
                ADD COLUMN active_transaction_id VARCHAR(20)
                    GENERATED ALWAYS AS (
                        CASE WHEN status <> 'closed' THEN related_transaction_id ELSE NULL END
                    ) STORED,
                ADD UNIQUE KEY uk_ticket_active_txn (customer_id, active_transaction_id)
        """)


def ensure_ticket_assignment_schema():
    columns = {row["Field"] for row in query("SHOW COLUMNS FROM tickets")}
    additions = {
        "assigned_team": "ALTER TABLE tickets ADD COLUMN assigned_team VARCHAR(40) NULL",
        "assigned_agent": "ALTER TABLE tickets ADD COLUMN assigned_agent VARCHAR(32) NULL",
        "assigned_at": "ALTER TABLE tickets ADD COLUMN assigned_at DATETIME NULL",
        "dispatch_note": "ALTER TABLE tickets ADD COLUMN dispatch_note VARCHAR(180) NULL",
        "dispatch_source": "ALTER TABLE tickets ADD COLUMN dispatch_source VARCHAR(12) NOT NULL DEFAULT 'rule'",
    }
    for name, statement in additions.items():
        if name not in columns:
            execute(statement)
    indexes = {row["Key_name"] for row in query("SHOW INDEX FROM tickets")}
    if "idx_tk_assignment" not in indexes:
        execute("ALTER TABLE tickets ADD INDEX idx_tk_assignment (assigned_team, assigned_agent, status, priority)")
    execute("""
        CREATE TABLE IF NOT EXISTS ticket_assignment_events (
            event_id BIGINT AUTO_INCREMENT PRIMARY KEY,
            ticket_id INT NOT NULL,
            from_agent VARCHAR(32) NULL,
            to_agent VARCHAR(32) NOT NULL,
            actor_type ENUM('system', 'supervisor') NOT NULL,
            actor_username VARCHAR(32) NULL,
            source VARCHAR(12) NOT NULL,
            note VARCHAR(180) NULL,
            created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
            CONSTRAINT fk_ticket_assignment_event_ticket FOREIGN KEY (ticket_id)
                REFERENCES tickets(ticket_id) ON DELETE CASCADE,
            INDEX idx_ticket_assignment_event_time (ticket_id, created_at, event_id)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
    """)
    execute("""
        update tickets set assigned_team = 'customer_service',
            dispatch_note = coalesce(dispatch_note, '按已有工单意图与风险规则补充分派。'),
            assigned_at = coalesce(assigned_at, created_at)
        where assigned_team is null or assigned_team <> 'customer_service'
    """)
    _auto_assign_unowned_tickets()
    execute("""
        INSERT INTO ticket_assignment_events
            (ticket_id, from_agent, to_agent, actor_type, actor_username, source, note, created_at)
        SELECT t.ticket_id, NULL, t.assigned_agent, 'system', NULL, t.dispatch_source,
               COALESCE(t.dispatch_note, '历史工单的当前坐席归属。'), COALESCE(t.assigned_at, t.created_at)
        FROM tickets t
        WHERE t.assigned_agent IS NOT NULL
          AND NOT EXISTS (
              SELECT 1 FROM ticket_assignment_events e WHERE e.ticket_id = t.ticket_id
          )
    """)


def _auto_assign_unowned_tickets():
    """Backfill existing records into the shared pool, balanced across every seat."""
    try:
        from src.utils.agent_auth import list_users

        seats = sorted(user["username"] for user in list_users() if user.get("role") == "agent")
    except (OSError, ValueError, KeyError):
        seats = []
    if not seats:
        return

    placeholders = ", ".join(["%s"] * len(seats))
    rows = query(f"""
        select assigned_agent, count(*) as assigned_count
        from tickets where assigned_agent in ({placeholders})
        group by assigned_agent
    """, tuple(seats))
    counts = {row["assigned_agent"]: row["assigned_count"] for row in rows}
    pending = query("select ticket_id from tickets where assigned_agent is null order by created_at, ticket_id")
    for ticket in pending:
        seat = min(seats, key=lambda username: (counts.get(username, 0), username))
        result = execute("""
            update tickets set assigned_agent = %s, assigned_at = coalesce(assigned_at, created_at)
            where ticket_id = %s and assigned_agent is null
        """, (seat, ticket["ticket_id"]))
        if result["rowcount"]:
            counts[seat] = counts.get(seat, 0) + 1
