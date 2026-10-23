import base64
import copy
import errno
import json
import os
from pathlib import Path
import re
import socket
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import Mock, patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import bandwidth
import server
import test_remote_manager as remote_fixture
import test_subscription_enforcement as subscription_fixture


class BandwidthOwnershipTests(unittest.TestCase):
    setUp=subscription_fixture.EnforcementTests.setUp
    configure=subscription_fixture.EnforcementTests.configure

    def test_only_current_subscription_owned_node_can_be_limited(self):
        self.configure();row=self.manager.inbounds.return_value['inbounds'][0]
        row.update(protocol='shadowsocks',settings={'method':'aes-128-gcm','password':'secret'})
        self.manager.set_bandwidth.return_value={'uploadKbps':1000,'downloadKbps':2000,'appliedAt':1}
        result=server.set_node_bandwidth({'serverId':'server-a','inboundId':1,'uploadMbps':1,'downloadMbps':2})
        self.assertEqual(result['downloadKbps'],2000)
        self.manager.set_bandwidth.assert_called_once_with('server-a',1,1000,2000)
        self.assertIn('bandwidth',server.profile_record()['remoteNodes'][0])
        self.manager.set_bandwidth.reset_mock()
        with self.assertRaisesRegex(ValueError,'绑定当前订阅'):
            server.set_node_bandwidth({'serverId':'other','inboundId':1,'downloadMbps':2})
        self.manager.set_bandwidth.assert_not_called()

    def test_identity_change_or_shared_inbound_prevents_limit(self):
        record=self.configure();row=self.manager.inbounds.return_value['inbounds'][0]
        row.update(protocol='vless',settings={'clients':[{'id':'one'}]})
        record['remoteNodes'][0]['identityHash']=server.remote_node_identity(row)
        row['settings']['clients'][0]['id']='changed'
        with self.assertRaisesRegex(ValueError,'身份已变化'):
            server.set_node_bandwidth({'serverId':'server-a','inboundId':1,'uploadMbps':1})
        self.manager.set_bandwidth.assert_not_called()


class BandwidthManagerTests(unittest.TestCase):
    setUp=remote_fixture.RemoteManagerTests.setUp
    tearDown=remote_fixture.RemoteManagerTests.tearDown
    with_fake_api=remote_fixture.RemoteManagerTests.with_fake_api

    def api(self):
        api=remote_fixture.FakeAPI();api.ssh=Mock()
        api.rows=[{'id':1,'port':24443,'protocol':'shadowsocks','listen':'','settings':{'method':'aes-128-gcm','password':'secret'}}]
        return api

    def test_apply_persist_and_clear_only_own_table(self):
        api=self.api()
        with self.with_fake_api(api),patch.object(self.manager,'execute',return_value=(0,'')) as execute:
            result=self.manager.set_bandwidth(self.identity,1,1000,2000)
            script=execute.call_args.args[2]
            subprocess.run(['bash','-n'],input=script,text=True,check=True)
            self.assertNotIn('flush ruleset',script);self.assertNotIn('qdisc',script)
            self.assertIn('nft -c -f',script);self.assertIn('mxioc-bandwidth-restore.service',script)
            self.assertEqual(self.manager.record(self.identity)['bandwidths']['1']['port'],24443)
            self.manager.set_bandwidth(self.identity,1,0,0)
            self.assertEqual(self.manager.record(self.identity)['bandwidths'],{})

    def test_failed_apply_does_not_report_or_persist_success(self):
        api=self.api()
        with self.with_fake_api(api),patch.object(self.manager,'execute',return_value=(12,'missing nft')):
            with self.assertRaisesRegex(ValueError,'应用失败'):self.manager.set_bandwidth(self.identity,1,1000,0)
        self.assertFalse(self.manager.record(self.identity).get('bandwidths'))

    def test_shared_management_port_and_duplicate_port_are_rejected(self):
        for kind in ('shared','management','duplicate'):
            api=self.api()
            if kind=='shared':api.rows[0]['settings']['clients']=[{'id':'a'},{'id':'b'}]
            elif kind=='management':api.rows[0]['port']=22
            else:api.rows.append({**api.rows[0],'id':2})
            with self.subTest(kind=kind),self.with_fake_api(api),patch.object(self.manager,'execute') as execute:
                with self.assertRaises(ValueError):self.manager.set_bandwidth(self.identity,1,1000,0)
                execute.assert_not_called()

    def test_delete_clears_limit_and_failed_delete_restores_it(self):
        api=self.api()
        original=bandwidth.policy(api.rows[0],1000,2000)
        self.manager.record(self.identity)['bandwidths']={'1':original}
        request=api.request
        api.request=lambda path,*a,**k: (_ for _ in ()).throw(ValueError('delete offline')) if '/inbounds/del/' in path else request(path,*a,**k)
        with self.with_fake_api(api),patch.object(self.manager,'execute',return_value=(0,'')):
            with self.assertRaisesRegex(ValueError,'offline'):self.manager.delete_inbound(self.identity,1)
            self.assertEqual(self.manager.record(self.identity)['bandwidths'],{'1':original})
            api.request=request
            self.manager.delete_inbound(self.identity,1)
            self.assertEqual(self.manager.record(self.identity)['bandwidths'],{})
            self.assertFalse(api.rows)


