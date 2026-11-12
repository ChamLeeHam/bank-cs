import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

from src.api import app
from src.utils import agent_auth


class AgentAuthTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / 'agent_auth.json'
        self.path_patch = patch.object(agent_auth, 'AUTH_FILE', self.path)
        self.path_patch.start()
        agent_auth._sessions.clear()
        agent_auth._failures.clear()
        app.dependency_overrides.clear()
        self.client = TestClient(app)
        self.credentials = {'username':'agent01', 'password':'example-pass-123'}

    def tearDown(self):
        self.client.close()
        agent_auth._sessions.clear()
        agent_auth._failures.clear()
        self.path_patch.stop()
        self.tmp.cleanup()

    def test_tickets_require_login_even_if_accessed_directly(self):
        with patch('src.api.list_tickets') as fetch, patch('src.api.update_ticket') as update:
            self.assertEqual(self.client.get('/api/agent/tickets').status_code, 401)
            self.assertEqual(self.client.patch('/api/agent/tickets/1', json={'expected_status':'open','status':'closed'}).status_code, 401)
            fetch.assert_not_called()
            update.assert_not_called()

    def test_setup_login_logout_and_password_hash(self):
        self.assertFalse(self.client.get('/api/agent/auth/status').json()['configured'])
        response = self.client.post('/api/agent/auth/setup', json=self.credentials)
        self.assertEqual(response.status_code, 200)
        cookie = response.headers['set-cookie'].lower()
        self.assertIn('httponly', cookie)
        self.assertIn('samesite=strict', cookie)
        record = json.loads(self.path.read_text())
        self.assertNotIn(self.credentials['password'], json.dumps(record))
        self.assertEqual(self.client.post('/api/agent/auth/setup', json=self.credentials).status_code, 409)
        with patch('src.api.list_tickets', return_value=[]):
            self.assertEqual(self.client.get('/api/agent/tickets').status_code, 200)
        self.assertEqual(self.client.post('/api/agent/auth/logout').status_code, 200)
        self.assertEqual(self.client.get('/api/agent/tickets').status_code, 401)
        self.assertEqual(self.client.post('/api/agent/auth/login', json={'username':'agent01','password':'wrong'}).status_code, 401)
        self.assertEqual(self.client.post('/api/agent/auth/login', json=self.credentials).status_code, 200)

    def test_lockout_and_origin_check(self):
        self.assertEqual(self.client.post('/api/agent/auth/setup', json=self.credentials,
                                          headers={'origin':'http://other.example'}).status_code, 403)
        self.assertFalse(self.path.exists())
        self.assertEqual(self.client.post('/api/agent/auth/setup', json=self.credentials).status_code, 200)
        self.client.post('/api/agent/auth/logout')
        for _ in range(4):
            self.assertEqual(self.client.post('/api/agent/auth/login', json={'username':'agent01','password':'wrong'}).status_code, 401)
        self.assertEqual(self.client.post('/api/agent/auth/login', json={'username':'agent01','password':'wrong'}).status_code, 429)
        self.assertEqual(self.client.post('/api/agent/auth/login', json=self.credentials).status_code, 429)

    def test_expired_session_rejected(self):
        self.client.post('/api/agent/auth/setup', json=self.credentials)
        token = self.client.cookies.get('agent_session')
        agent_auth._sessions[token] = 0
        self.assertEqual(self.client.get('/api/agent/tickets').status_code, 401)

    def test_nonlocal_host_is_rejected(self):
        other = TestClient(app, base_url='http://example.com')
        self.assertEqual(other.get('/api/agent/auth/status').status_code, 403)
        other.close()


if __name__ == '__main__':
    unittest.main()
