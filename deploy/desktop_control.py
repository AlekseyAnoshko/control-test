#!/usr/bin/env python3
"""Desktop controls for the current user's Control Test web service."""
from contextlib import contextmanager
import fcntl
import json
import os
from pathlib import Path
import select
import signal
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.request

UNIT = 'control-test-web.service'
URL = 'http://10.10.10.1:8000'
JOURNAL = 'journalctl --user -u control-test-web.service -n 40 --no-pager'


def message(text, error=False):
    print(text, file=sys.stderr if error else sys.stdout)
    if shutil.which('zenity') and error:
        subprocess.run(['zenity', '--error', '--title=Control Test', '--text', text], check=False)
    elif shutil.which('kdialog') and error:
        subprocess.run(['kdialog', '--error', text, '--title', 'Control Test'], check=False)
    elif shutil.which('notify-send'):
        subprocess.run(['notify-send', 'Control Test', text], check=False)


def systemctl(*args, check=True):
    try:
        return subprocess.run(['systemctl', '--user', *args], check=check,
                              capture_output=True, text=True, timeout=25)
    except subprocess.CalledProcessError as exc:
        detail = (exc.stderr or exc.stdout or str(exc)).strip()
        raise RuntimeError(f'Ошибка управления сервером: {detail}\nЖурнал: {JOURNAL}') from exc
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError(f'Сервис не завершил команду за 25 секунд. Журнал: {JOURNAL}') from exc


@contextmanager
def command_lock():
    """Serialize desktop clicks so a delayed start cannot race with a stop."""
    directory = Path.home() / '.cache/control-test'
    directory.mkdir(parents=True, exist_ok=True)
    with (directory / 'desktop.lock').open('a') as lock:
        deadline = time.monotonic() + 45
        while True:
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                if time.monotonic() >= deadline:
                    raise RuntimeError('Предыдущая команда управления сервером ещё выполняется. '
                                       'Повторите нажатие после её завершения.')
                time.sleep(0.2)
        try:
            yield
        finally:
            fcntl.flock(lock, fcntl.LOCK_UN)


def server_listening():
    # A successful connect() can be the transparent VPN proxy, not our server.
    # Inspect the kernel's local listeners instead of making a network request.
    result = subprocess.run(['ss', '-H', '-ltn', 'sport = :8000'],
                            check=True, capture_output=True, text=True, timeout=3)
    for line in result.stdout.splitlines():
        fields = line.split()
        if len(fields) >= 4:
            host, _, port = fields[3].rpartition(':')
            if port == '8000' and host.strip('[]') in ('10.10.10.1', '0.0.0.0', '*', '::'):
                return True
    return False


def is_project_server(entry, project):
    """Match this user's project, app and port across uvicorn CLI styles."""
    try:
        if entry.stat().st_uid != os.getuid() or (entry / 'cwd').resolve() != Path(project).resolve():
            return False
        args = (entry / 'cmdline').read_bytes().decode().rstrip('\0').split('\0')
        if (Path(args[0]).name.startswith(('python', 'pypy')) and '-m' in args
                and args[args.index('-m') + 1:args.index('-m') + 2] == ['uvicorn']):
            app_args = args[args.index('-m') + 2:]
        elif Path(args[0]).name == 'uvicorn':
            app_args = args[1:]
        elif len(args) > 1 and Path(args[1]).name == 'uvicorn':
            app_args = args[2:]
        else:
            return False
        port = '8000'  # uvicorn's default when the flag is omitted
        for index, arg in enumerate(app_args):
            if arg == '--port' and index + 1 < len(app_args):
                port = app_args[index + 1]
            elif arg.startswith('--port='):
                port = arg.split('=', 1)[1]
        return 'app.main:app' in app_args and port == '8000'
    except (OSError, ValueError, UnicodeError):
        return False


def manual_server_pids(project):
    if not project:
        return []
    return [int(entry.name) for entry in Path('/proc').iterdir()
            if entry.name.isdigit() and is_project_server(entry, project)]


