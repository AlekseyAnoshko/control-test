import importlib.util
import io
import os
from pathlib import Path
import signal
import socket
import subprocess
import sys
import tempfile
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
    def test_installs_into_localized_desktop_and_replaces_old_helper_copy(self):
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory) / 'user'
            desktop = home / 'Рабочий стол'
            helper = home / '.local/lib/control-test/desktop_control.py'
            helper.parent.mkdir(parents=True)
            helper.write_text('# old helper')
            unit, launchers = installer.install(ROOT, home, desktop)
            installer.install(ROOT, home, desktop)
            self.assertEqual(len(list(desktop.glob('*.desktop'))), 2)
            text = unit.read_text()
            self.assertIn('--host 10.10.10.1 --port 8000 --workers 1', text)
            self.assertIn('--timeout-graceful-shutdown 5', text)
            self.assertIn('KillMode=control-group', text)
            self.assertIn('Restart=on-failure', text)
            self.assertNotIn('User=root', text)
            self.assertNotIn('[Install]', text)
            self.assertTrue(helper.is_symlink())
            self.assertEqual(helper.resolve(), ROOT / 'deploy/desktop_control.py')
            for launcher in launchers:
                self.assertTrue(launcher.stat().st_mode & 0o111)
                self.assertIn('Terminal=false', launcher.read_text())
                self.assertNotIn('sudo', launcher.read_text())

    def test_stop_checks_local_listeners_after_stopping_both_kinds_of_server(self):
        events = []
        def systemctl(*args):
            events.append(args[0])
            return Mock(stdout='/home/aaf/project\n')
        with patch.object(controller, 'systemctl', side_effect=systemctl), \
             patch.object(controller, 'stop_manual_servers', side_effect=lambda p: events.append(p)), \
             patch.object(controller, 'server_listening', side_effect=lambda: events.append('listeners') or False), \
             patch.object(controller, 'message') as message:
            controller.control('stop')
            self.assertEqual(events, ['show', 'stop', '/home/aaf/project', 'listeners'])
            message.assert_called_once()

    def test_stop_does_not_report_success_when_other_server_remains(self):
        with patch.object(controller, 'systemctl', return_value=Mock(stdout='/project')), \
             patch.object(controller, 'stop_manual_servers'), \
             patch.object(controller, 'server_listening', return_value=True), \
             patch.object(controller, 'port_details', return_value='another-server pid=456'), \
             patch.object(controller, 'message') as message:
            with self.assertRaisesRegex(RuntimeError, 'another-server pid=456'):
                controller.control('stop')
            message.assert_not_called()

    def test_listener_check_ignores_transparent_proxy_and_loopback_only_service(self):
        for output, expected in [
            ('', False),
            ('LISTEN 0 4096 *:46705 *:*\n', False),
            ('LISTEN 0 32 127.0.0.1:8000 0.0.0.0:*\n', False),
            ('LISTEN 0 32 10.10.10.1:8000 0.0.0.0:*\n', True),
            ('LISTEN 0 32 0.0.0.0:8000 0.0.0.0:*\n', True),
            ('LISTEN 0 32 [::]:8000 [::]:*\n', True),
            ('LISTEN 0 32 *:8000 *:*\n', True),
        ]:
            with self.subTest(output=output), \
                 patch.object(controller.subprocess, 'run', return_value=Mock(stdout=output)):
                self.assertEqual(controller.server_listening(), expected)

    def test_detects_real_local_listener_and_its_closure(self):
        with socket.socket() as listener:
            try:
                listener.bind(('0.0.0.0', 8000))
            except OSError as exc:
                self.skipTest(f'Port 8000 unavailable in test environment: {exc}')
            listener.listen()
            self.assertTrue(controller.server_listening())
        self.assertFalse(controller.server_listening())

    def test_only_matches_our_project_uvicorn_and_port_in_different_cli_styles(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            project = root / 'project'
            project.mkdir()
            entries = []
            commands = [
                ['python', '-m', 'uvicorn', 'app.main:app', '--host', '10.10.10.1', '--port', '8000'],
                ['python', '-m', 'uvicorn', 'other:app', '--host', '10.10.10.1', '--port', '8000'],
                ['python', '-m', 'uvicorn', 'app.main:app', '--port', '9000'],
                ['python', '-m', 'uvicorn', 'app.main:app', '--port', '8000'],  # another cwd
                ['python', '-m', 'uvicorn', '--port=8000', '--host=0.0.0.0', 'app.main:app'],
                ['/project/.venv/bin/uvicorn', '--port', '8000', 'app.main:app'],
                ['python', '/project/.venv/bin/uvicorn', 'app.main:app'],
                ['python', '-u', '-m', 'uvicorn', 'app.main:app', '--port', '8000'],
            ]
            for i, args in enumerate(commands, 100):
                entry = root / str(i)
                entry.mkdir()
                (entry / 'cmdline').write_bytes(('\0'.join(args) + '\0').encode())
                (entry / 'cwd').symlink_to(project if i != 103 else root)
                entries.append(entry)
            with patch.object(controller.Path, 'iterdir', return_value=iter(entries)):
                self.assertEqual(controller.manual_server_pids(str(project)), [100, 104, 105, 106, 107])
            with patch.object(controller.os, 'getuid', return_value=os.getuid() + 1):
                self.assertFalse(controller.is_project_server(entries[0], str(project)))

    def test_stops_real_manual_server_even_when_it_ignores_sigterm(self):
        with tempfile.TemporaryDirectory() as directory:
            project = Path(directory)
            script = project / 'uvicorn'
            script.write_text('import signal, time\nsignal.signal(signal.SIGTERM, signal.SIG_IGN)\n'
                              'print("ready", flush=True)\ntime.sleep(60)\n')
            process = subprocess.Popen([sys.executable, str(script), 'app.main:app', '--port=8000'],
                                       cwd=project, stdout=subprocess.PIPE, text=True)
            try:
                self.assertEqual(process.stdout.readline().strip(), 'ready')
                controller.stop_manual_servers(str(project), grace=0.05)
                self.assertEqual(process.wait(timeout=2), -signal.SIGKILL)
            finally:
                if process.poll() is None:
                    process.kill()
                    process.wait()
                process.stdout.close()

    def test_revalidates_process_before_sending_signal(self):
        with patch.object(controller, 'manual_server_pids', return_value=[123]), \
             patch.object(controller.os, 'pidfd_open', return_value=42), \
             patch.object(controller.os, 'close') as close, \
             patch.object(controller, 'is_project_server', return_value=False), \
             patch.object(controller.signal, 'pidfd_send_signal') as send:
            controller.stop_manual_servers('/project')
            send.assert_not_called()
            close.assert_called_once_with(42)

    def test_start_opens_teacher_only_after_health_and_service_are_ready(self):
        with patch.object(controller, 'systemctl', return_value=Mock(returncode=0)) as systemctl, \
             patch.object(controller.urllib.request, 'build_opener') as build, \
             patch.object(controller.subprocess, 'Popen') as open_page:
            build.return_value.open.return_value = io.BytesIO(b'{"status": "ok"}')
            controller.control('start')
            self.assertIn(call('start', controller.UNIT), systemctl.call_args_list)
            self.assertEqual(open_page.call_args.args[0], ['xdg-open', controller.URL + '/teacher'])

    def test_failed_new_start_is_stopped_and_does_not_open_browser(self):
        with patch.object(controller, 'systemctl', return_value=Mock(returncode=3)) as systemctl, \
             patch.object(controller, 'server_listening', return_value=False), \
             patch.object(controller.time, 'monotonic', side_effect=[0, 16]), \
             patch.object(controller, 'port_details', return_value=''), \
             patch.object(controller.subprocess, 'Popen') as open_page:
            with self.assertRaisesRegex(RuntimeError, 'Сайт пока недоступен'):
                controller.control('start')
            systemctl.assert_called_with('stop', controller.UNIT)
            open_page.assert_not_called()

    def test_busy_port_does_not_start_another_instance(self):
        with patch.object(controller, 'systemctl', return_value=Mock(returncode=3)) as systemctl, \
             patch.object(controller, 'server_listening', return_value=True), \
             patch.object(controller, 'port_details', return_value='pid=123'):
            with self.assertRaisesRegex(RuntimeError, 'pid=123'):
                controller.control('start')
            self.assertEqual(systemctl.call_count, 1)

    def test_command_lock_is_released_even_after_error(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(controller.Path, 'home', return_value=Path(directory)):
            with self.assertRaisesRegex(RuntimeError, 'failure'):
                with controller.command_lock():
                    raise RuntimeError('failure')
            with controller.command_lock():
                pass

    def test_second_desktop_command_cannot_run_while_first_holds_lock(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(controller.Path, 'home', return_value=Path(directory)):
            with controller.command_lock():
                with patch.object(controller.time, 'monotonic', side_effect=[0, 46]):
                    with self.assertRaisesRegex(RuntimeError, 'Предыдущая команда'):
                        with controller.command_lock():
                            self.fail('A concurrent command acquired the lock')

    def test_systemd_error_includes_stderr(self):
        error = subprocess.CalledProcessError(1, ['systemctl'], stderr='Access denied')
        with patch.object(controller.subprocess, 'run', side_effect=error):
            with self.assertRaisesRegex(RuntimeError, 'Access denied'):
                controller.systemctl('stop', controller.UNIT)

    def test_quotes_space_and_percent_in_paths(self):
        self.assertEqual(installer.unit_quote('/home/a b/100%'), '"/home/a b/100%%"')
        self.assertIn('"/home/a b/helper.py"', installer.launcher_content('start', Path('/home/a b/helper.py')))
