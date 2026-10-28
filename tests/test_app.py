import os
from pathlib import Path
import unittest

from fastapi.testclient import TestClient
from app.main import app


class AppStartupTests(unittest.TestCase):
    def test_pages_work_without_static_directory_and_outside_project_cwd(self):
        previous = Path.cwd()
        try:
            os.chdir('/tmp')
            with TestClient(app) as client:
                for path in ('/', '/teacher', '/test', '/healthz'):
                    with self.subTest(path=path):
                        response = client.get(path)
                        self.assertEqual(response.status_code, 200)
                self.assertEqual(client.get('/healthz').json()['status'], 'ok')
        finally:
            os.chdir(previous)
