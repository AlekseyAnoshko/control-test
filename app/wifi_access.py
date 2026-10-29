"""Correlate trusted DHCP/station snapshots with direct clients; never trust browser MACs."""
from contextlib import contextmanager
import json
import math
import os
from pathlib import Path
import sqlite3
import time

STATUS_PATH = Path('/run/control-test-wifi/status.json')
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
            yield db
    finally:
        db.close()


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


def remaining(row, registration, now=None):
    now = time.time() if now is None else now
    if not registration or registration['first_seen'] > row.get('blocked_at', 0):
        return 0
    return max(0, math.ceil(row.get('blocked_until', 0) - now))


def blocked(ip):
    data = snapshot()
    if not data['healthy']:
        return 0  # An old IP-to-MAC mapping cannot identify a current client reliably.
    row = device_for_ip(data, ip)
    if row is None:
        return 0
    return remaining(row, registered().get(row['mac']))


def students():
    data = snapshot()
    devices = registered()
    rows = []
    for row in data['stations']:
        registration = devices.get(row['mac'])
        seconds = remaining(row, registration)
        rows.append({**row, 'name': registration['name'] if registration else '',
                     'registered': registration is not None, 'remaining_seconds': seconds,
                     'status': ('blocked' if seconds else 'unknown' if not data['healthy'] else
                                'connected' if row.get('connected') else 'disconnected')})
    # Keep registered people visible if the monitor has not rediscovered them after reboot.
    known = {row['mac'] for row in rows}
    rows.extend({'mac': mac, 'name': value['name'], 'registered': True,
                 'remaining_seconds': 0, 'status': 'unknown'}
                for mac, value in devices.items() if mac not in known)
    return {**data, 'stations': rows}


class WifiAccessMiddleware:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope['type'] in ('http', 'websocket'):
            ip = (scope.get('client') or ('', 0))[0]
            seconds = blocked(ip)
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
