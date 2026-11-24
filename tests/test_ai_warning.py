import unittest
from fastapi.testclient import TestClient
from app import main

class AIWarningTests(unittest.TestCase):
    def test_blocked_host_and_subdomain_any_path(self):
        with TestClient(main.app) as client:
            for host in ('chat.deepseek.com', 'kimi.com', 'CHAT.OPENAI.COM:8000', 'chatgpt.com.'):
                r = client.get('/api/results', headers={'host': host})
                self.assertEqual(r.status_code, 403)
                self.assertIn('Использование нейросетей на время тестирования запрещено', r.text)
                self.assertEqual(r.headers['cache-control'], 'no-store')

    def test_test_site_and_unrelated_hosts_work(self):
        with TestClient(main.app) as client:
            for host in ('test.int:8000', '10.10.10.1:8000', 'notchatgpt.com', 'chatgpt.com.example.org'):
                r = client.get('/healthz', headers={'host': host})
                self.assertEqual(r.status_code, 200)
                self.assertEqual(r.json()['status'], 'ok')

    def test_warning_local_url(self):
        with TestClient(main.app) as client:
            r = client.get('/ai-blocked', headers={'host': 'test.int:8000'})
            self.assertEqual(r.status_code, 403)
            self.assertIn('http://test.int:8000/', r.text)

    def test_ai_host_cannot_use_student_websocket(self):
        from starlette.websockets import WebSocketDisconnect
        with TestClient(main.app) as client:
            with self.assertRaises(WebSocketDisconnect):
                with client.websocket_connect('/ws/student', headers={'host': 'chat.deepseek.com'}):
                    pass
