import asyncio
import unittest

from fastapi.testclient import TestClient
from app import main


class QuestionTimerTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        main.state = main.AppState()
        main.state.questions = main.parse_gift('::Первый::Один? {=Да ~Нет}\n\n::Второй::Два? {TRUE}')
        main.state.test_started = True
        main.state.current_index = 0
        main.state.question_seconds = 1
        main.state.students['sid'] = {'name': 'Алексей', 'ws': None}
        main.state.answers['sid'] = {}

    async def asyncTearDown(self):
        task = main.state.timer_task
        main.cancel_question_timer()
        if task is not None:
            await asyncio.gather(task, return_exceptions=True)

    async def test_server_advances_without_clients_and_counts_missing_as_wrong(self):
        async with main.state.lock:
            main.schedule_question_timer()
        async def finished():
            while not main.state.test_finished:
                await asyncio.sleep(.02)
        await asyncio.wait_for(finished(), timeout=3)
        self.assertEqual(main.state.current_index, 1)
        self.assertFalse(main.state.test_started)
        self.assertIsNone(main.state.timer_task)
        row = (await main.get_results())['results'][0]
        self.assertEqual((row['correct'], row['incorrect'], row['unanswered']), (0, 2, 2))

    async def test_cancelled_generation_cannot_advance_next_question(self):
        async with main.state.lock:
            main.schedule_question_timer()
            old_generation = main.state.timer_generation
            old_deadline = main.state.deadline
            await main.advance_question_locked()
        self.assertEqual(main.state.current_index, 1)
        # Simulate a stale expiry waking after a manual transition.
        stale = asyncio.create_task(main.expire_question(main.state, old_generation, old_deadline))
        await stale
        self.assertEqual(main.state.current_index, 1)

    async def test_reset_cancels_timer(self):
        main.schedule_question_timer()
        task = main.state.timer_task
        main.cancel_question_timer()
        main.state.test_started = False
        main.state.current_index = -1
        main.state.questions = []
        await asyncio.gather(task, return_exceptions=True)
        self.assertEqual(main.state.current_index, -1)
        self.assertIsNone(main.state.deadline)


class TimerProtocolTests(unittest.TestCase):
    def setUp(self):
        main.state = main.AppState()
        main.state.questions = main.parse_gift('::Вопрос::Ответ? {=Да ~Нет}')

    def test_deadline_rejects_answer_even_before_expiry_callback(self):
        main.state.test_started = True
        main.state.current_index = 0
        main.state.deadline = main.time.monotonic() - 1
        with TestClient(main.app) as client:
            with client.websocket_connect('/ws/student') as ws:
                ws.receive_json()
                payload = ws.receive_json()
                self.assertEqual(payload['remaining_seconds'], 0)
                ws.send_json({'type': 'answer', 'question_id': main.state.questions[0].id, 'value': [0]})
                self.assertEqual(ws.receive_json()['type'], 'error')
                self.assertEqual(client.get('/api/results').json()['results'][0]['correct'], 0)

    def test_config_validation_and_timed_start(self):
        with TestClient(main.app) as client:
            with client.websocket_connect('/ws/teacher') as teacher:
                teacher.receive_json()
                for value in (0, -1, 3601, 1.5, True, '10'):
                    teacher.send_json({'type': 'set_question_seconds', 'value': value})
                    self.assertEqual(teacher.receive_json()['type'], 'error')
                    self.assertEqual(teacher.receive_json()['question_seconds'], 60)
                teacher.send_json({'type': 'set_question_seconds', 'value': 1})
                self.assertEqual(teacher.receive_json()['question_seconds'], 1)
                with client.websocket_connect('/ws/student') as student:
                    teacher.receive_json()  # participant joined
                    student.receive_json()
                    teacher.send_json({'type': 'start_test', 'force': True})
                    question = student.receive_json()
                    self.assertEqual(question['question_seconds'], 1)
                    self.assertGreater(question['remaining_seconds'], 0)
                    self.assertLessEqual(question['remaining_seconds'], 1)
                    self.assertEqual(student.receive_json()['type'], 'finished')
