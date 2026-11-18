"""Idempotent startup migration for chat-session service ratings."""
from src.utils.mysql_utils import execute


def ensure_service_feedback_schema():
    execute("""
        CREATE TABLE IF NOT EXISTS session_feedback (
            feedback_id BIGINT AUTO_INCREMENT PRIMARY KEY,
            session_id VARCHAR(64) NOT NULL UNIQUE,
            customer_id VARCHAR(10) NOT NULL,
            rating TINYINT UNSIGNED NULL,
            response_status ENUM('rated', 'skipped') NOT NULL,
            created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
            CONSTRAINT chk_session_feedback_rating CHECK (rating IS NULL OR rating BETWEEN 1 AND 5),
            CONSTRAINT fk_session_feedback_customer FOREIGN KEY (customer_id)
                REFERENCES customers(customer_id) ON DELETE CASCADE,
            INDEX idx_session_feedback_time (created_at, response_status)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
    """)
