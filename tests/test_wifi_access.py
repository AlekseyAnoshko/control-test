import importlib.util
import json
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect
from app import main, wifi_access

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('wifi_guard', ROOT / 'deploy/wifi_guard.py')
monitor = importlib.util.module_from_spec(spec)
spec.loader.exec_module(monitor)
MAC = '02:11:22:33:44:55'
IP = '10.10.10.100'


class GuardTests(unittest.TestCase):
    def test_disconnect_blocks_ten_minutes_despite_fast_reconnect(self):
        guard = monitor.Guard()
        guard.connect(MAC, 100)
        guard.disconnect(MAC, 110)
        guard.connect(MAC, 110.1)
        guard.tick(112, lambda: None)
        self.assertEqual(guard.records[MAC]['blocked_until'], 710)
        self.assertTrue(guard.records[MAC]['connected'])
        guard.disconnect(MAC, 120)
        guard.connect(MAC, 121)
        guard.tick(122, lambda: None)
        self.assertEqual(guard.records[MAC]['blocked_until'], 710)
        guard.tick(710, lambda: None)
        self.assertEqual(guard.records[MAC]['blocked_until'], 0)

    def test_ap_failure_does_not_punish_station(self):
        guard = monitor.Guard()
        guard.event('<3>AP-STA-CONNECTED ' + MAC, 100)
        guard.event('<3>AP-STA-DISCONNECTED ' + MAC, 110)
        guard.event('<3>AP-DISABLED', 111)
        guard.tick(115, lambda: None)
        self.assertNotIn('blocked_until', guard.records[MAC])

    def test_unknown_disconnection_and_browser_events_do_not_block(self):
        guard = monitor.Guard()
        guard.event('<3>AP-STA-DISCONNECTED ' + MAC, 110)
        guard.event('WEBSOCKET-DISCONNECTED ' + MAC, 110)
        guard.tick(120, lambda: None)
        self.assertEqual(guard.records, {})

    def test_state_survives_monitor_restart(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'state.json'
            guard = monitor.Guard()
            guard.connect(MAC, 100)
            guard.disconnect(MAC, 110)
            guard.tick(112, lambda: monitor.atomic_json(path, guard.records))
            restored = monitor.Guard(monitor.load_records(path))
            restored.unavailable()
            self.assertEqual(restored.records[MAC]['blocked_until'], 710)


class AccessTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        root = Path(self.directory.name)
        self.path = root / 'status.json'
        self.db = root / 'db.sqlite3'
        self.patches = [patch.object(wifi_access, 'STATUS_PATH', self.path),
                        patch.object(wifi_access, 'DB_PATH', self.db)]
        for item in self.patches:
            item.start()
            self.addCleanup(item.stop)
        self.write_status()

    def write_status(self, **fields):
        self.row = {'mac': MAC, 'ip': IP, 'connected': True, **fields}
        self.path.write_text(json.dumps({'updated_at': time.time(), 'healthy': True, 'stations': [self.row]}))

    def test_real_wifi_break_denies_http_and_ws_then_expires(self):
        now = time.time()
        with TestClient(main.app, client=(IP, 1234)) as client:
            self.assertEqual(client.get('/').status_code, 200)
            with client.websocket_connect('/ws/student?name=Student') as ws:
                self.assertEqual(ws.receive_json()['type'], 'joined')
            # Closing/reopening the browser did not change the station snapshot.
            self.assertEqual(client.get('/').status_code, 200)
            self.write_status(blocked_at=now + 1, blocked_until=now + 601)
            result = client.get('/')
            self.assertEqual(result.status_code, 403)
            self.assertIn('Retry-After', result.headers)
            with client.websocket_connect('/ws/student') as ws:
                with self.assertRaises(WebSocketDisconnect) as closed:
                    ws.receive_json()
                self.assertEqual(closed.exception.code, 4403)
            self.write_status(blocked_at=now - 601, blocked_until=now - 1)
            self.assertEqual(client.get('/').status_code, 200)

    def test_first_visit_after_old_wifi_disconnect_is_not_punished(self):
        now = time.time()
        self.write_status(blocked_at=now - 10, blocked_until=now + 590)
        wifi_access.remember(IP)
        self.assertEqual(wifi_access.blocked(IP), 0)

    def test_registration_and_name_survive_web_restart_and_ip_change(self):
        wifi_access.remember(IP, 'Student')
        now = time.time()
        self.write_status(blocked_at=now + 1, blocked_until=now + 601, ip='10.10.10.101')
        self.assertGreater(wifi_access.blocked('10.10.10.101'), 0)
        self.assertEqual(wifi_access.students()['stations'][0]['name'], 'Student')
        self.assertEqual(wifi_access.blocked(IP), 0)

    def test_unknown_monitor_is_reported_without_false_block(self):
        self.path.unlink()
        with TestClient(main.app) as client:
            self.assertEqual(client.get('/').status_code, 200)
            self.assertEqual(client.get('/students').status_code, 200)
            result = client.get('/api/students').json()
            self.assertFalse(result['healthy'])

    def test_stale_snapshot_is_not_reported_as_live_wifi(self):
        self.path.write_text(json.dumps({'updated_at': 1, 'healthy': True, 'stations': [self.row]}))
        self.assertFalse(wifi_access.students()['healthy'])
        self.assertEqual(wifi_access.students()['stations'][0]['status'], 'unknown')

    def test_browser_mac_parameter_cannot_identify_a_device(self):
        with TestClient(main.app) as client:
            self.assertEqual(client.get('/?mac=' + MAC).status_code, 200)
        self.assertEqual(wifi_access.registered(), {})


if __name__ == '__main__':
    unittest.main()
