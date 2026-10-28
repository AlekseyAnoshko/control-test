#!/usr/bin/env python3
"""Desktop controls for the current user's Control Test web service."""
import json
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


def control(action):
    if action == 'stop':
        subprocess.run(['systemctl', '--user', 'stop', UNIT], check=True)
        message('Тестирование остановлено. Сервер больше не принимает подключения.')
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
