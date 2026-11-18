import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient

from src.api import app, require_agent
from src.utils.ticket_utils import (
    fail_ticket_notification,
    mark_notification_recipient_sent,
    requeue_failed_notification,
)


class NotificationRetryApiTests(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(app)
        app.dependency_overrides[require_agent] = lambda: {"username": "test-supervisor", "role": "supervisor"}

    def tearDown(self):
        app.dependency_overrides.clear()

    @patch("src.api.requeue_failed_notification", return_value=True)
    def test_supervisor_can_requeue_confirmed_safe_failure(self, requeue):
        response = self.client.post("/api/agent/tickets/77/retry-notification")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["notification_status"], "pending")
        requeue.assert_called_once_with(77)

    @patch("src.api.requeue_failed_notification", return_value=False)
    def test_retry_rejects_old_or_ineligible_failure(self, requeue):
        response = self.client.post("/api/agent/tickets/18/retry-notification")
        self.assertEqual(response.status_code, 409)
        requeue.assert_called_once_with(18)

    @patch("src.api.query", side_effect=[
        [{"status": "failed", "total": 6}],
        [],
        [{"ticket_id": 90, "notification_attempts": 1,
          "notification_agent_sent_at": None, "notification_supervisor_sent_at": "2026-01-01"}],
    ])
    def test_preview_lists_only_safe_retry_candidates(self, query):
        response = self.client.get("/api/agent/automation/preview")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["retryable_failed_tickets"][0]["ticket_id"], 90)
        self.assertEqual(query.call_count, 3)

    @patch("src.api._require_n8n_internal_token")
    @patch("src.api.query_one", return_value={"notification_status": "processing"})
    @patch("src.api.mark_notification_recipient_sent", return_value=True)
    def test_completed_recipient_is_recorded(self, marked, _state, _token):
        response = self.client.post("/api/internal/tickets/90/complete-notification/agent")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["notification_status"], "processing")
        marked.assert_called_once_with(90, "agent")

    @patch("src.api._require_n8n_internal_token")
    @patch("src.api.fail_ticket_notification", return_value=True)
    def test_email_node_failure_marks_ticket_failed(self, failed, _token):
        response = self.client.post("/api/internal/tickets/90/fail-notification")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["notification_status"], "failed")
        failed.assert_called_once_with(90)


class NotificationRetrySqlTests(unittest.TestCase):
    @patch("src.utils.ticket_utils.execute", return_value={"rowcount": 1})
    def test_requeue_requires_safe_failed_record_cooldown_and_attempt_budget(self, execute):
        self.assertTrue(requeue_failed_notification(90))
        sql, params = execute.call_args.args
        self.assertIn("notification_retry_safe = 1", sql)
        self.assertIn("notification_attempts < 3", sql)
        self.assertIn("INTERVAL 10 MINUTE", sql)
        self.assertIn("notification_agent_sent_at is null or notification_supervisor_sent_at is null", sql)
        self.assertEqual(params, (90,))

    @patch("src.utils.ticket_utils.execute", return_value={"rowcount": 1})
    def test_recipient_success_is_recorded_independently(self, execute):
        self.assertTrue(mark_notification_recipient_sent(90, "agent"))
        sql, params = execute.call_args.args
        self.assertIn("notification_agent_sent_at", sql)
        self.assertIn("notification_supervisor_sent_at is not null", sql)
        self.assertIn("notification_status = 'processing'", sql)
        self.assertEqual(params, (90,))

    @patch("src.utils.ticket_utils.execute", return_value={"rowcount": 1})
    def test_failure_does_not_clear_delivered_recipient_marker(self, execute):
        self.assertTrue(fail_ticket_notification(90))
        sql, params = execute.call_args.args
        self.assertIn("notification_status = 'failed'", sql)
        self.assertNotIn("notification_agent_sent_at = NULL", sql)
        self.assertEqual(params, (90,))


if __name__ == "__main__":
    unittest.main()
