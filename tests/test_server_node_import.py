import copy
import threading
import unittest
from unittest.mock import patch
from urllib.request import Request, urlopen
from urllib.error import HTTPError

import server
import test_subscription_enforcement as fixture


class ServerNodeImportTests(unittest.TestCase):
    setUp = fixture.EnforcementTests.setUp

    def row(self, count=1):
        nodes=[{'name':f'remote-{i}', 'type':'ss','server':'198.51.100.1','port':24443,
                'cipher':'aes-128-gcm','password':f'password-{i}'} for i in range(count)]
        row={'id':50,'port':24443,'remark':'dedicated','up':100,'down':200,
             'shareLinks':[server.node_to_uri(n) for n in nodes]}
        self.manager.inbounds.return_value={'inbounds':[row]}
        return row

    def import_row(self, **changes):
        return server.import_server_inbound({'serverId':'s','inboundId':50,**changes})

    def test_live_import_is_idempotent_and_only_copy_does_not_bind(self):
        row=self.row()
        result=self.import_row(links='ss://untrusted-client-credentials')
        self.assertEqual(result['added'],1);self.assertFalse(result['bound'])
        self.assertFalse(server.profile_record().get('remoteNodes'))
        self.manager.ensure_meter.assert_not_called()
        result=self.import_row()
        self.assertEqual(result['added'],0);self.assertEqual(result['existing'],1)
        self.assertEqual(server.read_config()['proxies'][0]['password'],'password-0')
        self.assertEqual(len(server.read_config()['proxies']),1)

    def test_import_and_bind_uses_baseline_and_existing_enforcement(self):
        self.row()
        result=self.import_row(bind=True,confirmedDedicated=True)
        self.assertTrue(result['bound']);self.assertFalse(result['enforcementEnabled'])
        binding=server.profile_record()['remoteNodes'][0]
        self.assertEqual((binding['up'],binding['down']),(100,200))
        record=server.profile_record();record['traffic']={'enabled':True,'enforce':True,'upload':0,'download':0,'total':500,'expire':None}
        self.manager.inbounds.return_value['inbounds'][0].update(up=200,down=600)
        server.enforce_profile('clash')
        self.assertEqual(server.traffic_info(record)['used'],500)
        self.manager.delete_inbound.assert_called_once_with('s',50)
        self.assertEqual(binding['status'],'revoked')

    def test_shared_inbound_can_copy_selected_client_but_cannot_bind(self):
        self.row(2)
        result=self.import_row(indices=[1])
        self.assertEqual(result['added'],1)
        self.assertEqual(server.read_config()['proxies'][0]['password'],'password-1')
        with self.assertRaisesRegex(ValueError,'多个用户'):
            self.import_row(bind=True,confirmedDedicated=True)
        with self.assertRaisesRegex(ValueError,'多个用户'):
            server.bind_remote_node('s',50)
        self.manager.ensure_meter.assert_not_called()
        self.manager.delete_inbound.assert_not_called()

    def test_invalid_selection_or_missing_consent_makes_no_local_changes(self):
        self.row()
        for changes in ({'indices':[]},{'indices':[9]},{'indices':[False]},{'indices':[0,0]},
                        {'bind':True},{'bind':'true'}):
            with self.subTest(changes=changes),self.assertRaises(ValueError):self.import_row(**changes)
        self.assertEqual(server.read_config()['proxies'],[])
        self.assertFalse(server.profile_record().get('remoteNodes'))
        self.manager.ensure_meter.assert_not_called()

    def test_other_subscription_ownership_blocks_even_copy(self):
        self.row();self.import_row(bind=True,confirmedDedicated=True)
        other=server.create_profile({'name':'other'})
        server.CTX.profile=other['id']
        with self.assertRaisesRegex(ValueError,'其他订阅'):self.import_row()
        self.assertEqual(server.read_config()['proxies'],[])

    def test_enforced_subscription_cannot_import_uncontrolled_node(self):
        self.row();record=server.profile_record()
        record['traffic']={'enabled':True,'enforce':True,'upload':0,'download':0,'total':500,'expire':None}
        with self.assertRaisesRegex(ValueError,'必须绑定'):self.import_row()
        self.assertEqual(server.read_config()['proxies'],[])
        result=self.import_row(bind=True,confirmedDedicated=True)
        self.assertTrue(result['enforcementEnabled'])

    def test_failure_keeps_existing_remote_node_and_reports_retained_binding(self):
        self.row()
        with patch('server.write_config',side_effect=OSError('disk full')):
            with self.assertRaisesRegex(ValueError,'专属绑定仍保留'):
                self.import_row(bind=True,confirmedDedicated=True)
        self.manager.delete_inbound.assert_not_called()
        self.assertEqual(server.profile_record()['remoteNodes'][0]['status'],'active')
        self.assertEqual(self.import_row()['added'],1)

    def test_expiry_revokes_imported_dedicated_inbound(self):
        self.row();self.import_row(bind=True,confirmedDedicated=True)
        record=server.profile_record()
        record['traffic']={'enabled':True,'enforce':True,'upload':0,'download':0,'total':500,'expire':1}
        server.enforce_profile('clash')
        self.manager.delete_inbound.assert_called_once_with('s',50)
        self.assertTrue(server.subscription_blocked(record))

    def test_historic_revoked_id_does_not_hide_new_owned_binding(self):
        self.row();self.import_row(bind=True,confirmedDedicated=True)
        old=server.create_profile({'name':'history'})
        server.profile_record(old['id'])['remoteNodes']=[{'serverId':'s','inboundId':50,'status':'revoked'}]
        # History may precede the current owner in the persisted profile order.
        server.load_profiles()['profiles'].reverse()
        self.assertEqual(self.import_row()['existing'],1)
        self.assertEqual(server.bind_remote_node('s',50)['status'],'active')

    def test_endpoint_auth_and_profile_selection(self):
        self.row()
        httpd=server.ThreadingHTTPServer(('127.0.0.1',0),server.Handler)
        thread=threading.Thread(target=httpd.serve_forever,daemon=True);thread.start()
        try:
            request=Request(f'http://127.0.0.1:{httpd.server_address[1]}/admin/api/servers/import',
                data=b'{"serverId":"s","inboundId":50}',method='POST',
                headers={'Content-Type':'application/json','Origin':f'https://127.0.0.1:{httpd.server_address[1]}','X-Forwarded-Proto':'https'})
            with self.assertRaises(HTTPError) as error:urlopen(request,timeout=3)
            self.assertEqual(error.exception.code,401)
            other=server.create_profile({'name':'target'})
            request.add_header('X-Profile',other['id'])
            with patch.object(server.Handler,'authorized',return_value=True):
                with urlopen(request,timeout=3) as response:self.assertEqual(response.status,200)
            self.assertFalse(server.read_config()['proxies'])
            server.CTX.profile=other['id']
            self.assertEqual(len(server.read_config()['proxies']),1)
        finally:
            httpd.shutdown();httpd.server_close();thread.join(3)
