import copy
from concurrent.futures import ThreadPoolExecutor
import json
import subprocess
import sys
import threading
import unittest
from unittest.mock import Mock

from server_monitor import HostMonitor, REMOTE_SAMPLE, derive_metrics


def sample(uptime=100, boot='boot-a', total=1000, idle=500, rx=500, tx=1000):
    return {'sampledAt': uptime+1000, 'uptime': uptime, 'bootId': boot,
            'hostname': 'test', 'cpuTicks': {'cpu': {'total': total, 'idle': idle},
            'cpu0': {'total': total, 'idle': idle}}, 'cpuModel': 'test cpu', 'cores': 1,
            'memory': {'total': 10000, 'used': 6000, 'available': 4000, 'swapTotal':0,'swapUsed':0},
            'interfaces': [{'name':'eth0','rxBytes':rx,'txBytes':tx},
                           {'name':'veth0','rxBytes':rx*2,'txBytes':tx*2}],
            'summaryInterfaces':['eth0'], 'addresses':[], 'disks':[], 'load':[0,0,0], 'os':'Linux','kernel':'test'}


class ServerMonitorTests(unittest.TestCase):
    def test_first_sample_never_invents_zero_cpu_or_network_speed(self):
        result=derive_metrics(sample())
        self.assertTrue(result['warmingUp'])
        self.assertIsNone(result['cpuPercent'])
        self.assertIsNone(result['network']['rxBytesPerSecond'])
        self.assertNotIn('bootId',result)

    def test_cpu_memory_network_deltas_and_default_route_selection(self):
        result=derive_metrics(sample(105,total=1200,idle=580,rx=2500,tx=6000),sample())
        self.assertEqual(result['cpuPercent'],60)
        self.assertEqual(result['memory']['percent'],60)
        self.assertEqual(result['network']['rxBytesPerSecond'],400)
        self.assertEqual(result['network']['txBytesPerSecond'],1000)
        self.assertEqual(result['network']['rxBytes'],2500)
        self.assertFalse(result['warmingUp'])

    def test_reboot_counter_reset_and_clock_regression_do_not_create_negative_rates(self):
        previous=sample()
        for now in (sample(3,boot='boot-b'),sample(90),sample(300),sample(105,rx=10,tx=20)):
            with self.subTest(now=now):
                result=derive_metrics(now,previous)
                self.assertIsNone(result['network']['rxBytesPerSecond'])

    def manager(self):
        manager=Mock()
        manager.lock=threading.RLock()
        manager.record.return_value={'id':'a','host':'198.51.100.1','sshPort':22,'sshUser':'root',
                                     'fingerprint':'pin','sshPassword':'encrypted','name':'test'}
        manager.execute.return_value=(0,json.dumps(sample()))
        return manager

    def test_cache_coalesces_requests_and_failed_refresh_is_explicitly_stale(self):
        clock=[0]
        monitor=HostMonitor(clock=lambda:clock[0])
        manager=self.manager()
        first=monitor.sample(manager,'a')
        first['hostname']='client-mutated'
        self.assertEqual(monitor.sample(manager,'a')['hostname'],'test')
        manager.connect.assert_called_once()
        clock[0]=6
        manager.execute.return_value=(0,json.dumps(sample(105,total=1200,idle=580,rx=2500,tx=6000)))
        second=monitor.sample(manager,'a')
        self.assertEqual(second['cpuPercent'],60)
        clock[0]=12
        manager.connect.side_effect=ValueError('offline')
        stale=monitor.sample(manager,'a')
        self.assertTrue(stale['stale'])
        self.assertEqual(stale['sampledAt'],second['sampledAt'])
        self.assertIn('offline',stale['error'])
        monitor.sample(manager,'a')
        self.assertEqual(manager.connect.call_count,3)

    def test_simultaneous_visitors_share_one_ssh_sample(self):
        monitor=HostMonitor()
        manager=self.manager()
        started, release=threading.Event(),threading.Event()
        def execute(*args,**kwargs):
            started.set()
            self.assertTrue(release.wait(2))
            return 0,json.dumps(sample())
        manager.execute.side_effect=execute
        with ThreadPoolExecutor(max_workers=4) as pool:
            first=pool.submit(monitor.sample,manager,'a')
            self.assertTrue(started.wait(1))
            others=[pool.submit(monitor.sample,manager,'a') for _ in range(3)]
            release.set()
            results=[future.result(3) for future in [first,*others]]
        self.assertEqual(len(results),4)
        manager.execute.assert_called_once()

    def test_initial_failure_is_backed_off_for_other_visitors(self):
        clock=[0]
        monitor=HostMonitor(clock=lambda:clock[0])
        manager=self.manager()
        manager.connect.side_effect=ValueError('offline')
        for _ in range(3):
            with self.assertRaises(ValueError):
                monitor.sample(manager,'a')
        manager.connect.assert_called_once()

    @unittest.skipUnless(sys.platform.startswith('linux'), 'requires Linux /proc')
    def test_collector_reads_real_linux_proc_without_network_service(self):
        result=subprocess.run([sys.executable,'-c',REMOTE_SAMPLE],capture_output=True,text=True,check=True,timeout=10)
        raw=json.loads(result.stdout)
        self.assertGreater(raw['cpuTicks']['cpu']['total'],0)
        self.assertGreater(raw['memory']['total'],0)
        self.assertIsInstance(raw['interfaces'],list)
        self.assertTrue(any(d['mount']=='/' for d in raw['disks']))


if __name__=='__main__':
    unittest.main()
