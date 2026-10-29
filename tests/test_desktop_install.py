import importlib.util
from pathlib import Path
import tempfile
import signal
import unittest
from unittest.mock import patch, Mock, call

ROOT = Path(__file__).resolve().parents[1]


def module(name):
    spec = importlib.util.spec_from_file_location(name, ROOT / 'deploy' / f'{name}.py')
    result = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(result)
    return result


installer = module('install_desktop')
controller = module('desktop_control')


class DesktopInstallTests(unittest.TestCase):
    def test_installs_into_localized_desktop_and_is_repeatable(self):
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory) / 'user'
            desktop = home / 'Рабочий стол'
            unit, launchers = installer.install(ROOT, home, desktop)
            installer.install(ROOT, home, desktop)
            self.assertEqual(len(list(desktop.glob('*.desktop'))), 2)
            text = unit.read_text()
            self.assertIn('--host 10.10.10.1 --port 8000 --workers 1', text)
            self.assertIn('Restart=on-failure', text)
            self.assertNotIn('User=root', text)
            self.assertNotIn('[Install]', text)
            self.assertTrue((home / '.local/lib/control-test/desktop_control.py').is_file())
            for launcher in launchers:
                self.assertTrue(launcher.stat().st_mode & 0o111)
                self.assertIn('Terminal=false', launcher.read_text())
                self.assertNotIn('sudo', launcher.read_text())

    def test_stop_checks_port_and_stops_manual_project_server(self):
        with patch.object(controller.subprocess, 'run', return_value=Mock(stdout='/home/aaf/project\n')) as run, \
             patch.object(controller, 'manual_server_pids', return_value=[123]) as pids, \
             patch.object(controller.os, 'kill') as kill, \
             patch.object(controller, 'server_reachable', side_effect=[True, False]), \
             patch.object(controller.time, 'sleep'), patch.object(controller, 'message') as message:
            controller.control('stop')
            pids.assert_called_once_with('/home/aaf/project')
            kill.assert_called_once_with(123, signal.SIGTERM)
            self.assertIn(call(['systemctl', '--user', 'stop', controller.UNIT], check=True), run.call_args_list)
            message.assert_called_once()

    def test_stop_does_not_report_success_when_other_server_remains(self):
        with patch.object(controller.subprocess, 'run', return_value=Mock(stdout='')), \
             patch.object(controller, 'manual_server_pids', return_value=[]), \
             patch.object(controller, 'server_reachable', return_value=True), \
             patch.object(controller.time, 'monotonic', side_effect=[0, 16]), \
             patch.object(controller, 'message') as message:
            with self.assertRaisesRegex(RuntimeError, 'Остановка не подтверждена'):
                controller.control('stop')
            message.assert_not_called()

    def test_only_matches_our_project_uvicorn_and_port(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            project = root / 'project'
            project.mkdir()
            entries = []
            commands = [
                ['python', '-m', 'uvicorn', 'app.main:app', '--host', '10.10.10.1', '--port', '8000'],
                ['python', '-m', 'uvicorn', 'other:app', '--host', '10.10.10.1', '--port', '8000'],
                ['python', '-m', 'uvicorn', 'app.main:app', '--host', '10.10.10.1', '--port', '9000'],
                ['python', '-m', 'uvicorn', 'app.main:app', '--host', '10.10.10.1', '--port', '8000'],
            ]
            for i, args in enumerate(commands, 100):
                entry = root / str(i)
                entry.mkdir()
                (entry / 'cmdline').write_bytes(('\0'.join(args) + '\0').encode())
                (entry / 'cwd').symlink_to(project if i != 103 else root)
                entries.append(entry)
            with patch.object(controller.Path, 'iterdir', return_value=iter(entries)):
                self.assertEqual(controller.manual_server_pids(str(project)), [100])

    def test_quotes_space_and_percent_in_paths(self):
        self.assertEqual(installer.unit_quote('/home/a b/100%'), '"/home/a b/100%%"')
        self.assertIn('"/home/a b/helper.py"', installer.launcher_content('start', Path('/home/a b/helper.py')))
