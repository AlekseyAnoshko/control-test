import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('install_wifi_monitor', ROOT / 'deploy/install_wifi_monitor.py')
installer = importlib.util.module_from_spec(spec)
with patch.object(sys, 'path', [str(ROOT / 'deploy'), *sys.path]):
    spec.loader.exec_module(installer)


class MonitorConfigTests(unittest.TestCase):
    def test_fresh_install_uses_new_adapter(self):
        with tempfile.TemporaryDirectory() as directory:
            config = installer.monitor_config(path=Path(directory) / 'missing.json')
        self.assertEqual(config, {
            'interface': 'wlx001d43501e71',
            'control_socket': '/run/wpa_supplicant/wlx001d43501e71',
            'lease_file': '/var/lib/misc/control-test-dnsmasq.leases',
        })

    def test_reinstall_preserves_installed_interface_socket_and_custom_leases(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'wifi.json'
            existing = {'interface': 'classroom0', 'control_socket': '/run/custom/classroom0',
                        'lease_file': '/var/lib/misc/dnsmasq.leases', 'custom_setting': True}
            path.write_text(json.dumps(existing))
            self.assertEqual(installer.monitor_config(path=path), existing)

    def test_explicit_adapter_change_rebuilds_socket_and_keeps_leases(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'wifi.json'
            path.write_text(json.dumps({'interface': 'wlx00127b21755b',
                                        'control_socket': '/run/wpa_supplicant/wlx00127b21755b',
                                        'lease_file': '/var/lib/misc/dnsmasq.leases'}))
            config = installer.monitor_config(interface='wlx001d43501e71', path=path)
            self.assertEqual(config['interface'], 'wlx001d43501e71')
            self.assertEqual(config['control_socket'], '/run/wpa_supplicant/wlx001d43501e71')
            self.assertEqual(config['lease_file'], '/var/lib/misc/dnsmasq.leases')

    def test_lease_override_preserves_adapter_and_socket(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'wifi.json'
            path.write_text(json.dumps({'interface': 'classroom0', 'control_socket': '/run/custom/socket',
                                        'lease_file': '/old/leases'}))
            config = installer.monitor_config(lease_file='/new/leases', path=path)
            self.assertEqual(config, {'interface': 'classroom0', 'control_socket': '/run/custom/socket',
                                      'lease_file': '/new/leases'})

    def test_invalid_saved_configuration_is_not_silently_replaced(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'wifi.json'
            for content in ('{broken', '[]', '{"interface": null}', '{"lease_file": "relative"}',
                            '{"control_socket": 123}'):
                with self.subTest(content=content):
                    path.write_text(content)
                    with self.assertRaises(ValueError):
                        installer.monitor_config(path=path)
                    self.assertEqual(path.read_text(), content)

    def test_invalid_explicit_interface_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'wifi.json'
            for interface in ('../wlan0', '', 'wlan0;reboot', 'x' * 16):
                with self.subTest(interface=interface), self.assertRaises(ValueError):
                    installer.monitor_config(interface=interface, path=path)

    def test_missing_network_interface_never_installs_or_restarts_anything(self):
        config = {'interface': 'wlx001d43501e71', 'control_socket': '/run/wpa_supplicant/wlx001d43501e71',
                  'lease_file': '/var/lib/misc/dnsmasq.leases'}
        with patch.object(sys, 'argv', ['install_wifi_monitor.py']), \
             patch.object(installer.os, 'geteuid', return_value=0), \
             patch.object(installer, 'monitor_config', return_value=config), \
             patch.object(installer.Path, 'exists', return_value=False), \
             patch.object(installer, 'atomic_text') as write, \
             patch.object(installer.subprocess, 'run') as run:
            with self.assertRaisesRegex(SystemExit, 'Сетевой интерфейс wlx001d43501e71 не найден'):
                installer.main()
            write.assert_not_called()
            run.assert_not_called()
