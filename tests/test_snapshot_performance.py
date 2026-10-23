import gzip
import json
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch
from urllib.request import Request, urlopen
import server


class SnapshotCacheTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        root=Path(self.temp.name)
        for name,value in dict(ROOT=root,PROFILES_FILE=root/'profiles.json',PROFILES_DIR=root/'profiles',
             FILES=[root/'config.yml'],PROFILES_CACHE=None,PROFILES_MTIME=0,CONFIG_CACHE={},BACKUP_DIR=root/'backups').items():
            original=getattr(server,name)
            setattr(server,name,value)
            self.addCleanup(setattr,server,name,original)
        previous=getattr(server.CTX,'profile','clash')
        server.CTX.profile='clash'
        self.addCleanup(setattr,server.CTX,'profile',previous)
        server.FILES[0].write_text(server.yaml_dump(server.minimal_profile_config()))
        server.load_profiles()

    def test_wire_and_gzip_match_snapshot_and_cache_heavy_view(self):
        expected=server.snapshot()
        view=server.snapshot_view()
        self.assertEqual(json.loads(server.snapshot_json()),expected)
        first=server.snapshot_json(True)
        self.assertEqual(json.loads(gzip.decompress(first)),expected)
        self.assertIs(server.snapshot_json(True),first)
        self.assertIs(server.snapshot_view(),view)

    def test_config_mutation_and_profile_changes_invalidate_without_leaking(self):
        old=server.snapshot_json()
        other=server.create_profile({'name':'second'})
        doc=server.read_config()
        doc['rules'].insert(0,'DOMAIN,changed.example.com,DIRECT')
        server.write_config(doc)
        self.assertNotEqual(server.snapshot_json(),old)
        server.CTX.profile=other['id']
        self.assertNotIn('changed.example.com',server.snapshot_json().decode())
        self.assertEqual(json.loads(server.snapshot_json())['profile']['id'],other['id'])

    def test_usage_and_expiry_remain_dynamic_when_config_is_cached(self):
        view=server.snapshot_view()
        record=server.profile_record()
        record['traffic']={'enabled':True,'upload':1,'download':2,'total':100,'expire':200}
        with patch('server.time.time',return_value=150):
            first=json.loads(gzip.decompress(server.snapshot_json(True)))
        record['traffic']['download']=5
        with patch('server.time.time',return_value=250):
            second=json.loads(gzip.decompress(server.snapshot_json(True)))
        self.assertFalse(first['profile']['traffic']['expired'])
        self.assertTrue(second['profile']['traffic']['expired'])
        self.assertEqual(second['profile']['traffic']['used'],6)
        self.assertIs(server.snapshot_view(),view)

    def test_config_cache_is_bounded_across_many_profiles(self):
        for index in range(12):
            server.create_profile({'name':'profile-'+str(index)})
        self.assertLessEqual(len(server.CONFIG_CACHE),8)
        self.assertEqual(json.loads(server.snapshot_json())['profile']['id'],'clash')

    def test_http_negotiates_gzip_and_respects_explicit_zero_quality(self):
        with patch.object(server.Handler,'authorized',return_value=True):
            httpd=server.ThreadingHTTPServer(('127.0.0.1',0),server.Handler)
            thread=threading.Thread(target=httpd.serve_forever,daemon=True);thread.start()
            try:
                for encoding,compressed in [('gzip',True),('gzip;q=0',False),('gzip;q=0.0',False),('gzip;q=0.5',True)]:
                    request=Request('http://127.0.0.1:'+str(httpd.server_address[1])+'/admin/api/snapshot',headers={'Accept-Encoding':encoding})
                    with self.subTest(encoding=encoding),urlopen(request,timeout=3) as response:
                        raw=response.read()
                        self.assertEqual(response.headers.get('Content-Encoding')=='gzip',compressed)
                        body=json.loads(gzip.decompress(raw) if compressed else raw)
                        self.assertEqual(body['profile']['id'],'clash')
            finally:
                httpd.shutdown();httpd.server_close();thread.join(3)


class NodeValidationTests(unittest.TestCase):
    def node(self):
        return {'name':'Reality','type':'vless','server':'example.com','port':'443',
                'uuid':'22453845-ff1a-471d-8214-25952a9348a6','tls':True,
                'reality-opts':{'public-key':'AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA','short-id':'abcd12'}}

    def test_normalizes_port_and_preserves_advanced_options(self):
        node=self.node();node['smux']={'enabled':True}
        result=server.validate_node_config(node)
        self.assertEqual(result['port'],443)
        self.assertEqual(result['smux'],{'enabled':True})
        self.assertEqual(node['port'],'443')

    def test_rejects_invalid_port_uuid_reality_and_incompatible_vision(self):
        for change in ({'port':65536},{'port':'443;id'},{'uuid':'bad'},
                       {'server':'https://example.com'},{'reality-opts':{'public-key':'short'}},
                       {'reality-opts':{'public-key':'AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA','short-id':'abc'}},
                       {'flow':'xtls-rprx-vision','network':'ws'}):
            with self.subTest(change=change),self.assertRaises(ValueError):
                server.validate_node_config({**self.node(),**change})
    def test_shadowsocks_2022_requires_the_actual_psk_length(self):
        node={'name':'ss2022','type':'ss','server':'example.com','port':443,
              'cipher':'2022-blake3-aes-128-gcm','password':'short'}
        with self.assertRaisesRegex(ValueError,'16 字节'):
            server.validate_node_config(node)
        node['password']='AAAAAAAAAAAAAAAAAAAAAA=='
        self.assertEqual(server.validate_node_config(node)['password'],node['password'])


if __name__=='__main__':
    unittest.main()
