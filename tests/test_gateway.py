import copy
import importlib.util
import json
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('gateway', ROOT / 'deploy/gateway.py')
gateway = importlib.util.module_from_spec(spec)
spec.loader.exec_module(gateway)


class GatewayTests(unittest.TestCase):
    def setUp(self):
        self.config = json.loads((ROOT / 'deploy/gateway.json.example').read_text())

    def test_defaults_preserve_web_and_filter_dns(self):
        result = gateway.render(self.config, (ROOT / 'deploy/blocked-ai-domains.txt').read_text())
        dns, nft = result['dnsmasq.conf'], result['gateway.nft']
        self.assertIn('local=/chatgpt.com/', dns)
        self.assertNotIn('local=/#/', dns)
        self.assertIn('server=1.1.1.1', dns)
        self.assertIn('host-record=control-test.test,10.10.10.1', dns)
        self.assertIn('oifname "wlp3s0" ip saddr 10.10.10.0/24 counter accept', nft)
        self.assertIn('tcp dport 853 counter drop', nft)
        self.assertIn('udp dport 53 redirect to :53', nft)
        self.assertIn('tcp dport 53 redirect to :53', nft)
        self.assertIn('meta nfproto ipv6 counter drop', nft)
        self.assertNotIn('flush ruleset', nft)

    def test_rejects_dangerous_or_inconsistent_settings(self):
        for key, value in [
            ('wan_interface', self.config['ap_interface']),
            ('ap_interface', 'ap";flush ruleset'),
            ('dhcp_start', '192.168.1.1'),
            ('dhcp_start', '10.10.10.1'),
            ('dhcp_end', '10.10.10.255'),
            ('web_port', 53), ('web_port', True),
            ('mode', 'local-only'),
            ('dns_servers', ['10.10.10.1']),
        ]:
            with self.subTest(key=key, value=value):
                config = copy.deepcopy(self.config)
                config[key] = value
                with self.assertRaises(ValueError):
                    gateway.render(config, 'chatgpt.com')

    def test_domain_validation_and_normalization(self):
        for domain in ['https://chatgpt.com', 'ya.ru/ai', '*.openai.com', 'a..com', 'bad;domain.com', '']:
            with self.subTest(domain=domain), self.assertRaises(ValueError):
                gateway.render(self.config, domain)
        dns = gateway.render(self.config, '# comment\nChatGPT.com\nchatgpt.com')['dnsmasq.conf']
        self.assertEqual(dns.count('local=/chatgpt.com/'), 1)


if __name__ == '__main__':
    unittest.main()
