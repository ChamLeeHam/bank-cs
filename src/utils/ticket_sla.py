"""First-response SLA rules shared by the workbench and internal API."""

SLA_ALERT_RANK = {"pre_timeout": 1, "timeout": 2, "severe_timeout": 3}
SLA_LABELS = {"pre_timeout": "准备超时", "timeout": "超时", "severe_timeout": "严重超时"}


def sla_level(priority, status, elapsed_seconds):
    """Return the current first-response SLA tier for a ticket, if any."""
    if status != "open":
        return None
    elapsed = max(int(elapsed_seconds or 0), 0)
    if priority == "urgent":
        if elapsed >= 5 * 60:
            return "severe_timeout"
        if elapsed >= 4 * 60:
            return "pre_timeout"
        return None
    if elapsed >= 60 * 60:
        return "severe_timeout"
    if elapsed >= 30 * 60:
        return "timeout"
    if elapsed >= 24 * 60:
        return "pre_timeout"
    return None
