#!/usr/bin/env python3
"""Install read-only station monitor. Does not restart or reconfigure Wi-Fi/DNS/firewall."""
import argparse
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile

from wifi_guard import Control, ap_active, station_list, atomic_text

CONFIG = Path('/etc/control-test/wifi.json')
DEFAULT_INTERFACE = 'wlx001d43501e71'
DEFAULT_LEASE_FILE = '/var/lib/misc/control-test-dnsmasq.leases'

UNIT = '''[Unit]
Description=Control Test Wi-Fi station monitor (read-only)
After=NetworkManager.service
Wants=NetworkManager.service

[Service]
Type=simple
ExecStart=/usr/bin/python3 /usr/local/lib/control-test/wifi_guard.py
Restart=on-failure
RestartSec=3
RuntimeDirectory=control-test-wifi
RuntimeDirectoryMode=0755
StateDirectory=control-test-wifi
StateDirectoryMode=0700
UMask=0077
NoNewPrivileges=yes
ProtectSystem=strict
ProtectHome=yes
ReadWritePaths=/var/lib/control-test-wifi /run/control-test-wifi
PrivateTmp=yes
RestrictAddressFamilies=AF_UNIX AF_NETLINK
CapabilityBoundingSet=CAP_NET_ADMIN

[Install]
WantedBy=multi-user.target
'''


def monitor_config(interface=None, lease_file=None, path=CONFIG):
    """Preserve installed settings unless an option is explicitly supplied."""
    try:
        previous = json.loads(path.read_text())
    except FileNotFoundError:
        previous = {}
    except (OSError, ValueError) as exc:
        raise ValueError(f'Не удалось прочитать {path}: {exc}. Настройки не изменены.') from exc
    if not isinstance(previous, dict):
        raise ValueError(f'Некорректная конфигурация {path}. Настройки не изменены.')
    selected = interface if interface is not None else previous.get('interface', DEFAULT_INTERFACE)
    if not isinstance(selected, str) or not re.fullmatch(r'[a-zA-Z0-9_-]{1,15}', selected):
        raise ValueError('Недопустимый интерфейс')
    lease = lease_file if lease_file is not None else previous.get('lease_file', DEFAULT_LEASE_FILE)
    endpoint = (str(Path('/run/wpa_supplicant') / selected) if interface is not None
                else previous.get('control_socket', str(Path('/run/wpa_supplicant') / selected)))
    if not isinstance(lease, str) or not Path(lease).is_absolute():
        raise ValueError('Путь DHCP leases должен быть абсолютным')
    if not isinstance(endpoint, str) or not Path(endpoint).is_absolute():
        raise ValueError('Путь управляющего сокета должен быть абсолютным')
    return {**previous, 'interface': selected, 'control_socket': endpoint, 'lease_file': lease}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--interface', help='Интерфейс AP; по умолчанию из wifi.json, при первой установке ' + DEFAULT_INTERFACE)
    parser.add_argument('--lease-file', help='Файл DHCP leases; по умолчанию из wifi.json')
    args = parser.parse_args()
    if os.geteuid() != 0:
        raise SystemExit('Запустите установку через sudo; веб-сервис остаётся пользовательским.')
    try:
        config = monitor_config(args.interface, args.lease_file)
    except ValueError as exc:
        raise SystemExit(str(exc)) from exc
    interface = config['interface']
    if not (Path('/sys/class/net') / interface).exists():
        raise SystemExit(f'Сетевой интерфейс {interface} не найден. Проверьте nmcli device status, '
                         'lsusb и загрузку драйвера. Настройки не изменены.')
    if not shutil.which('iw'):
        raise SystemExit('Установите пакет iw')
    endpoint = Path(config['control_socket'])
    if not endpoint.exists():
        raise SystemExit(f'Интерфейс {interface} найден, но нет управляющего сокета {endpoint}. '
                         'Включите точку доступа control-test и повторите установку. Настройки не изменены.')
    # All capability checks happen before installing or restarting any service.
    with tempfile.TemporaryDirectory() as private, Control(endpoint, private) as ctrl, Control(endpoint, private) as events:
        if ctrl.request('PING') != 'PONG' or not ap_active(ctrl):
            raise SystemExit('Интерфейс не работает как точка доступа')
        events.ok('ATTACH')
        events.ok('DETACH')
        station_list(interface)
    lease = Path(config['lease_file'])
    if not lease.is_file():
        raise SystemExit('Не найден файл DHCP leases: ' + str(lease) + '. Подключите телефон или укажите --lease-file.')
    source = Path(__file__).resolve().parent / 'wifi_guard.py'
    atomic_text(Path('/usr/local/lib/control-test/wifi_guard.py'), source.read_text(), 0o644)
    atomic_text(CONFIG, json.dumps(config, indent=2) + '\n', 0o644)
    atomic_text(Path('/etc/systemd/system/control-test-wifi.service'), UNIT, 0o644)
    subprocess.run(['systemctl', 'daemon-reload'], check=True)
    subprocess.run(['systemctl', 'enable', 'control-test-wifi.service'], check=True)
    subprocess.run(['systemctl', 'restart', 'control-test-wifi.service'], check=True)
    print('Контроль Wi-Fi установлен. Wi-Fi, DNS и firewall не перезапускались.')
    print('Интерфейс:', interface, '| DHCP leases:', lease)
    print('Проверка: systemctl status control-test-wifi.service --no-pager')
    print('Список: http://10.10.10.1:8000/students')


if __name__ == '__main__':
    main()
