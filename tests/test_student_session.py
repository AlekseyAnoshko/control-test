import unittest

from fastapi.testclient import TestClient
from app import main


class StudentSessionTests(unittest.TestCase):
    def setUp(self):
        main.state = main.AppState()
        self.client = TestClient(main.app)
        main.state.questions = main.parse_gift('::Вопрос::Выберите ответ {=Да ~Нет}\n\n::Second::Two? {TRUE}')
        main.state.current_index = 0
        main.state.test_started = True
        self.qid = main.state.questions[0].id

    def test_late_join_acknowledgement_and_resume_saved_answer(self):
        with self.client.websocket_connect('/ws/student?name=Алексей') as ws:
            sid = ws.receive_json()['student_id']
            self.assertEqual(ws.receive_json()['question']['id'], self.qid)
            ws.send_json({'type': 'answer', 'question_id': self.qid, 'value': [0]})
            self.assertEqual(ws.receive_json(), {'type': 'answer_saved', 'question_id': self.qid})
        with self.client.websocket_connect(f'/ws/student?name=Алексей&student_id={sid}') as ws:
            self.assertEqual(ws.receive_json()['student_id'], sid)
            payload = ws.receive_json()
            self.assertEqual(payload['index'], 1)
            self.assertGreater(payload['remaining_seconds'], 0)
            self.assertEqual(main.state.connected_count(), 1)
            # Retries are idempotent: an acknowledged answer cannot be overwritten.
            ws.send_json({'type': 'answer', 'question_id': self.qid, 'value': [1]})
            self.assertEqual(ws.receive_json()['type'], 'error')
            result = self.client.get('/api/results').json()['results'][0]
            self.assertEqual(result['correct'], 1)
        self.assertEqual(self.client.get('/api/results').json()['results'][0]['correct'], 1)

    def test_answer_immediately_advances_only_sender(self):
        with TestClient(main.app) as client:
            with client.websocket_connect('/ws/student?name=First') as first:
                first_sid = first.receive_json()['student_id']
                first.receive_json()
                with client.websocket_connect('/ws/student?name=Second') as second:
                    second_sid = second.receive_json()['student_id']
                    second.receive_json()
                    deadline = main.state.progress[second_sid]['deadline']
                    first.send_json({'type': 'answer', 'question_id': self.qid, 'value': [0]})
                    self.assertEqual(first.receive_json()['type'], 'answer_saved')
                    self.assertEqual(first.receive_json()['index'], 1)
                    self.assertEqual(main.state.progress[second_sid]['index'], 0)
                    self.assertEqual(main.state.progress[second_sid]['deadline'], deadline)
                    first.send_json({'type': 'answer', 'question_id': main.state.questions[1].id, 'value': 'TRUE'})
                    self.assertEqual(first.receive_json()['type'], 'answer_saved')
                    self.assertEqual(first.receive_json()['type'], 'finished')
                    self.assertTrue(main.state.test_started)
                    self.assertFalse(main.state.test_finished)

    def test_rejects_empty_malformed_and_stale_answers(self):
        with self.client.websocket_connect('/ws/student') as ws:
            ws.receive_json()
            ws.receive_json()
            for qid, value in [(self.qid, []), (self.qid, [True]), (self.qid, [99]),
                               (self.qid, [0, 0]), ('old-question', [0]), (self.qid, '0')]:
                with self.subTest(qid=qid, value=value):
                    ws.send_json({'type': 'answer', 'question_id': qid, 'value': value})
                    self.assertEqual(ws.receive_json()['type'], 'error')
            ws.send_json({'type': 'ping'})
            self.assertEqual(ws.receive_json()['type'], 'pong')

    def test_finished_session_remains_finished_on_reload(self):
        main.state.test_started = False
        main.state.test_finished = True
        with self.client.websocket_connect('/ws/student') as ws:
            self.assertTrue(ws.receive_json()['test_finished'])
            self.assertEqual(ws.receive_json()['type'], 'finished')
