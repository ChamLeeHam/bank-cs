"""Idempotent additive schema update for local ticket routing."""
from src.utils.mysql_utils import execute, query


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
        update tickets set assigned_team = 'customer_service',
            dispatch_note = coalesce(dispatch_note, '按已有工单意图与风险规则补充分派。'),
            assigned_at = coalesce(assigned_at, created_at)
        where assigned_team is null or assigned_team <> 'customer_service'
    """)
    _auto_assign_unowned_tickets()


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
