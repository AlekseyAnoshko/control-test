#!/usr/bin/env python3
"""Render gateway configuration without changing the host network (Python 3.10+)."""
import argparse
import ipaddress
import json
from pathlib import Path
import re


def render(config, domains):
    ap, wan = config['ap_interface'], config['wan_interface']
    for interface in (ap, wan):
        if not re.fullmatch(r'[a-zA-Z0-9_-]{1,15}', interface):
            raise ValueError('Invalid interface name')
    if ap == wan:
        raise ValueError('AP and WAN must use different interfaces')
    address = ipaddress.IPv4Interface(config['address'])
    network, gateway = address.network, address.ip
    start, end = (ipaddress.IPv4Address(config[k]) for k in ('dhcp_start', 'dhcp_end'))
    if not (network.network_address < start <= end < network.broadcast_address):
        raise ValueError('DHCP range must be inside the AP subnet')
    if start <= gateway <= end or gateway in (network.network_address, network.broadcast_address):
        raise ValueError('Gateway must be a host outside the DHCP range')
    mode = config['mode']
    if mode != 'block-ai':
        raise ValueError('Only block-ai mode is supported')
    port = config['web_port']
    if type(port) is not int or not 1 <= port <= 65535 or port in (53, 67):
        raise ValueError('Invalid web port')
    servers = [str(ipaddress.IPv4Address(s)) for s in config['dns_servers']]
    if not servers or any(ipaddress.IPv4Address(s) in network for s in servers):
        raise ValueError('Use upstream IPv4 DNS servers outside the AP subnet')
    blocked = []
    for line in domains.splitlines():
        domain = line.split('#', 1)[0].strip().lower()
        if not domain:
            continue
        if len(domain) > 253 or '.' not in domain or any(
            not re.fullmatch(r'[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?', label)
            for label in domain.split('.')
        ):
            raise ValueError(f'Invalid blocked domain: {domain}')
        blocked.append(domain)
    if mode == 'block-ai' and not blocked:
        raise ValueError('AI blocklist must not be empty')
    dns = f'''# Generated; edit gateway.json and regenerate instead.
interface={ap}
except-interface=lo
bind-dynamic
dhcp-range={start},{end},{network.netmask},1h
dhcp-option=option:router,{gateway}
dhcp-option=option:dns-server,{gateway}
dhcp-authoritative
dhcp-leasefile=/var/lib/misc/control-test-dnsmasq.leases
domain-needed
bogus-priv
no-resolv
no-hosts
host-record=control-test.test,{gateway}
'''
    dns += ''.join(f'server={s}\n' for s in servers)
    # Empty server address returns NXDOMAIN for every RR type, including HTTPS.
    dns += ''.join(f'local=/{d}/\n' for d in sorted(set(blocked)))
    allow = ''
    if mode == 'block-ai':
        allow = f'''        iifname "{ap}" oifname "{wan}" ip saddr {network} ip daddr {{ 0.0.0.0/8, 10.0.0.0/8, 100.64.0.0/10, 127.0.0.0/8, 169.254.0.0/16, 172.16.0.0/12, 192.168.0.0/16, 224.0.0.0/4, 240.0.0.0/4 }} counter drop
        iifname "{ap}" tcp dport 853 counter drop
        iifname "{ap}" udp dport 853 counter drop
        iifname "{ap}" oifname "{wan}" ip saddr {network} counter accept
        iifname "{wan}" oifname "{ap}" ip daddr {network} ct state established,related counter accept
'''
    nft = f'''# Generated; only owns table inet control_test.
table inet control_test {{
    chain input {{
        type filter hook input priority -10; policy accept;
        iifname "{ap}" meta nfproto ipv6 counter drop
        iifname "{ap}" udp sport 68 udp dport 67 counter accept
        iifname "{ap}" ip saddr != {network} counter drop
        iifname "{ap}" ip daddr {gateway} udp dport 53 counter accept
        iifname "{ap}" ip daddr {gateway} tcp dport {{ 53, {port} }} counter accept
        iifname "{ap}" ip daddr {gateway} icmp type echo-request counter accept
        iifname "{ap}" counter drop
    }}
    chain forward {{
        type filter hook forward priority -10; policy accept;
{allow}        iifname "{ap}" counter drop
        oifname "{ap}" counter drop
    }}
    chain prerouting {{
        type nat hook prerouting priority -100; policy accept;
        iifname "{ap}" meta nfproto ipv4 udp dport 53 redirect to :53
        iifname "{ap}" meta nfproto ipv4 tcp dport 53 redirect to :53
    }}
    chain postrouting {{
        type nat hook postrouting priority 100; policy accept;
        iifname "{ap}" oifname "{wan}" ip saddr {network} masquerade
    }}
}}
'''
    return {'gateway.nft': nft, 'dnsmasq.conf': dns}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', type=Path, required=True)
    parser.add_argument('--domains', type=Path, default=Path(__file__).with_name('blocked-ai-domains.txt'))
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    try:
        files = render(json.loads(args.config.read_text()), args.domains.read_text())
    except (ValueError, KeyError, TypeError, OSError) as exc:
        parser.error(str(exc))
    args.output.mkdir(parents=True, exist_ok=True)
    for name, content in files.items():
        (args.output / name).write_text(content)


if __name__ == '__main__':
    main()
