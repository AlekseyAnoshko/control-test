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


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--interface', default='wlx00127b21755b')
    parser.add_argument('--lease-file', default='/var/lib/misc/control-test-dnsmasq.leases')
    args = parser.parse_args()
    if os.geteuid() != 0:
        raise SystemExit('Запустите установку через sudo; веб-сервис остаётся пользовательским.')
    if not re.fullmatch(r'[a-zA-Z0-9_-]{1,15}', args.interface):
        raise SystemExit('Недопустимый интерфейс')
    if not shutil.which('iw'):
        raise SystemExit('Установите пакет iw')
    endpoint = Path('/run/wpa_supplicant') / args.interface
    if not endpoint.exists():
        raise SystemExit('Не найден управляющий сокет ' + str(endpoint) + '. Включите control-test и повторите установку.')
    # All capability checks happen before installing or restarting any service.
    with tempfile.TemporaryDirectory() as private, Control(endpoint, private) as ctrl, Control(endpoint, private) as events:
        if ctrl.request('PING') != 'PONG' or not ap_active(ctrl):
            raise SystemExit('Интерфейс не работает как точка доступа')
        events.ok('ATTACH')
        events.ok('DETACH')
        station_list(args.interface)
    lease = Path(args.lease_file)
    if not lease.is_absolute():
        raise SystemExit('Путь DHCP leases должен быть абсолютным')
    if not lease.is_file():
        raise SystemExit('Не найден файл DHCP leases: ' + str(lease) + '. Подключите телефон или укажите --lease-file.')
    source = Path(__file__).resolve().parent / 'wifi_guard.py'
    atomic_text(Path('/usr/local/lib/control-test/wifi_guard.py'), source.read_text(), 0o644)
    config = {'interface': args.interface, 'control_socket': str(endpoint), 'lease_file': str(lease)}
    atomic_text(Path('/etc/control-test/wifi.json'), json.dumps(config, indent=2) + '\n', 0o644)
    atomic_text(Path('/etc/systemd/system/control-test-wifi.service'), UNIT, 0o644)
    subprocess.run(['systemctl', 'daemon-reload'], check=True)
    subprocess.run(['systemctl', 'enable', 'control-test-wifi.service'], check=True)
    subprocess.run(['systemctl', 'restart', 'control-test-wifi.service'], check=True)
    print('Контроль Wi-Fi установлен. Wi-Fi, DNS и firewall не перезапускались.')
    print('Проверка: systemctl status control-test-wifi.service --no-pager')
    print('Список: http://10.10.10.1:8000/students')


if __name__ == '__main__':
    main()
