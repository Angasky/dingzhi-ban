"""Opt-in checks against actual release binaries; no server installation or listeners.

MXIOC_XRAY_BIN=/path/xray MXIOC_SING_BOX_BIN=/path/sing-box python -m unittest
  discover -s tests -p test_native_core_binaries.py -v
"""
import base64
import copy
import json
import os
import re
import subprocess
import tempfile
import threading
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import Mock

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID

import core_adapter
import remote_manager as remote


class NativeBinaryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if not os.environ.get('MXIOC_XRAY_BIN') or not os.environ.get('MXIOC_SING_BOX_BIN'):
            raise unittest.SkipTest('Set MXIOC_XRAY_BIN and MXIOC_SING_BOX_BIN for real binary validation')

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, 'example.com')])
        now = datetime.now(timezone.utc)
        cert = (x509.CertificateBuilder().subject_name(name).issuer_name(name).public_key(key.public_key())
                .serial_number(x509.random_serial_number()).not_valid_before(now - timedelta(days=1))
                .not_valid_after(now + timedelta(days=1))
                .add_extension(x509.SubjectAlternativeName([x509.DNSName('example.com')]), critical=False)
                .sign(key, hashes.SHA256()))
        (self.root / 'cert.pem').write_bytes(cert.public_bytes(serialization.Encoding.PEM))
        (self.root / 'key.pem').write_bytes(key.private_bytes(serialization.Encoding.PEM,
                            serialization.PrivateFormat.PKCS8, serialization.NoEncryption()))

    def compile_and_check(self, kind, row, outbound=None):
        adapter = core_adapter.CoreAPI.__new__(core_adapter.CoreAPI)
        adapter.kind, adapter.path, adapter.binary = kind, '/tmp/config.json', '/tmp/core'
        adapter.record, adapter.ssh = {'id': 'test'}, Mock()
        adapter.state = {'inbounds': [], 'outbounds': [], 'routing': {'rules': []}}
        adapter.native = {'inbounds': [], 'outbounds': []}
        manager = Mock()
        manager.lock, manager.record.return_value = threading.RLock(), {}
        adapter.manager = manager
        captured = []
        def execute(ssh, record, script, **kwargs):
            data = re.search(r"printf '%s' '([A-Za-z0-9+/=]+)'", script)[1]
            captured.append(json.loads(base64.b64decode(data)))
            return 0, ''
        manager.execute.side_effect = execute
        row = {**row, 'id': 1, 'tag': 'mxioc-inbound-1'}
        state = {'inbounds': [row], 'outbounds': [outbound] if outbound else [], 'routing': {'rules': []}}
        if outbound:
            state['routing']['rules'] = [{'type': 'field', 'inboundTag': [row['tag']], 'outboundTag': outbound['tag']}]
        adapter.apply(state)
        config = self.root / 'config.json'
        config.write_text(json.dumps(captured[-1]))
        command = ([os.environ['MXIOC_XRAY_BIN'], 'run', '-test', '-config', str(config)] if kind == 'xray' else
                   [os.environ['MXIOC_SING_BOX_BIN'], 'check', '-c', str(config)])
        result = subprocess.run(command, capture_output=True, text=True, timeout=15)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_all_supported_templates_compile_in_release_binaries(self):
        for kind in ('xray', 'sing-box'):
            api = Mock()
            api.kind = kind
            api.request.return_value = core_adapter.key_pair()
            templates = ['vless-reality', 'vmess-tcp', 'shadowsocks', 'trojan-tls', 'vless-ws-tls']
            if kind == 'sing-box':
                templates += ['hysteria2', 'tuic']
            for template in templates:
                with self.subTest(kind=kind, template=template):
                    row = remote.build_inbound(api, {'port': 24443, 'template': template,
                        'serverName': 'example.com', 'certFile': str(self.root / 'cert.pem'),
                        'keyFile': str(self.root / 'key.pem')})
                    self.compile_and_check(kind, row)

    def test_supported_relay_outbounds_compile_in_release_binaries(self):
        for kind in ('xray', 'sing-box'):
            api = Mock()
            api.request.return_value = core_adapter.key_pair()
            row = remote.build_inbound(api, {'port': 24443, 'template': 'vless-reality', 'serverName': 'example.com'})
            base = {'server': '198.51.100.40', 'port': 443, 'servername': 'example.com',
                    'sni': 'example.com', 'uuid': '22453845-ff1a-471d-8214-25952a9348a6',
                    'password': 'test-password', 'cipher': 'aes-128-gcm'}
            for protocol in ['vless', 'vmess', 'trojan', 'ss', 'socks5', 'http'] + (['hysteria2', 'tuic'] if kind == 'sing-box' else []):
                with self.subTest(kind=kind, protocol=protocol):
                    node = {**base, 'type': protocol, 'tls': protocol in ('vless', 'trojan'),
                            'cipher': 'auto' if protocol == 'vmess' else base['cipher']}
                    if protocol in ('hysteria2', 'tuic'):
                        outbound = {'tag': 'landing', 'protocol': 'mxioc-native',
                                    'settings': {'native': core_adapter.sing_quic_outbound(node, 'landing')}}
                    else:
                        outbound = remote.node_outbound(node, 'landing')
                    self.compile_and_check(kind, row, outbound)


if __name__ == '__main__':
    unittest.main()
