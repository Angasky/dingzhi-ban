"""Real Xray ordered failover with local proxies, no production traffic."""
import json
import os
from pathlib import Path
import subprocess
import socket
import tempfile
import threading
import time
import unittest
from urllib.request import build_opener, ProxyHandler

import client_adapter
import test_mihomo_failover as fixtures


class RecordingHandler(fixtures.Handler):
    def handle(self):
        # Peek without consuming bytes; count payload connections, not health probes.
        try:
            if b'payload.test.invalid' in self.connection.recv(4096, socket.MSG_PEEK):
                self.server.payload_hits += 1
        except OSError:
            pass
        super().handle()


@unittest.skipUnless(os.environ.get('MXIOC_XRAY_BIN'), 'set MXIOC_XRAY_BIN for native client failover verification')
class XrayFailoverTests(unittest.TestCase):
    def test_priority_failover_failback_and_fixed_exit(self):
        proxies=[]
        for _ in range(4):
            proxy=fixtures.Proxy(('127.0.0.1',0),RecordingHandler)
            proxy.egress=None;proxy.hits=proxy.payload_hits=0
            proxy.available=threading.Event();proxy.available.set()
            threading.Thread(target=proxy.serve_forever,daemon=True).start();proxies.append(proxy)
            self.addCleanup(proxy.server_close);self.addCleanup(proxy.shutdown)
        egress,*entries=proxies
        nodes=[]
        for i,p in enumerate(entries):
            p.egress=egress.server_address
            nodes.append({'name':f'path-{i}','type':'http','server':'127.0.0.1','port':p.server_address[1]})
        config=client_adapter.xray_failover('HA',nodes,10,probe_url='http://health.test.invalid/check')
        port=fixtures.free_port()
        config['inbounds']=[{'tag':'http','listen':'127.0.0.1','port':port,'protocol':'http','settings':{}}]
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'config.json';path.write_text(json.dumps(config))
            checked=subprocess.run([os.environ['MXIOC_XRAY_BIN'],'run','-test','-config',str(path)],capture_output=True,text=True,timeout=15)
            self.assertEqual(checked.returncode,0,checked.stdout+checked.stderr)
            with open(Path(directory)/'xray.log','w+') as log:
                process=subprocess.Popen([os.environ['MXIOC_XRAY_BIN'],'run','-config',str(path)],stdout=log,stderr=log)
                client=build_opener(ProxyHandler({'http':f'http://127.0.0.1:{port}'}))
                def request():
                    with client.open('http://payload.test.invalid/egress',timeout=3) as response:
                        self.assertEqual(response.read(),b'fixed-exit\n')
                def wait_path(index):
                    deadline=time.monotonic()+35
                    while time.monotonic()<deadline:
                        before=entries[index].payload_hits
                        try:
                            request()
                            if entries[index].payload_hits>before:return
                        except OSError:pass
                        time.sleep(.3)
                    log.flush();log.seek(0)
                    self.fail(f'Failed to switch to path {index}: '+log.read()[-4000:])
                try:
                    wait_path(0)
                    entries[0].available.clear();wait_path(1)
                    entries[1].available.clear();wait_path(2)
                    entries[0].available.set();wait_path(0)
                    for p in entries:p.available.clear()
                    with self.assertRaises(OSError):request()
                    self.assertGreater(egress.payload_hits,0)
                    self.assertNotIn('freedom',{o['protocol'] for o in config['outbounds']})
                    print('PASS Xray: primary -> backup 1 -> backup 2 -> primary; common egress; all down fails closed')
                finally:
                    process.terminate()
                    try:process.wait(5)
                    except subprocess.TimeoutExpired:process.kill();process.wait(5)
