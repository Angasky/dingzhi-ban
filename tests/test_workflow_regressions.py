"""Regressions reproduced during the October full-workflow review."""
import copy
import io
import unittest
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import Mock, patch

import server
import bandwidth
import test_subscription_enforcement as subscription_fixture
import test_remote_manager as remote_fixture


class QuotaWorkflowRegressions(unittest.TestCase):
    setUp = subscription_fixture.EnforcementTests.setUp
    configure = subscription_fixture.EnforcementTests.configure

    def test_invalid_quota_is_rejected_before_remote_changes_or_settlement(self):
        record = self.configure()
        self.manager.inbounds.return_value['inbounds'][0]['up'] = 10
        with self.assertRaises(ValueError):
            server.update_traffic({'enforce': True, 'total': -1, 'multiplier': 2, 'preserveUsage': True})
        self.manager.ensure_meter.assert_not_called()
        self.manager.inbounds.assert_not_called()
        self.assertEqual(record['traffic']['upload'], 0)

    def test_disconnect_failure_keeps_retrying_after_inbound_is_gone(self):
        record = self.configure(expire=1)
        self.manager.delete_inbound.side_effect = lambda *args: self.manager.inbounds.return_value.update(inbounds=[])
        self.manager.disconnect_port.side_effect = [ValueError('ss offline'), ValueError('ss still offline'), None]
        server.enforce_profile('clash')
        server.enforce_profile('clash')
        self.assertEqual(record['remoteNodes'][0]['status'], 'active', 'failed socket cleanup must stay retryable')
        server.enforce_profile('clash')
        self.assertEqual(record['remoteNodes'][0]['status'], 'revoked')
        self.assertEqual(self.manager.disconnect_port.call_count, 3)

    def test_concurrent_sync_does_not_double_charge(self):
        record = self.configure(total=10000)
        record['traffic']['multiplierUnits'] = 2500
        self.manager.inbounds.return_value['inbounds'][0].update(up=100, down=200)
        with ThreadPoolExecutor(max_workers=8) as pool:
            list(pool.map(lambda _: server.enforce_profile('clash'), range(24)))
        self.assertEqual(server.traffic_info(record)['used'], 750)
        self.assertEqual(server.traffic_info(record)['rawUsed'], 300)

    def test_history_does_not_receive_live_counters_or_revocation(self):
        record = self.configure(total=1000)
        history = {**copy.deepcopy(record['remoteNodes'][0]), 'status': 'revoked', 'up': 777, 'down': 888}
        record['remoteNodes'].insert(0, history)
        self.manager.inbounds.return_value['inbounds'][0].update(up=10, down=20)
        server.enforce_profile('clash')
        self.assertEqual(server.traffic_info(record)['used'], 30)
        self.assertEqual((history['up'], history['down']), (777, 888))
        self.assertEqual(record['remoteNodes'][1]['up'], 10)
        record['traffic']['total'] = 30
        server.enforce_profile('clash')
        self.assertEqual(record['remoteNodes'][1]['status'], 'revoked')
        self.assertNotIn('revokedAt', history)


class BandwidthWorkflowRegressions(unittest.TestCase):
    setUp = remote_fixture.RemoteManagerTests.setUp
    tearDown = remote_fixture.RemoteManagerTests.tearDown
    with_fake_api = remote_fixture.RemoteManagerTests.with_fake_api

    def test_limited_node_cannot_switch_tcp_to_udp_transport(self):
        api = remote_fixture.FakeAPI()
        row = {'id': 1, 'port': 24443, 'protocol': 'vless', 'listen': '',
               'settings': {'clients': [{'id': '22453845-ff1a-471d-8214-25952a9348a6'}], 'decryption': 'none'},
               'streamSettings': {'network': 'tcp', 'security': 'none'}, 'enable': True, 'remark': 'test'}
        api.rows = [row]
        self.manager.record(self.identity)['bandwidths'] = {'1': bandwidth.policy(row, 1000, 2000)}
        body = {**row, 'streamSettings': {'network': 'kcp', 'security': 'none'}}
        with self.with_fake_api(api), self.assertRaisesRegex(ValueError, '限速'):
            self.manager.update_inbound(self.identity, 1, body)

    def test_panel_meter_does_not_open_an_unused_ssh_connection(self):
        with patch.object(self.manager, 'connect') as connect, patch.object(self.manager, 'panel'):
            self.manager.ensure_meter(self.identity, 1)
        connect.assert_not_called()

    def test_repeated_delete_of_missing_node_does_not_call_panel_delete(self):
        api = remote_fixture.FakeAPI()
        request = api.request
        def fail_missing(path, *args, **kwargs):
            if '/inbounds/del/' in path:
                raise ValueError('inbound not found')
            return request(path, *args, **kwargs)
        api.request = fail_missing
        with self.with_fake_api(api):
            self.assertTrue(self.manager.delete_inbound(self.identity, 1)['deleted'])


class RequestBodyRegressions(unittest.TestCase):
    def handler(self, raw, length=None):
        handler = object.__new__(server.Handler)
        handler.headers = {'Content-Length': str(len(raw) if length is None else length), 'Content-Type': 'application/json'}
        handler.rfile = io.BytesIO(raw)
        return handler

    def test_negative_content_length_is_rejected_before_read(self):
        handler = self.handler(b'{}', -1)
        handler.rfile = Mock()
        with self.assertRaises(ValueError):
            handler.body()
        handler.rfile.read.assert_not_called()

    def test_json_body_must_be_object(self):
        for raw in (b'[]', b'null', b'123', b'"hello"'):
            with self.subTest(raw=raw), self.assertRaisesRegex(ValueError, 'JSON'):
                self.handler(raw).body()
