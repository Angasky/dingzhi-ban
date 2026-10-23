import copy
import threading
import unittest
from unittest.mock import patch

import server
import test_subscription_enforcement as enforcement_fixture


class FailoverTests(unittest.TestCase):
    setUp = enforcement_fixture.EnforcementTests.setUp

    def provision(self):
        self.nodes = [dict(name=n, type='ss', server=f'198.51.100.{i+1}', port=443,
                           cipher='aes-128-gcm', password=n) for i,n in enumerate(['primary','backup','exit'])]
        doc=server.read_config(); doc['proxies']=self.nodes
        server.write_config(doc)
        self.manager.list.return_value=[{'id':n['name'],'host':n['server']} for n in self.nodes]
        self.manager.inbounds.side_effect=lambda identity: {'inbounds':[{'id':1,'port':443,
            'shareLinks':[server.node_to_uri(next(n for n in self.nodes if n['name']==identity))]}]}
        self.manager.create_relay.side_effect=lambda identity,payload: {
            'inbound':{'id':50,'port':24443,'remark':payload.get('remark')},
            'shareLinks':[server.node_to_uri(dict(self.nodes[0 if identity=='primary' else 1],
                port=24443,password='dedicated'))]}
        self.payload={'name':'HA','entries':['primary','backup'],'exit':'exit','port':24443,'interval':10}

    def test_fixed_exit_order_subscription_ownership_and_protection(self):
        self.provision()
        result=server.create_failover(self.payload)
        self.assertEqual(result['status'],'active')
        calls=self.manager.create_relay.call_args_list
        self.assertEqual([c.args[0] for c in calls],['primary','backup'])
        self.assertEqual({c.args[1]['landingLink'] for c in calls},{server.node_to_uri(self.nodes[2])})
        doc=server.read_config()
        group=next(g for g in doc['proxy-groups'] if g['name']=='HA')
        self.assertEqual(group['type'],'fallback'); self.assertEqual(group['proxies'],['HA / 1','HA / 2'])
        self.assertFalse(group['lazy']);self.assertNotIn('DIRECT',group['proxies'])
        self.assertEqual(len(server.profile_record()['remoteNodes']),2)
        for name in ['HA / 1','HA / 2']:
            with self.assertRaisesRegex(ValueError,'主备'):
                server.mutate_node('DELETE',{'name':name})
            with self.assertRaises(ValueError):
                server.bulk_delete_nodes({'names':[name]})
        with self.assertRaises(ValueError):server.mutate_group('DELETE',{'name':'HA'})
        server.mutate_node('POST',{'config':dict(self.nodes[0],name='unrelated')})
        group=next(g for g in server.read_config()['proxy-groups'] if g['name']=='HA')
        self.assertEqual(group['proxies'],['HA / 1','HA / 2'])
        server.remove_failover('HA')
        self.assertEqual(self.manager.delete_inbound.call_count,2)
        self.assertFalse(server.profile_record()['failovers'])
        self.assertFalse(any(n['name'].startswith('HA /') for n in server.read_config()['proxies']))
        self.assertTrue(all(b['status']=='revoked' for b in server.profile_record()['remoteNodes']))

    def test_failed_provisioning_rolls_back_owned_paths_only(self):
        self.provision()
        create=self.manager.create_relay.side_effect
        self.manager.create_relay.side_effect=lambda identity,payload: create(identity,payload) if identity=='primary' else (_ for _ in ()).throw(ValueError('offline'))
        with self.assertRaisesRegex(ValueError,'offline'):server.create_failover(self.payload)
        self.manager.delete_inbound.assert_called_once_with('primary',50)
        self.assertFalse(server.profile_record()['failovers'])
        self.assertEqual([n['name'] for n in server.read_config()['proxies']],['primary','backup','exit'])

    def test_failed_cleanup_is_visible_retryable_and_never_falls_back_to_direct(self):
        self.provision();server.create_failover(self.payload)
        self.manager.delete_inbound.side_effect=ValueError('offline')
        with self.assertRaises(ValueError):server.remove_failover('HA')
        self.assertEqual(server.profile_record()['failovers'][0]['status'],'cleanup-required')
        self.assertFalse(any(g['name']=='HA' for g in server.read_config()['proxy-groups']))
        self.assertNotIn('HA',str(server.read_config()['rules']))
        self.manager.delete_inbound.side_effect=None
        server.remove_failover('HA')
        self.assertFalse(server.profile_record()['failovers'])

    def test_invalid_entries_do_not_mutate_servers(self):
        self.provision()
        for changes in [{'entries':['primary','primary']},{'entries':['primary']},{'exit':'primary'},
                        {'interval':0},{'entries':['unknown','backup']}]:
            with self.subTest(changes=changes),self.assertRaises(ValueError):
                server.create_failover({**self.payload,**changes})
        self.manager.create_relay.assert_not_called()
        self.manager.list.return_value=[]
        with self.assertRaisesRegex(ValueError,'Mihomo'):server.create_failover(self.payload)
        self.manager.create_relay.assert_not_called()

    def test_node_edit_copy_on_write_noop_and_serialization(self):
        self.provision()
        original=server.read_config(copy_doc=False);frozen=copy.deepcopy(original)
        backup_count=len(list(server.BACKUP_DIR.glob('*.yaml')))
        self.assertTrue(server.mutate_node('PUT',{'oldName':'primary','config':self.nodes[0]})['unchanged'])
        self.assertEqual(len(list(server.BACKUP_DIR.glob('*.yaml'))),backup_count)
        for port in (444,445):
            server.mutate_node('PUT',{'oldName':'primary','config':dict(self.nodes[0],port=port)})
            self.assertEqual(server.yaml_load(server.FILES[0].read_text()),server.read_config())
        self.assertEqual(original,frozen)
        server.mutate_node('PUT',{'oldName':'primary','config':dict(self.nodes[0],name='renamed')})
        self.assertIn('renamed',[n['name'] for n in server.read_config()['proxies']])

    def test_global_sorting_and_flagging_preserve_managed_priority(self):
        self.provision();server.create_failover(self.payload)
        server.move_node({'name':'HA / 2','targetName':'HA / 1','position':'before'})
        group=next(g for g in server.read_config()['proxy-groups'] if g['name']=='HA')
        self.assertEqual(group['proxies'],['HA / 1','HA / 2'])
        with patch('server.country_for_server',return_value=('US','198.51.100.1')):
            server.add_country_flags({},protected_names=['HA / 1','HA / 2'])
        self.assertTrue({'HA / 1','HA / 2'} <= {n['name'] for n in server.read_config()['proxies']})

    def test_expiry_revokes_every_generated_entry(self):
        self.provision();server.create_failover(self.payload)
        record=server.profile_record()
        record['traffic']={'enabled':True,'enforce':True,'upload':0,'download':0,'total':1000,'expire':1}
        self.manager.inbounds.side_effect=lambda identity: {'inbounds':[{'id':50,'port':24443,
            'remark':'HA / 1' if identity=='primary' else 'HA / 2','up':0,'down':0}]}
        server.enforce_profile('clash')
        self.assertEqual({call.args for call in self.manager.delete_inbound.call_args_list},{('primary',50),('backup',50)})
        self.assertTrue(all(b['status']=='revoked' for b in record['remoteNodes']))

    def test_fragment_serialization_preserves_short_ids_and_shared_values(self):
        self.provision()
        doc=server.read_config()
        shared={'nested':['a',True,None,{'key':'00123'}]}
        doc['custom-a']=shared;doc['custom-b']=shared
        doc['proxies'].append({'name':'reality','type':'vless','server':'example.com','port':443,
            'uuid':'22453845-ff1a-471d-8214-25952a9348a6','tls':True,
            'reality-opts':{'public-key':'AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA','short-id':'001234'}})
        server.write_config(doc)
        for port in (444,445):
            server.mutate_node('PUT',{'oldName':'primary','config':dict(self.nodes[0],port=port)})
            actual=server.yaml_load(server.FILES[0].read_text())
            self.assertEqual(actual,server.read_config())
            self.assertEqual(actual['custom-a'],actual['custom-b'])
            self.assertEqual(actual['proxies'][-1]['reality-opts']['short-id'],'001234')

    def test_concurrent_node_edits_preserve_both_changes(self):
        self.provision(); errors=[]
        def edit(node):
            server.CTX.profile='clash'
            try:server.mutate_node('PUT',{'oldName':node['name'],'config':dict(node,port=444)})
            except Exception as exc:errors.append(exc)
        workers=[threading.Thread(target=edit,args=(n,)) for n in self.nodes[:2]]
        for w in workers:w.start()
        for w in workers:w.join()
        self.assertFalse(errors)
        self.assertEqual([n['port'] for n in server.read_config()['proxies'][:2]],[444,444])


if __name__=='__main__':unittest.main()
