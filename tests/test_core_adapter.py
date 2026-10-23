import base64
import copy
import contextlib
import io
import socket
import json
import re
import subprocess
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import Mock, MagicMock, patch

import core_adapter
import remote_manager as remote
import server


class CoreAdapterTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.manager = remote.RemoteManager(Path(self.temp.name), server.parse_node_link, server.node_to_uri)
        self.native = {}
        self.scripts = []
        self.fail_apply = False

    def initialize(self, kind):
        saved = self.manager.save({'name': kind, 'host': '198.51.100.30', 'sshUser': 'root',
            'sshPassword': 'ssh-secret', 'fingerprint': 'SHA256:' + 'a' * 43, 'installType': kind})
        self.identity = saved['id']
        self.native = {'inbounds': [], 'outbounds': [{'tag': 'existing-direct',
                       ('type' if kind == 'sing-box' else 'protocol'): ('direct' if kind == 'sing-box' else 'freedom')}]}
        self.native['route' if kind == 'sing-box' else 'routing'] = {'rules': []}
        self.manager.connect = Mock(return_value=Mock())
        def execute(ssh, record, script, **kwargs):
            if script.startswith('base64 < '):
                return 0, base64.b64encode(json.dumps(self.native).encode()).decode()
            self.scripts.append(script)
            subprocess.run(['bash', '-n'], input=script, text=True, capture_output=True, check=True)
            if self.fail_apply:
                return 22, 'invalid'
            match = re.search(r"printf '%s' '([A-Za-z0-9+/=]+)'", script)
            if match:
                self.native = json.loads(base64.b64decode(match[1]))
            return 0, ''
        self.manager.execute = Mock(side_effect=execute)
        return self.manager.record(self.identity)

    def test_templates_and_relay_preserve_unmanaged_configuration(self):
        for kind in ('xray', 'sing-box'):
            with self.subTest(kind=kind):
                record = self.initialize(kind)
                payload = {'port': 24443, 'remark': 'chain', 'serverName': 'www.microsoft.com',
                           'landingLink': 'socks5://user:pass@198.51.100.40:1080#landing'}
                original = copy.deepcopy(self.native['outbounds'][0])
                result = self.manager.create_relay(self.identity, payload)
                self.assertEqual(len(self.native['inbounds']), 1)
                self.assertEqual(self.native['outbounds'][0], original)
                self.assertEqual(len(self.native['outbounds']), 2)
                self.assertTrue(result['shareLinks'][0].startswith('vless://'))
                self.assertNotIn('privateKey', self.manager.path.read_text())
                rows = self.manager.inbounds(self.identity)['inbounds']
                self.assertEqual(rows[0]['id'], result['inbound']['id'])
                self.manager.delete_inbound(self.identity, result['inbound']['id'])
                self.assertEqual(self.native['inbounds'], [])
                self.assertEqual(self.native['outbounds'], [original])
                key = 'route' if kind == 'sing-box' else 'routing'
                self.assertEqual(self.native[key]['rules'], [])
                self.manager.remove(self.identity)

    def test_failed_apply_does_not_persist_state_or_report_success(self):
        self.initialize('sing-box')
        self.fail_apply = True
        with self.assertRaisesRegex(ValueError, '22'):
            self.manager.create_inbound(self.identity, {'port': 24443, 'template': 'shadowsocks'})
        self.assertNotIn('coreState', self.manager.record(self.identity))
        self.assertEqual(self.native['inbounds'], [])

    def test_disable_and_port_conflict(self):
        self.initialize('xray')
        result = self.manager.create_inbound(self.identity, {'port': 24443, 'template': 'shadowsocks'})
        with self.assertRaisesRegex(ValueError, '监听端口'):
            self.manager.create_inbound(self.identity, {'port': 24443, 'template': 'shadowsocks'})
        self.manager.update_inbound(self.identity, result['inbound']['id'], {**result['inbound'], 'enable': False})
        self.assertEqual(self.native['inbounds'], [])
        self.assertFalse(self.manager.inbounds(self.identity)['inbounds'][0]['enable'])

    def test_xpanel_chinese_settings_and_existing_mode(self):
        output = '\x1b[32mport（端口号）: 2053\x1b[0m\nwebBasePath（访问路径）: /panel-secret/\ncert: /etc/cert.pem\n'
        settings = remote.parse_panel_settings(output, {'host': '198.51.100.5', 'domain': 'panel.example.com'})
        self.assertEqual(settings['panelUrl'], 'https://panel.example.com:2053/panel-secret/')
        saved = self.manager.save({'name': 'X-Panel', 'host': '198.51.100.5', 'sshUser': 'root',
            'sshPassword': 'ssh', 'fingerprint': 'SHA256:' + 'a' * 43, 'installType': 'x-panel',
            'connectionMode': 'existing', 'panelUser': 'a', 'panelPassword': 'old'})
        with self.assertRaisesRegex(ValueError, '不会执行'):
            self.manager.start_install(saved['id'], True)

    def test_meter_failure_never_becomes_zero_usage(self):
        record = self.initialize('sing-box')
        record['meters'] = {'42': 24443}
        self.manager.execute = Mock(return_value=(1, ''))
        with self.assertRaisesRegex(ValueError, '不可核实'):
            self.manager.meter_snapshot(record, Mock())
        self.manager.execute.return_value = (0, '{"nftables": []}')
        with self.assertRaisesRegex(ValueError, '缺失'):
            self.manager.meter_snapshot(record, Mock())

    def test_meter_rule_setup_is_valid_shell_and_restored_on_boot(self):
        self.initialize('sing-box')
        result = self.manager.create_inbound(self.identity, {'port': 24443, 'template': 'shadowsocks'})
        self.manager.ensure_meter(self.identity, result['inbound']['id'])
        script = self.scripts[-1]
        self.assertIn('mxioc-meter-restore.service', script)
        self.assertIn('nft -s list table inet mxioc_usage', script)
        self.assertEqual(self.manager.record(self.identity)['meters'][str(result['inbound']['id'])], 24443)

    def test_sni_probe_runs_remote_tls_and_rejects_private_dns(self):
        self.initialize('xray')
        def execute(ssh, record, script, **kwargs):
            probe = script.split("MXIOC_SNI'\n", 1)[1].rsplit('\nMXIOC_SNI', 1)[0]
            output = io.StringIO()
            connection = MagicMock()
            tls = MagicMock()
            tls.__enter__.return_value.version.return_value = 'TLSv1.3'
            tls.__enter__.return_value.selected_alpn_protocol.return_value = 'h2'
            context = MagicMock()
            context.wrap_socket.return_value = tls
            def addresses(host, port, **kwargs):
                return [(socket.AF_INET, socket.SOCK_STREAM, 6, '',
                         ('127.0.0.1' if host == 'private.example.com' else '8.8.8.8', 443))]
            with contextlib.redirect_stdout(output), patch('socket.getaddrinfo', side_effect=addresses), \
                    patch('socket.socket', return_value=connection), patch('ssl.create_default_context', return_value=context):
                exec(compile(probe, '<remote-sni-probe>', 'exec'), {})
            return 0, output.getvalue()
        self.manager.execute = Mock(side_effect=execute)
        result = self.manager.test_sni(self.identity, ['public.example.com', 'private.example.com'])
        public, private = result['results']
        self.assertTrue(public['ok'])
        self.assertEqual(public['tls'], 'TLSv1.3')
        self.assertFalse(private['ok'])
        self.assertIn('非公网', private['error'])
        with self.assertRaises(ValueError):
            self.manager.test_sni(self.identity, ['example.com; id'])


if __name__ == '__main__':
    unittest.main()