class BandwidthScriptTests(unittest.TestCase):
    @unittest.skipUnless(sys.platform.startswith('linux'), 'requires Linux executable scripts and filesystem paths')
    def test_persistence_failure_rolls_back_previous_kernel_and_saved_rules(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);binpath=root/'bin';binpath.mkdir()
            kernel=root/'kernel';kernel.write_text('old kernel rules\n')
            saved=root/'etc/mxioc-bandwidth/rules.nft';saved.parent.mkdir(parents=True);saved.write_text('old persisted rules\n')
            unit=root/'etc/systemd/system/mxioc-bandwidth-restore.service';unit.parent.mkdir(parents=True);unit.write_text('old service\n')
            (binpath/'nft').write_text('#!/bin/sh\nif [ "$1" = list ]; then cat "'+str(kernel)+'"; elif [ "$1" = -f ]; then sed \'/^delete table /d\' "$2" > "'+str(kernel)+'"; fi\n')
            (binpath/'systemctl').write_text('#!/bin/sh\nif [ "$1" = enable ]; then exit 22; fi\nexit 0\n')
            for file in binpath.iterdir():file.chmod(0o755)
            script=bandwidth.apply_script({'1':bandwidth.policy({'port':24443,'protocol':'vless'},1000,2000)})
            for prefix in ('/etc/','/var/lock','/tmp/mxioc-bandwidth.'):
                script=script.replace(prefix,str(root)+prefix)
            env={**os.environ,'PATH':str(binpath)+':'+os.environ['PATH']}
            (root/'tmp').mkdir()
            result=subprocess.run(['bash'],input=script,text=True,capture_output=True,env=env)
            self.assertNotEqual(result.returncode,0,result.stdout+result.stderr)
            self.assertEqual(kernel.read_text(),'old kernel rules\n')
            self.assertEqual(saved.read_text(),'old persisted rules\n')
            self.assertEqual(unit.read_text(),'old service\n')


def kernel_child(nft):
    subprocess.run(['ip','link','set','lo','up'],check=True)
    configured={'1':bandwidth.policy({'port':42001,'protocol':'shadowsocks'},1000,2000)}
    with tempfile.TemporaryDirectory() as directory:
        path=Path(directory)/'rules.nft';path.write_text(bandwidth.rules(configured))
        subprocess.run([nft,'-c','-f',str(path)],check=True)
        subprocess.run([nft,'-f',str(path)],check=True)
        def measure(port,download=False,ipv6=False):
            family=socket.AF_INET6 if ipv6 else socket.AF_INET;host='::1' if ipv6 else '127.0.0.1'
            receiver=socket.socket(family,socket.SOCK_DGRAM);sender=socket.socket(family,socket.SOCK_DGRAM)
            receiver.bind((host,0 if download else port));sender.bind((host,port if download else 0))
            receiver.settimeout(.1);count=[0];done=threading.Event()
            def collect():
                while not done.is_set():
                    try:count[0]+=len(receiver.recv(65536))
                    except socket.timeout:pass
            thread=threading.Thread(target=collect,daemon=True);thread.start()
            start=time.monotonic();target=start;payload=b'x'*1000;baseline=None
            while time.monotonic()-start<4.5:
                if baseline is None and time.monotonic()-start>=1.5:baseline=(count[0],time.monotonic())
                try:sender.sendto(payload,receiver.getsockname())
                except OSError as exc:
                    if exc.errno not in (errno.EPERM,errno.ENOBUFS):raise
                target+=.002
                time.sleep(max(0,target-time.monotonic()))
            elapsed=time.monotonic()-baseline[1];time.sleep(.15);done.set();thread.join();receiver.close();sender.close()
            return round((count[0]-baseline[0])*8/elapsed/1_000_000,3)
        up=measure(42001);down=measure(42001,download=True);other=measure(42002);v6=measure(42001,ipv6=True)
        assert .3<up<1.6,(up,'upload');assert .6<down<2.6,(down,'download');assert other>2.5,(other,'other node');assert .3<v6<1.6,(v6,'IPv6')
        path.write_text('delete table inet mxioc_bandwidth\n'+bandwidth.rules({}))
        subprocess.run([nft,'-f',str(path)],check=True)
        cleared=measure(42001);assert cleared>2.5,(cleared,'clear')
        print(json.dumps({'upload_1Mbps':up,'download_2Mbps':down,'other_port_unlimited':other,'IPv6_1Mbps':v6,'after_clear':cleared}))


@unittest.skipUnless(os.environ.get('MXIOC_NFT_BIN'),'set MXIOC_NFT_BIN for isolated kernel policing tests')
class NativeBandwidthTests(unittest.TestCase):
    def test_kernel_polices_both_directions_and_not_other_port(self):
        result=subprocess.run(['unshare','-Urn',sys.executable,str(Path(__file__).resolve()),'--kernel-child',os.environ['MXIOC_NFT_BIN']],capture_output=True,text=True,timeout=35)
        self.assertEqual(result.returncode,0,result.stdout+result.stderr)
        print('PASS isolated nftables: '+result.stdout.strip())


if __name__=='__main__':
    if len(sys.argv)>1 and sys.argv[1]=='--kernel-child':kernel_child(sys.argv[2])
    else:unittest.main()
