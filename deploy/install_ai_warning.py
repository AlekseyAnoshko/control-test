#!/usr/bin/env python3
"""Configure local DNS sink and HTTP warning. HTTPS stays blocked (no TLS interception)."""
from datetime import datetime
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile


def run(args, **kwargs):
    return subprocess.run(args, text=True, check=True, **kwargs)


def main():
    if os.geteuid() != 0:
        raise SystemExit('Запустите через sudo python3 deploy/install_ai_warning.py')
    dns = Path('/etc/control-test-dnsmasq.conf')
    gateway = Path('/etc/control-test/gateway.nft')
    interface = json.loads(Path('/etc/control-test/wifi.json').read_text())['interface']
    if not re.fullmatch(r'[a-zA-Z0-9_-]{1,15}', interface):
        raise SystemExit('Недопустимое имя интерфейса')
    # Retain domains added locally by the teacher as well as repository entries.
    original_dns, original_gateway = dns.read_text(), gateway.read_text()
    source = Path(__file__).resolve().parent / 'blocked-ai-domains.txt'
    domains = {s.strip().lower().rstrip('.') for s in source.read_text().splitlines()
               if s.strip() and not s.lstrip().startswith('#')}
    domains.update(re.findall(r'^local=/([^/]+)/\s*$', original_dns, re.M))
    if not all(re.fullmatch(r'[a-z0-9.-]+', domain) for domain in domains):
        raise SystemExit('Некорректный домен в списке')
    begin, end = '# BEGIN control-test AI warning', '# END control-test AI warning'
    clean = re.sub(re.escape(begin) + r'.*?' + re.escape(end) + r'\n?', '', original_dns, flags=re.S)
    rules = [begin]
    for domain in sorted(domains):
        # local= prevents AAAA and other record types from leaking upstream.
        rules.extend((f'local=/{domain}/', f'address=/{domain}/10.10.10.1'))
    rules.extend(('host-record=test.int,10.10.10.1', end))
    updated_dns = clean.rstrip() + '\n' + '\n'.join(rules) + '\n'
    rule = (f'        iifname "{interface}" ip daddr 10.10.10.1 '
            'tcp dport 80 redirect to :8000 comment "control-test-ai-warning"\n')
    updated_gateway = original_gateway
    if 'control-test-ai-warning' not in updated_gateway:
        anchor = 'type nat hook prerouting priority -100; policy accept;'
        if updated_gateway.count(anchor) != 1:
            raise SystemExit('Не найдена цепочка prerouting; файл не изменён')
        updated_gateway = updated_gateway.replace(anchor, anchor + '\n' + rule, 1)
    exists = subprocess.run(['/usr/sbin/nft', 'list', 'table', 'inet', 'control_test'],
                            capture_output=True, text=True).returncode == 0
    prefix = 'delete table inet control_test\n' if exists else ''
    # Validate both configurations before installing or restarting anything.
    with tempfile.TemporaryDirectory() as tmp:
        candidate = Path(tmp) / 'dnsmasq.conf'
        candidate.write_text(updated_dns)
        run(['/usr/sbin/dnsmasq', '--test', '--conf-file=' + str(candidate)])
        run(['/usr/sbin/nft', '-c', '-f', '-'], input=prefix + updated_gateway)
    stamp = datetime.now().strftime('%Y%m%d-%H%M%S')
    for path in (dns, gateway):
        shutil.copy2(path, str(path) + '.before-ai-warning-' + stamp)
    dns.write_text(updated_dns)
    gateway.write_text(updated_gateway)
    domain_file = Path('/etc/control-test/ai-warning-domains.txt')
    domain_file.write_text('\n'.join(sorted(domains)) + '\n')
    domain_file.chmod(0o644)
    run(['/usr/sbin/nft', '-f', '-'], input=prefix + updated_gateway)
    run(['systemctl', 'restart', 'control-test-dnsmasq.service'])
    print('Настроено доменов:', len(domains))
    print('HTTP показывает предупреждение. HTTPS не перенаправляется из-за проверки сертификатов.')
    print('Нужна новая версия веб-сервиса. Адрес предупреждения: http://test.int:8000/ai-blocked')


if __name__ == '__main__':
    main()
