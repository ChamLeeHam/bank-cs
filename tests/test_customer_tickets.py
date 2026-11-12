import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient

from src.api import app, sessions
from src.schema.exceptions import MySQLError
from src.utils.ticket_utils import list_customer_tickets


class CustomerTicketTests(unittest.TestCase):
    def setUp(self):
        sessions.clear()
        self.client = TestClient(app)
        self.sid = self.client.post('/api/sessions', json={'customer_id':'C001'}).json()['session_id']
        self.url = f'/api/sessions/{self.sid}/tickets'

    def tearDown(self):
        self.client.close()

    @patch('src.api.is_customer_verified', return_value=False)
    @patch('src.api.list_customer_tickets')
    def test_unverified_cannot_read(self, lookup, verified):
        self.assertEqual(self.client.get(self.url).status_code, 403)
        lookup.assert_not_called()

    @patch('src.api.is_customer_verified', return_value=False)
    @patch('src.api.query_one', return_value={'demo_otp':'123456'})
    @patch('src.api.list_customer_tickets', return_value=[{'ticket_id': 4, 'status':'replied', 'agent_reply':'已处理'}])
    def test_valid_otp_reads_only_bound_customer(self, lookup, otp, verified):
        self.assertEqual(self.client.post(self.url + '/verify', json={'code':'000000'}).status_code, 401)
        self.assertEqual(self.client.post(self.url + '/verify', json={'code':'123456'}).status_code, 200)
        response = self.client.get(self.url)
        self.assertEqual(response.json()['tickets'][0]['agent_reply'], '已处理')
        self.assertEqual(response.headers['cache-control'], 'no-store')
        lookup.assert_called_once_with('C001')

    @patch('src.api.is_customer_verified', return_value=True)
    @patch('src.api.list_customer_tickets', return_value=[])
    def test_existing_graph_verification_can_be_reused(self, lookup, verified):
        self.assertEqual(self.client.get(self.url).status_code, 200)
        verified.assert_called_with(self.sid, 'C001')

    @patch('src.api.is_customer_verified', return_value=True)
    @patch('src.api.list_customer_tickets', side_effect=lambda customer: [{'ticket_id': 1}] if customer == 'C001' else [])
    def test_switching_customer_does_not_return_previous_customers_tickets(self, lookup, verified):
        other = self.client.post('/api/sessions', json={'customer_id':'C002'}).json()['session_id']
        self.assertEqual(len(self.client.get(self.url).json()['tickets']), 1)
        self.assertEqual(self.client.get(f'/api/sessions/{other}/tickets').json()['tickets'], [])
        lookup.assert_any_call('C002')

    @patch('src.api.is_customer_verified', return_value=False)
    @patch('src.api.query_one', return_value={'demo_otp':'123456'})
    def test_wrong_otp_locks_this_demo_session(self, otp, verified):
        for _ in range(2):
            self.assertEqual(self.client.post(self.url + '/verify', json={'code':'000000'}).status_code, 401)
        self.assertEqual(self.client.post(self.url + '/verify', json={'code':'000000'}).status_code, 429)
        self.assertEqual(self.client.post(self.url + '/verify', json={'code':'123456'}).status_code, 429)

    @patch('src.api.is_customer_verified', return_value=False)
    @patch('src.api.query_one', side_effect=MySQLError('offline','test'))
    def test_database_error_does_not_verify(self, otp, verified):
        self.assertEqual(self.client.post(self.url + '/verify', json={'code':'123456'}).status_code, 503)
        self.assertFalse(sessions[self.sid]['ticket_verified'])

    @patch('src.utils.ticket_utils.query', return_value=[])
    def test_query_is_scoped_and_hides_draft_replies(self, query):
        list_customer_tickets('C002', 999)
        sql, params = query.call_args.args
        self.assertIn('where customer_id = %s', sql.lower())
        self.assertIn("status in ('replied', 'closed')", sql.lower())
        self.assertEqual(params, ('C002', 50))


if __name__ == '__main__':
    unittest.main()
