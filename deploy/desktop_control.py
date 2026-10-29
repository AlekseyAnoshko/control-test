#!/usr/bin/env python3
"""Desktop controls for the current user's Control Test web service."""
import json
import os
from pathlib import Path
import signal
import socket
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.request

UNIT = 'control-test-web.service'
URL = 'http://10.10.10.1:8000'


def message(text, error=False):
    print(text, file=sys.stderr if error else sys.stdout)
    if shutil.which('zenity') and error:
        subprocess.run(['zenity', '--error', '--title=Control Test', '--text', text], check=False)
    elif shutil.which('kdialog') and error:
        subprocess.run(['kdialog', '--error', text, '--title', 'Control Test'], check=False)
    elif shutil.which('notify-send'):
        subprocess.run(['notify-send', 'Control Test', text], check=False)


def server_reachable():
    try:
        with socket.create_connection(('10.10.10.1', 8000), timeout=0.5):
            return True
    except OSError:
        return False


def manual_server_pids(project):
    """Only our user's explicit uvicorn invocation in this service's project."""
    if not project:
        return []
    found = []
    for entry in Path('/proc').iterdir():
        if not entry.name.isdigit():
            continue
        try:
            if entry.stat().st_uid != os.getuid():
                continue
            if (entry / 'cwd').resolve() != Path(project).resolve():
                continue
            args = (entry / 'cmdline').read_bytes().decode().strip('\0').split('\0')
            # Accept python -m uvicorn and the venv's uvicorn console script.
            is_uvicorn = (len(args) > 3 and args[1:3] == ['-m', 'uvicorn']
                          and args[3] == 'app.main:app') or (
                          len(args) > 2 and Path(args[1]).name == 'uvicorn'
                          and args[2] == 'app.main:app')
            def option(name):
                return args[args.index(name) + 1] if name in args and args.index(name) + 1 < len(args) else None
            if is_uvicorn and option('--port') == '8000' and option('--host') in ('10.10.10.1', '0.0.0.0'):
                found.append(int(entry.name))
        except (OSError, ValueError, UnicodeError):
            continue
    return found


def stop_server():
    result = subprocess.run(
        ['systemctl', '--user', 'show', UNIT, '--property=WorkingDirectory', '--value'],
        check=True, capture_output=True, text=True)
    project = result.stdout.strip()
    subprocess.run(['systemctl', '--user', 'stop', UNIT], check=True)
    for pid in manual_server_pids(project):
        try:
            os.kill(pid, signal.SIGTERM)
        except ProcessLookupError:
            pass
    deadline = time.monotonic() + 15
    while server_reachable():
        if time.monotonic() >= deadline:
            raise RuntimeError('Порт 10.10.10.1:8000 всё ещё доступен. Возможно, работает другой экземпляр сервера. '
                               'Проверьте: ss -ltnp sport = :8000. Остановка не подтверждена.')
        time.sleep(0.2)
    message('Тестирование остановлено. Порт сервера закрыт. Открытая страница может оставаться в браузере до обновления.')


def control(action):
    if action == 'stop':
        stop_server()
        return
    if action != 'start':
        raise ValueError('Expected start or stop')
    subprocess.run(['systemctl', '--user', 'reset-failed', UNIT], check=False, capture_output=True)
    subprocess.run(['systemctl', '--user', 'start', UNIT], check=True)
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    deadline = time.monotonic() + 15
    while time.monotonic() < deadline:
        try:
            with opener.open(URL + '/healthz', timeout=1) as response:
                healthy = json.load(response).get('status') == 'ok'
            active = subprocess.run(['systemctl', '--user', 'is-active', '--quiet', UNIT], check=False).returncode == 0
            if healthy and active:
                subprocess.Popen(['xdg-open', URL + '/teacher'], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                return
        except (OSError, ValueError, urllib.error.URLError):
            pass
        time.sleep(0.3)
    raise RuntimeError('Сайт пока недоступен. Проверьте, что Wi-Fi control-test включён и ноутбуку назначен адрес 10.10.10.1. Журнал: journalctl --user -u control-test-web.service -n 40 --no-pager')


def main():
    try:
        control(sys.argv[1] if len(sys.argv) > 1 else 'start')
    except (OSError, ValueError, RuntimeError, subprocess.CalledProcessError) as exc:
        message(str(exc), error=True)
        return 1
    return 0


if __name__ == '__main__':
    sys.exit(main())
