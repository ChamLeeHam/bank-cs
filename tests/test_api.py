import unittest
import json
from unittest.mock import patch

from fastapi.testclient import TestClient

from src.api import app, sessions


class ApiTests(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(app)
        sessions.clear()

    def test_page_and_new_session(self):
        self.assertEqual(self.client.get("/").status_code, 200)
        self.assertEqual(self.client.post("/api/sessions", json={"customer_id": "OTHER"}).status_code, 400)
        response = self.client.post("/api/sessions", json={"customer_id": "C001"})
        self.assertEqual(response.status_code, 200)
        self.assertIn(response.json()["session_id"], sessions)

    @patch("src.api.resume", return_value={"answer": "选择交易", "pending": {"type": "select_transfer"}})
    @patch("src.api.run_query", return_value={"answer": "请输入演示验证码", "pending": {"type": "otp"}})
    def test_pending_input_resumes_the_same_customer_session(self, run, resume):
        session = self.client.post("/api/sessions", json={"customer_id": "C001"}).json()["session_id"]
        path = f"/api/sessions/{session}/messages"
        result = self.client.post(path, json={"text": "我的汇款到了吗"}).json()
        self.assertEqual(result["pending"], "otp")
        result = self.client.post(path, json={"text": "123456"}).json()
        self.assertEqual(result["pending"], "select_transfer")
        run.assert_called_once_with(session, "C001", "我的汇款到了吗", verbose=False)
        resume.assert_called_once_with(session, "123456", verbose=False)

    def test_missing_session_and_empty_message(self):
        self.assertEqual(self.client.post('/api/sessions/nope/messages', json={'text': 'hi'}).status_code, 404)
        session = self.client.post('/api/sessions', json={'customer_id': 'C001'}).json()['session_id']
        self.assertEqual(self.client.post(f'/api/sessions/{session}/messages', json={'text': '   '}).status_code, 400)

    @patch("src.api.resume", return_value={"answer": "已取消选择汇款", "pending": None})
    @patch("src.api.run_query", return_value={"answer": "余额查询", "pending": None})
    def test_new_question_cancels_transfer_selection(self, run, resume):
        session = self.client.post('/api/sessions', json={'customer_id': 'C001'}).json()['session_id']
        sessions[session]['pending'] = 'select_transfer'
        result = self.client.post(f'/api/sessions/{session}/messages', json={'text': '帮我 check 一下 balance?'}).json()
        self.assertEqual(result['answer'], '余额查询')
        resume.assert_called_once_with(session, '取消', verbose=False)
        run.assert_called_once_with(session, 'C001', '帮我 check 一下 balance?', verbose=False)

    @patch('src.api.run_query', return_value={'answer': '这是一条经过检查的演示回复，内容比较长。',
                                               'pending': {'type': 'otp'}})
    def test_stream_sends_checked_answer_then_pending_state(self, run):
        session = self.client.post('/api/sessions', json={'customer_id': 'C001'}).json()['session_id']
        response = self.client.post(f'/api/sessions/{session}/messages/stream', json={'text': '查余额'})
        events = [json.loads(line) for line in response.text.splitlines()]
        self.assertEqual(events[0]['type'], 'status')
        self.assertEqual(''.join(e['text'] for e in events if e['type'] == 'delta'),
                         '这是一条经过检查的演示回复，内容比较长。')
        self.assertEqual(events[-1], {'type': 'done', 'pending': 'otp'})
        self.assertEqual(sessions[session]['pending'], 'otp')


if __name__ == '__main__':
    unittest.main()
