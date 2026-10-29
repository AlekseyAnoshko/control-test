#!/usr/bin/env python3
"""Read-only Wi-Fi station monitor; records ten-minute site exclusion intervals."""
import json
import os
from pathlib import Path
import re
import signal
import socket
import subprocess
import tempfile
import time

MAC = re.compile(r'^[0-9a-f]{2}(?::[0-9a-f]{2}){5}$')
DURATION = 600
STATE = Path('/var/lib/control-test-wifi/state.json')
STATUS = Path('/run/control-test-wifi/status.json')
CONFIG = Path('/etc/control-test/wifi.json')


def atomic_json(path, value, mode=0o600):
    atomic_text(path, json.dumps(value, ensure_ascii=False), mode)


def atomic_text(path, text, mode=0o600):
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(dir=path.parent, prefix='.' + path.name)
    try:
        with os.fdopen(fd, 'w') as out:
            out.write(text)
            out.flush()
            os.fsync(out.fileno())
        os.chmod(name, mode)
        os.replace(name, path)
    finally:
        if os.path.exists(name):
            os.unlink(name)


class Guard:
    def __init__(self, records=None):
        self.records = records or {}
        self.pending = {}

    def connect(self, mac, now):
        if mac not in self.records and len(self.records) >= 4096:
            old = [key for key, value in self.records.items()
                   if not value.get('connected') and value.get('blocked_until', 0) <= now
                   and key not in self.pending]
            if not old:
                raise RuntimeError('Слишком много устройств в истории Wi-Fi')
            del self.records[min(old, key=lambda key: self.records[key].get('last_seen', 0))]
        row = self.records.setdefault(mac, {})
        row.update(connected=True, last_seen=now)

    def disconnect(self, mac, now):
        row = self.records.get(mac)
        if row and row.get('connected'):
            row['connected'] = False
            if row.get('blocked_until', 0) > now:
                return
            row['disconnected_at'] = now
            # Defer enforcement briefly to discard AP shutdown's mass disconnect events.
            self.pending.setdefault(mac, now)

    def unavailable(self):
        self.pending.clear()
        for row in self.records.values():
            row['connected'] = False

    def tick(self, now, save):
        for mac, row in self.records.items():
            until = row.get('blocked_until', 0)
            if until and until <= now:
                row['blocked_until'] = 0
                save()
        for mac, since in list(self.pending.items()):
            if now - since < 2:
                continue
            row = self.records[mac]
            if row.get('blocked_until', 0) <= now:
                row.update(blocked_until=since + DURATION, blocked_at=since)
                save()  # A monitor restart must keep the original deadline.
            del self.pending[mac]

    def event(self, event, now):
        event = re.sub(r'^<\d+>', '', event.strip())
        parts = event.split()
        if not parts:
            return
        if parts[0] in ('AP-DISABLED', 'CTRL-EVENT-TERMINATING', 'INTERFACE-DISABLED'):
            self.unavailable()
        elif len(parts) >= 2 and MAC.fullmatch(parts[1].lower()):
            mac = parts[1].lower()
            if parts[0] == 'AP-STA-CONNECTED':
                self.connect(mac, now)
            elif parts[0] == 'AP-STA-DISCONNECTED':
                self.disconnect(mac, now)


def load_records(path=STATE):
    try:
        data = json.loads(path.read_text())
    except FileNotFoundError:
        return {}
    if not isinstance(data, dict) or len(data) > 4096:
        raise ValueError('Invalid Wi-Fi state file')
    for mac, row in data.items():
        if not MAC.fullmatch(mac) or not isinstance(row, dict):
            raise ValueError('Invalid Wi-Fi state entry')
        for field in ('blocked_until', 'blocked_at', 'last_seen', 'disconnected_at'):
            if field in row and (type(row[field]) not in (int, float) or not 0 <= row[field] < 1e12):
                raise ValueError('Invalid Wi-Fi timestamp')
    return data


def persist(guard):
    atomic_json(STATE, guard.records)