def wait_for_exit(handles, timeout):
    pending = set(handles)
    deadline = time.monotonic() + timeout
    while pending:
        ready, _, _ = select.select(list(pending), [], [], max(0, deadline - time.monotonic()))
        pending.difference_update(ready)
        if not ready:
            break
    return pending


def stop_manual_servers(project, grace=5):
    # pidfds identify a process even if its PID is later reused by another app.
    handles = {}
    try:
        for pid in manual_server_pids(project):
            try:
                handle = os.pidfd_open(pid)
            except ProcessLookupError:
                continue
            handles[handle] = pid
            if not is_project_server(Path('/proc') / str(pid), project):
                os.close(handle)
                del handles[handle]
                continue
            try:
                signal.pidfd_send_signal(handle, signal.SIGTERM)
            except ProcessLookupError:
                pass
        pending = wait_for_exit(handles, grace)
        for handle in pending:
            try:
                signal.pidfd_send_signal(handle, signal.SIGKILL)
            except ProcessLookupError:
                pass
        pending = wait_for_exit(pending, 2)
        if pending:
            pids = ', '.join(str(handles[handle]) for handle in pending)
            raise RuntimeError(f'Остановка не подтверждена: процессы {pids} не завершились. Журнал: {JOURNAL}')
    finally:
        for handle in handles:
            os.close(handle)


def port_details():
    try:
        result = subprocess.run(['ss', '-ltnp', 'sport = :8000'],
                                capture_output=True, text=True, timeout=3)
        return result.stdout.strip() if result.returncode == 0 else ''
    except (OSError, subprocess.TimeoutExpired):
        return ''


def stop_server():
    project = systemctl('show', UNIT, '--property=WorkingDirectory', '--value').stdout.strip()
    systemctl('stop', UNIT)
    stop_manual_servers(project)
    if server_listening():
        raise RuntimeError('Остановка не подтверждена: 10.10.10.1:8000 всё ещё принимает подключения. '
                           'Другие приложения автоматически не завершаются.\n'
                           + port_details() + '\nЖурнал: ' + JOURNAL)
    message('Тестирование остановлено. Процессы сервера завершены. '
            'Открытая страница может оставаться в браузере до обновления.')


def control(action):
    if action == 'stop':
        stop_server()
        return
    if action != 'start':
        raise ValueError('Expected start or stop')
    already_active = systemctl('is-active', '--quiet', UNIT, check=False).returncode == 0
    if not already_active and server_listening():
        raise RuntimeError('Не удалось запустить сервер: адрес 10.10.10.1:8000 уже занят. '
                           'Если это ручной экземпляр Control Test, нажмите «Остановить тестирование», '
                           'затем запустите снова.\n' + port_details())
    systemctl('reset-failed', UNIT, check=False)
    systemctl('start', UNIT)
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    deadline = time.monotonic() + 15
    while time.monotonic() < deadline:
        try:
            with opener.open(URL + '/healthz', timeout=1) as response:
                healthy = json.load(response).get('status') == 'ok'
            active = systemctl('is-active', '--quiet', UNIT, check=False).returncode == 0
            if healthy and active:
                subprocess.Popen(['xdg-open', URL + '/teacher'], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                return
        except (OSError, ValueError, urllib.error.URLError):
            pass
        time.sleep(0.3)
    if not already_active:
        systemctl('stop', UNIT)
    raise RuntimeError('Сайт пока недоступен. Проверьте, что Wi-Fi control-test включён '
                       'и ноутбуку назначен адрес 10.10.10.1.\n' + port_details() + '\nЖурнал: ' + JOURNAL)


def main():
    try:
        with command_lock():
            control(sys.argv[1] if len(sys.argv) > 1 else 'start')
    except (OSError, ValueError, RuntimeError, subprocess.CalledProcessError, subprocess.TimeoutExpired) as exc:
        message(str(exc), error=True)
        return 1
    return 0


if __name__ == '__main__':
    sys.exit(main())
