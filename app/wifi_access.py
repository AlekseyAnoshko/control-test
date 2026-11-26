"""Correlate trusted DHCP/station snapshots with direct clients; never trust browser MACs."""
from contextlib import contextmanager
import json
import math
import os
from pathlib import Path
import sqlite3
import time

STATUS_PATH = Path(os.environ.get('CONTROL_TEST_STATUS', '/run/control-test-wifi/status.json'))
DB_PATH = Path(os.environ.get('CONTROL_TEST_WIFI_DB',
               str(Path.home() / '.local/state/control-test/wifi.sqlite3')))


def snapshot():
    try:
        data = json.loads(STATUS_PATH.read_text())
        if not isinstance(data.get('stations'), list):
            raise ValueError('Invalid station snapshot')
        fresh = 0 <= time.time() - float(data['updated_at']) < 6
        data['healthy'] = bool(data.get('healthy') and fresh)
        if not fresh:
            data['error'] = 'Нет свежих данных от службы контроля Wi-Fi'
        return data
    except (OSError, ValueError, TypeError, KeyError):
        return {'healthy': False, 'error': 'Служба контроля Wi-Fi пока не настроена или недоступна', 'stations': []}


@contextmanager
def database():
    DB_PATH.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    db = sqlite3.connect(DB_PATH, timeout=3)
    db.execute('CREATE TABLE IF NOT EXISTS devices (mac TEXT PRIMARY KEY, first_seen REAL NOT NULL, name TEXT NOT NULL)')
    try:
        with db:
            db.execute('CREATE TABLE IF NOT EXISTS penalties '
                       '(mac TEXT PRIMARY KEY, cleared_at REAL NOT NULL DEFAULT 0, '
                       'event_at REAL NOT NULL DEFAULT 0, blocked_until REAL NOT NULL DEFAULT 0)')
            yield db
    finally:
        db.close()


def reset_session():
    """Forget registrations and penalties belonging to the previous test session."""
    with database() as db:
        db.execute('DELETE FROM devices')
        db.execute('DELETE FROM penalties')


def registered():
    with database() as db:
        return {mac: {'first_seen': first_seen, 'name': name}
                for mac, first_seen, name in db.execute('SELECT mac, first_seen, name FROM devices')}


def device_for_ip(data, ip):
    rows = [row for row in data['stations'] if row.get('ip') == ip]
    return rows[0] if len(rows) == 1 else None


def remember(ip, name=''):
    data = snapshot()
    row = device_for_ip(data, ip)
    if row is None or not data['healthy']:
        return None
    mac = row['mac']
    with database() as db:
        db.execute('INSERT OR IGNORE INTO devices VALUES (?, ?, ?)', (mac, time.time(), name))
        if name:
            db.execute('UPDATE devices SET name=? WHERE mac=?', (name[:100], mac))
    return mac


def reset_penalties(mac=None):
    """Forgive existing events without deleting registrations or test answers."""
    now = time.time()
    events = {row['mac']: row.get('last_disconnect_at', row.get('blocked_at', 0))
              for row in snapshot()['stations']}
    with database() as db:
        devices = list(db.execute('SELECT mac FROM devices' + (' WHERE mac=?' if mac else ''),
                                  (mac,) if mac else ()))
        for (device,) in devices:
            db.execute('INSERT INTO penalties (mac, cleared_at, event_at) VALUES (?, ?, ?) '
                       'ON CONFLICT(mac) DO UPDATE SET cleared_at=excluded.cleared_at, '
                       'event_at=MAX(penalties.event_at, excluded.event_at), blocked_until=0',
                       (device, now, max(now, events.get(device, 0))))
    return len(devices)


def remaining(row, registration, since=None, now=None):
    """Consume confirmed Wi-Fi events only inside the current active test window."""
    now = time.time() if now is None else now
    if since is None or not registration:
        return 0
    # New monitors report every confirmed break, including during an older interval.
    # The fallback keeps existing monitor installations compatible during an update.
    event = row.get('last_disconnect_at', row.get('blocked_at', 0))
    with database() as db:
        saved = db.execute('SELECT cleared_at, event_at, blocked_until FROM penalties WHERE mac=?',
                           (row['mac'],)).fetchone() or (0, 0, 0)
        cleared, previous_event, until = saved
        cutoff = max(since, registration['first_seen'], cleared)
        if event > previous_event:
            if event > cutoff and event >= until:
                until = event + 600
            db.execute('INSERT INTO penalties VALUES (?, ?, ?, ?) '
                       'ON CONFLICT(mac) DO UPDATE SET event_at=excluded.event_at, '
                       'blocked_until=excluded.blocked_until',
                       (row['mac'], cleared, event, until))
    return max(0, math.ceil(until - now))


def blocked(ip, since=None):
    if since is None:
        return 0
    data = snapshot()
    if not data['healthy']:
        return 0  # An old IP-to-MAC mapping cannot identify a current client reliably.
    row = device_for_ip(data, ip)
    if row is None:
        return 0
    return remaining(row, registered().get(row['mac']), since)


def students(since=None):
    data = snapshot()
    devices = registered()
    rows = []
    for row in data['stations']:
        registration = devices.get(row['mac'])
        if registration is None and not row.get('connected'):
            continue
        seconds = remaining(row, registration, since) if data['healthy'] else 0
        rows.append({**row, 'name': registration['name'] if registration else '',
                     'registered': registration is not None, 'remaining_seconds': seconds,
                     'status': ('blocked' if seconds else 'unknown' if not data['healthy'] else
                                'connected' if row.get('connected') else 'disconnected')})
    # Keep registered people visible if the monitor has not rediscovered them after reboot.
    known = {row['mac'] for row in rows}
    rows.extend({'mac': mac, 'name': value['name'], 'registered': True,
                 'remaining_seconds': 0, 'status': 'unknown'}
                for mac, value in devices.items() if mac not in known)
    return {**data, 'stations': rows, 'penalties_active': since is not None}


class WifiAccessMiddleware:
    def __init__(self, app, test_start=lambda: None):
        self.app = app
        self.test_start = test_start

    async def __call__(self, scope, receive, send):
        if scope['type'] in ('http', 'websocket'):
            ip = (scope.get('client') or ('', 0))[0]
            seconds = blocked(ip, self.test_start())
            if seconds:
                if scope['type'] == 'websocket':
                    await send({'type': 'websocket.accept'})
                    await send({'type': 'websocket.close', 'code': 4403, 'reason': 'Wi-Fi disconnected; site blocked'})
                else:
                    from starlette.responses import HTMLResponse
                    response = HTMLResponse(
                        '<!doctype html><html lang="ru"><meta charset="utf-8">'
                        '<meta name="viewport" content="width=device-width,initial-scale=1">'
                        '<title>Доступ временно закрыт</title>'
                        '<body style="font-family:Arial,sans-serif;color:#111;background:#f6f6f4;padding:5vw">'
                        '<h1>Доступ временно закрыт</h1><p>Зафиксирован разрыв Wi-Fi-соединения.</p>'
                        f'<p>Повторный вход через <strong>{seconds // 60}:{seconds % 60:02d}</strong>.</p>'
                        '<p>По истечении времени обновите страницу. Wi-Fi остаётся доступен.</p></body></html>',
                        status_code=403, headers={'Cache-Control': 'no-store', 'Retry-After': str(seconds)})
                    await response(scope, receive, send)
                return
            remember(ip)
        await self.app(scope, receive, send)