class Control:
    def __init__(self, endpoint, private):
        self.directory = tempfile.TemporaryDirectory(dir=private)
        self.path = Path(self.directory.name) / 'ctrl'
        self.sock = socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM)
        try:
            self.sock.bind(str(self.path))
            self.sock.connect(str(endpoint))
            self.sock.settimeout(2)
        except BaseException:
            self.close()
            raise

    def request(self, command):
        self.sock.send(command.encode())
        response = self.sock.recv(65535).decode().strip()
        if response.startswith(('FAIL', 'UNKNOWN')):
            raise RuntimeError('wpa_supplicant rejected ' + command.split()[0])
        return response

    def ok(self, command):
        if self.request(command) != 'OK':
            raise RuntimeError('Unexpected wpa_supplicant reply')

    def close(self):
        self.sock.close()
        self.directory.cleanup()

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()


def station_list(interface):
    output = subprocess.run(['iw', 'dev', interface, 'station', 'dump'],
                            check=True, capture_output=True, text=True, timeout=3).stdout
    return {m.lower() for m in re.findall(r'^Station ([0-9a-fA-F:]{17}) ', output, re.MULTILINE)
            if MAC.fullmatch(m.lower())}


def ap_active(cmd):
    lines = cmd.request('STATUS').splitlines()
    return 'mode=AP' in lines and 'wpa_state=COMPLETED' in lines


def lease_map(path):
    result = {}
    try:
        for line in path.read_text().splitlines():
            fields = line.split()
            if len(fields) >= 4 and MAC.fullmatch(fields[1].lower()):
                expires = int(fields[0])
                if expires == 0 or expires > time.time():
                    result[fields[1].lower()] = {'ip': fields[2], 'hostname': '' if fields[3] == '*' else fields[3]}
    except (OSError, ValueError):
        pass
    return result


def publish(guard, config, healthy, error=''):
    now = time.time()
    leases = lease_map(Path(config['lease_file']))
    rows = []
    for mac, info in sorted(guard.records.items()):
        rows.append({'mac': mac, **info, **leases.get(mac, {})})
    atomic_json(STATUS, {'updated_at': now, 'healthy': healthy, 'error': error,
                        'interface': config['interface'], 'ban_seconds': DURATION, 'stations': rows}, 0o644)


def main():
    guard = Guard(load_records())
    guard.unavailable()
    persist(guard)
    config = json.loads(CONFIG.read_text())
    private = STATUS.parent / 'private'
    private.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.chmod(private, 0o700)
    running = True
    def stop(*_):
        nonlocal running
        running = False
    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    while running:
        try:
            with Control(config['control_socket'], private) as cmd, Control(config['control_socket'], private) as events:
                events.ok('ATTACH')
                if not ap_active(cmd):
                    raise RuntimeError('Точка доступа не активна')
                guard.unavailable()
                for mac in station_list(config['interface']):
                    guard.connect(mac, time.time())
                events.sock.settimeout(0.2)
                last_poll = last_saved = 0
                while running:
                    try:
                        event = events.sock.recv(65535).decode()
                        guard.event(event, time.time())
                    except socket.timeout:
                        pass
                    now = time.time()
                    if now - last_poll >= 1:
                        if not ap_active(cmd):
                            raise RuntimeError('Точка доступа не активна')
                        actual = station_list(config['interface'])
                        for mac, row in list(guard.records.items()):
                            if row.get('connected') and mac not in actual:
                                guard.disconnect(mac, now)
                        for mac in actual:
                            guard.connect(mac, now)
                        guard.tick(now, lambda: persist(guard))
                        if now - last_saved >= 30:
                            persist(guard)
                            last_saved = now
                        publish(guard, config, True)
                        last_poll = now
        except (OSError, RuntimeError, ValueError, subprocess.SubprocessError) as exc:
            guard.unavailable()
            publish(guard, config, False, str(exc))
            print(str(exc), flush=True)
            if running:
                time.sleep(1)
    publish(guard, config, False, 'Контроль Wi-Fi остановлен')


if __name__ == '__main__':
    main()
