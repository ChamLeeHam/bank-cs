"""Small local-demo CSAT capture for completed chat sessions."""
import re

from src.utils.mysql_utils import execute, query_one

_SKIP = re.compile(r"^(?:跳过|不评价|不想评价|算了|skip|no thanks)[。！!,.，\s]*$", re.IGNORECASE)
_NUMBER = re.compile(r"^(?:我)?(?:给|打)?\s*([1-5])\s*(?:分|星|颗星)?[。！!,.，\s]*$", re.IGNORECASE)
_WORDS = (
    (re.compile(r"^(?:非常不满意|很不满意)$"), 1),
    (re.compile(r"^(?:不太满意|不满意)$"), 2),
    (re.compile(r"^(?:一般|还行)$"), 3),
    (re.compile(r"^(?:满意|还不错)$"), 4),
    (re.compile(r"^(?:非常满意|很满意)$"), 5),
)


def parse_service_feedback(text):
    normalized = text.strip()
    if _SKIP.fullmatch(normalized):
        return "skipped", None
    match = _NUMBER.fullmatch(normalized)
    if match:
        return "rated", int(match.group(1))
    for pattern, rating in _WORDS:
        if pattern.fullmatch(normalized):
            return "rated", rating
    return None


def record_service_feedback(session_id, customer_id, status, rating):
    execute("""
        INSERT INTO session_feedback (session_id, customer_id, rating, response_status)
        VALUES (%s, %s, %s, %s)
        ON DUPLICATE KEY UPDATE rating = VALUES(rating), response_status = VALUES(response_status)
    """, (session_id, customer_id, rating, status))


def service_feedback_analytics(start_date, end_date):
    return query_one("""
        SELECT COUNT(*) AS response_count,
               COALESCE(SUM(response_status = 'rated'), 0) AS rating_count,
               COALESCE(SUM(response_status = 'skipped'), 0) AS skipped_count,
               AVG(rating) AS average_rating,
               COALESCE(SUM(rating >= 4), 0) AS positive_count
        FROM session_feedback
        WHERE created_at >= %s AND created_at < DATE_ADD(%s, INTERVAL 1 DAY)
    """, (start_date, end_date)) or {}
