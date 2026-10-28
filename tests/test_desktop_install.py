import importlib.util
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

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

    def test_stop_only_stops_web_service(self):
        with patch.object(controller.subprocess, 'run') as run, patch.object(controller, 'message'):
            controller.control('stop')
            run.assert_called_once_with(['systemctl', '--user', 'stop', 'control-test-web.service'], check=True)

    def test_quotes_space_and_percent_in_paths(self):
        self.assertEqual(installer.unit_quote('/home/a b/100%'), '"/home/a b/100%%"')
        self.assertIn('"/home/a b/helper.py"', installer.launcher_content('start', Path('/home/a b/helper.py')))
