import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient

from src.api import app, require_agent
from src.schema.exceptions import MySQLError
from src.utils.ticket_utils import list_tickets, update_ticket


class WorkbenchTests(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(app)
        app.dependency_overrides[require_agent] = lambda: True

    def tearDown(self):
        app.dependency_overrides.clear()

    def test_page_and_filter_validation(self):
        self.assertEqual(self.client.get('/agent').status_code, 200)
        self.assertEqual(self.client.get('/api/agent/tickets?status=invalid').status_code, 422)

    @patch('src.api.list_tickets', return_value=[{'ticket_id': 2, 'priority': 'urgent'}])
    def test_list_passes_filters(self, fetch):
        response = self.client.get('/api/agent/tickets?status=open&priority=urgent')
        self.assertEqual(response.json()['tickets'][0]['ticket_id'], 2)
        fetch.assert_called_once_with(status='open', priority='urgent')

    @patch('src.api.update_ticket', return_value=True)
    def test_reply_requires_text_and_valid_transition(self, update):
        path = '/api/agent/tickets/1'
        self.assertEqual(self.client.patch(path, json={'expected_status':'open','status':'replied','reply':'  '}).status_code, 400)
        self.assertEqual(self.client.patch(path, json={'expected_status':'closed','status':'replied','reply':'ok'}).status_code, 400)
        update.assert_not_called()
        response = self.client.patch(path, json={'expected_status':'open','status':'replied','reply':' 已记录 '})
        self.assertEqual(response.status_code, 200)
        update.assert_called_once_with(1, 'open', 'replied', '已记录')

    @patch('src.api.update_ticket', return_value=False)
    def test_changed_status_returns_conflict(self, update):
        response = self.client.patch('/api/agent/tickets/1', json={'expected_status':'open','status':'closed'})
        self.assertEqual(response.status_code, 409)

    @patch('src.api.list_tickets', side_effect=MySQLError('offline', 'test'))
    def test_database_failure_is_reported(self, fetch):
        self.assertEqual(self.client.get('/api/agent/tickets').status_code, 503)

    @patch('src.utils.ticket_utils.query', return_value=[])
    def test_query_uses_parameters_and_bounded_limit(self, query):
        list_tickets(status='open', priority='urgent', limit=999)
        sql, args = query.call_args.args
        self.assertIn('status = %s', sql)
        self.assertEqual(args, ['open', 'urgent', 100])

    @patch('src.utils.ticket_utils.execute', return_value={'rowcount': 1})
    def test_update_checks_previous_state(self, execute):
        self.assertTrue(update_ticket(4, 'open', 'replied', '回复'))
        sql, args = execute.call_args.args
        self.assertIn('ticket_id = %s and status = %s', sql)
        self.assertEqual(args, ('replied', '回复', 4, 'open'))


if __name__ == '__main__':
    unittest.main()
