import unittest
from fastapi.testclient import TestClient
from app import main

BANK = r'''
::Multiple::Choose {~%33.33333%One ~%33.33333%Two ~%33.33333%Three ~%-100%Wrong}

::Single::Choose {=Yes ~No}

::Synonyms::Type {=SRV =srv}

::Number::Type {#3:0.1}

::Matching::Match {=1 -> Discover =2 -> Offer =3 -> Discover}

::Escapes::File {=\~/.ssh/authorized_keys ~\~/.ssh/known_hosts}

::Braces::Type {=\{\{ stage \}\}}
'''

class GiftFormatsTests(unittest.TestCase):
    def test_weighted_answers(self):
        q = main.parse_gift(BANK)[0]
        self.assertTrue(q.to_public_dict()['multiple'])
        self.assertEqual([a.text for a in q.answers], ['One', 'Two', 'Three', 'Wrong'])
        self.assertTrue(q.check([0, 1, 2]))
        self.assertFalse(q.check([0, 1]))
        self.assertFalse(q.check([0, 1, 2, 3]))

    def test_synonyms_and_escapes(self):
        qs = main.parse_gift(BANK)
        self.assertEqual(qs[2].qtype, 'short_answer')
        self.assertTrue(qs[2].check('SRV'))
        self.assertEqual(qs[5].answers[0].text, '~/.ssh/authorized_keys')
        self.assertEqual(len(qs[5].answers), 2)
        self.assertTrue(qs[6].check('{{ stage }}'))

    def test_numbers(self):
        q = main.parse_gift(BANK)[3]
        self.assertEqual(q.to_public_dict()['type'], 'short_answer')
        self.assertTrue(q.check('3,05'))
        self.assertFalse(q.check('4'))
        self.assertFalse(q.check('NaN'))

    def test_matching(self):
        q = main.parse_gift(BANK)[4]
        self.assertEqual(q.to_public_dict()['answers'], ['1', '2', '3'])
        self.assertEqual(q.to_public_dict()['matching_choices'], ['Discover', 'Offer'])
        self.assertTrue(q.check([0, 1, 0]))
        self.assertFalse(q.check([1, 0, 0]))

    def test_all_answers_saved_and_advance(self):
        main.state = main.AppState()
        with TestClient(main.app) as client:
            response = client.post('/api/upload_test', files={'file': ('formats.gift', BANK.encode())})
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.json()['count'], 7)
            with client.websocket_connect('/ws/student?name=Formats') as student:
                student.receive_json()
                with client.websocket_connect('/ws/teacher') as teacher:
                    teacher.receive_json()
                    teacher.send_json({'type': 'start_test', 'force': True})
                    for index, value in enumerate(([0, 1, 2], [0], 'srv', '3', [0, 1, 0], [0], '{{ stage }}')):
                        payload = student.receive_json()
                        self.assertEqual(payload['index'], index)
                        student.send_json({'type': 'answer', 'question_id': payload['question']['id'], 'value': value})
                        self.assertEqual(student.receive_json()['type'], 'answer_saved')
                    self.assertEqual(student.receive_json()['type'], 'finished')
            row = client.get('/api/results').json()['results'][0]
            self.assertEqual((row['answered'], row['correct'], row['unanswered']), (7, 7, 0))

    def test_invalid_keys_fail_upload(self):
        main.state = main.AppState()
        with TestClient(main.app) as client:
            result = client.post('/api/upload_test', files={'file': ('invalid.gift', b'::Bad::Choose {~Wrong ~Wrong too}')})
            self.assertEqual(result.status_code, 400)
            self.assertEqual(main.state.questions, [])
