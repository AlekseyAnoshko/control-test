#!/usr/bin/env python3
"""Install a user service and desktop launchers; run without sudo."""
import os
from pathlib import Path
import shutil
import subprocess
import sys

UNIT = 'control-test-web.service'


def unit_quote(value):
    return '"' + str(value).replace('\\', '\\\\').replace('"', '\\"').replace('%', '%%') + '"'


def desktop_quote(value):
    text = str(value).replace('\\', '\\\\\\\\').replace('"', '\\\\"').replace('`', '\\\\`').replace('$', '\\\\$').replace('%', '%%')
    return '"' + text + '"'


def service_content(project):
    return f'''[Unit]
Description=Control Test local web server
StartLimitIntervalSec=60
StartLimitBurst=10

[Service]
Type=simple
WorkingDirectory={str(project).replace('%', '%%')}
Environment=PYTHONUNBUFFERED=1
ExecStart={unit_quote(project / '.venv/bin/python')} -m uvicorn app.main:app --host 10.10.10.1 --port 8000 --workers 1
Restart=on-failure
RestartSec=3
TimeoutStopSec=15
UMask=0077
'''


def launcher_content(action, helper):
    title, icon, comment = (
        ('Запустить тестирование', 'media-playback-start', 'Запустить сервер и открыть панель преподавателя')
        if action == 'start' else
        ('Остановить тестирование', 'media-playback-stop', 'Остановить сервер; предварительно выгрузите результаты')
    )
    return f'''[Desktop Entry]
Version=1.0
Type=Application
Name={title}
Comment={comment}
Exec=/usr/bin/python3 {desktop_quote(helper)} {action}
Icon={icon}
Terminal=false
Categories=Education;
StartupNotify=false
'''


def install(project, home, desktop):
    unit = home / '.config/systemd/user' / UNIT
    helper = home / '.local/lib/control-test/desktop_control.py'
    unit.parent.mkdir(parents=True, exist_ok=True)
    helper.parent.mkdir(parents=True, exist_ok=True)
    desktop.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(project / 'deploy/desktop_control.py', helper)
    helper.chmod(0o755)
    unit.write_text(service_content(project))
    launchers = []
    for action in ('start', 'stop'):
        launcher = desktop / f'control-test-{action}.desktop'
        launcher.write_text(launcher_content(action, helper))
        launcher.chmod(0o755)
        launchers.append(launcher)
    return unit, launchers


def main():
    if os.geteuid() == 0:
        sys.exit('Запустите без sudo, от пользователя рабочего стола.')
    project = Path(__file__).resolve().parents[1]
    if any(c in str(project) for c in '\n\r'):
        sys.exit('Путь проекта содержит перевод строки.')
    if not (project / '.venv/bin/python').is_file():
        sys.exit('Не найден .venv/bin/python. Сначала создайте окружение и установите requirements.txt.')
    check = subprocess.run(['systemctl', '--user', 'show-environment'], capture_output=True, text=True)
    if check.returncode:
        sys.exit('Недоступен пользовательский systemd:\n' + check.stderr)
    desktop_result = subprocess.run(['xdg-user-dir', 'DESKTOP'], capture_output=True, text=True) if shutil.which('xdg-user-dir') else None
    home = Path.home()
    desktop = Path(desktop_result.stdout.strip()) if desktop_result and desktop_result.returncode == 0 and desktop_result.stdout.strip() else home / 'Desktop'
    unit, launchers = install(project, home, desktop)
    subprocess.run(['systemctl', '--user', 'daemon-reload'], check=True)
    # Desktop trust is optional and depends on the desktop environment.
    if shutil.which('gio'):
        for launcher in launchers:
            subprocess.run(['gio', 'set', str(launcher), 'metadata::trusted', 'true'], capture_output=True)
    print('Сервис установлен:', unit)
    for launcher in launchers:
        print('Кнопка:', launcher)
    print('Запуск: systemctl --user start ' + UNIT)
    print('Журнал: journalctl --user -u ' + UNIT + ' -n 40 --no-pager')
    print('Сервис запускается кнопкой, автоматический запуск при входе не включён.')
    print('При первом открытии ярлыка рабочий стол может предложить разрешить запуск.')


if __name__ == '__main__':
    main()
