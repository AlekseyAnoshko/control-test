import asyncio
import time
import unittest
from fastapi.testclient import TestClient
from app import main


class QuestionTimerTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        main.state = main.AppState()
        main.state.questions = main.parse_gift('::First::One? {=Yes ~No}\n\n::Second::Two? {TRUE}')
        main.state.test_started = True
        main.state.question_seconds = 1
        main.state.students['offline'] = {'name': 'Offline', 'ws': None}
        main.state.progress['offline'] = {'index': 0, 'deadline': time.monotonic() + .02}

    async def asyncTearDown(self):
        task = main.state.timer_task
        main.cancel_question_timer()
        if task:
            await asyncio.gather(task, return_exceptions=True)

    async def test_offline_student_times_out_and_test_finishes(self):
        main.schedule_question_timer()
        async def finished():
            while not main.state.test_finished:
                await asyncio.sleep(.02)
        await asyncio.wait_for(finished(), 2)
        row = (await main.get_results())['results'][0]
        self.assertEqual((row['correct'], row['unanswered']), (0, 2))

    async def test_delayed_callback_counts_all_elapsed_questions(self):
        main.state.progress['offline']['deadline'] = time.monotonic() - 3
        await main.expire_question(main.state, main.state.timer_generation, time.monotonic())
        self.assertTrue(main.state.test_finished)

    async def test_answer_changes_only_own_progress_and_deadline(self):
        second_deadline = time.monotonic() + 40
        main.state.progress['other'] = {'index': 0, 'deadline': second_deadline}
        main.state.students['other'] = {'name': 'Other', 'ws': None}
        await main.advance_student_locked('offline')
        self.assertEqual(main.state.progress['offline']['index'], 1)
        self.assertEqual(main.state.progress['other'], {'index': 0, 'deadline': second_deadline})
        self.assertFalse(main.state.test_finished)

    async def test_cancelled_generation_cannot_advance(self):
        generation = main.state.timer_generation
        main.cancel_question_timer()
        await main.expire_question(main.state, generation, time.monotonic())
        self.assertEqual(main.state.progress['offline']['index'], 0)


class TimedStartTests(unittest.TestCase):
    def test_server_starts_personal_timer_and_finishes(self):
        main.state = main.AppState()
        main.state.questions = main.parse_gift('::One::One? {TRUE}')
        with TestClient(main.app) as client:
            with client.websocket_connect('/ws/student') as student:
                sid = student.receive_json()['student_id']
                with client.websocket_connect('/ws/teacher') as teacher:
                    teacher.receive_json()
                    teacher.send_json({'type': 'start_test', 'force': True, 'question_seconds': 1})
                    question = student.receive_json()
                    self.assertGreater(question['remaining_seconds'], 0)
                    self.assertEqual(student.receive_json()['type'], 'finished')
                    self.assertEqual(main.state.progress[sid]['index'], 1)
                    self.assertTrue(main.state.test_finished)
