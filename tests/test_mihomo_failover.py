"""Optional native integration: two local ingress proxies share one local egress.
No internet targets or production servers. MXIOC_MIHOMO_BIN enables this test.
"""
import json
import os
from pathlib import Path
import select
import socket
import socketserver
import subprocess
import tempfile
import threading
import time
import unittest
from urllib.request import build_opener, ProxyHandler

import server


def free_port():
    with socket.socket() as s:
        s.bind(('127.0.0.1',0));return s.getsockname()[1]


class Proxy(socketserver.ThreadingTCPServer):
    allow_reuse_address=True
    daemon_threads=True


class Handler(socketserver.StreamRequestHandler):
    def handle(self):
        try:
            if not self.server.available.is_set():return
            line=self.rfile.readline()
            while self.rfile.readline() not in (b'\r\n', b'\n', b''):pass
            if not line:return
            if self.server.egress is None:
                if line.startswith(b'CONNECT '):
                    self.wfile.write(b'HTTP/1.1 200 Connection established\r\n\r\n');self.wfile.flush()
                    self.rfile.readline()
                    while self.rfile.readline() not in (b'\r\n', b'\n', b''):pass
                self.wfile.write(b'HTTP/1.1 200 OK\r\nContent-Length: 11\r\nConnection: close\r\n\r\nfixed-exit\n')
                self.server.hits+=1
            else:
                self.server.hits+=1
                with socket.create_connection(self.server.egress,timeout=2) as upstream:
                    upstream.sendall(line+b'Host: test.invalid\r\nConnection: close\r\n\r\n')
                    while True:
                        ready,_,_=select.select([upstream,self.connection],[],[],3)
                        if not ready:break
                        for source in ready:
                            data=source.recv(65536)
                            if not data:return
                            (self.connection if source is upstream else upstream).sendall(data)
                    self.server.hits+=1
        except (OSError,ValueError):pass


@unittest.skipUnless(os.environ.get('MXIOC_MIHOMO_BIN'),'set MXIOC_MIHOMO_BIN for native failover verification')
class NativeFailoverTests(unittest.TestCase):
    def test_primary_failure_backup_recovery_and_all_down(self):
        proxies=[]
        for _ in range(3):
            proxy=Proxy(('127.0.0.1',0),Handler);proxy.egress=None;proxy.hits=0
            proxy.available=threading.Event();proxy.available.set()
            threading.Thread(target=proxy.serve_forever,daemon=True).start();proxies.append(proxy)
            self.addCleanup(proxy.server_close);self.addCleanup(proxy.shutdown)
        egress,primary,backup=proxies
        primary.egress=backup.egress=egress.server_address
        port,control=free_port(),free_port()
        with tempfile.TemporaryDirectory() as directory:
            config={'mixed-port':port,'external-controller':f'127.0.0.1:{control}','mode':'rule',
                    'log-level':'silent','proxies':[{'name':name,'type':'http','server':'127.0.0.1',
                     'port':p.server_address[1]} for name,p in [('primary',primary),('backup',backup)]],
                    'proxy-groups':[server.failover_group('HA',['primary','backup'],10)], 'rules':['MATCH,HA']}
            # Health checks stay on loopback; production uses an HTTPS 204 endpoint.
            config['proxy-groups'][0]['url']='http://health.test.invalid/check'
            config['proxy-groups'][0]['timeout']=1000
            path=Path(directory)/'config.yaml';path.write_text(server.yaml_dump(config))
            checked=subprocess.run([os.environ['MXIOC_MIHOMO_BIN'],'-t','-d',directory,'-f',str(path)],capture_output=True,text=True,timeout=15)
            self.assertEqual(checked.returncode,0,checked.stdout+checked.stderr)
            log=open(Path(directory)/'mihomo.log','w+')
            process=subprocess.Popen([os.environ['MXIOC_MIHOMO_BIN'],'-d',directory,'-f',str(path)],stdout=log,stderr=log)
            direct=build_opener(ProxyHandler({}))
            client=build_opener(ProxyHandler({'http':f'http://127.0.0.1:{port}'}))
            def selected():
                with direct.open(f'http://127.0.0.1:{control}/proxies/HA',timeout=2) as response:return json.load(response)['now']
            def wait_for(name):
                deadline=time.monotonic()+28
                while time.monotonic()<deadline:
                    try:
                        if selected()==name:return
                    except OSError:pass
                    time.sleep(.25)
                self.fail(f'Expected {name}; selected {selected()}')
            def request():
                with client.open('http://payload.test.invalid/egress',timeout=4) as response:
                    self.assertEqual(response.read(),b'fixed-exit\n')
            try:
                wait_for('primary'); request();self.assertGreater(primary.hits,0)
                primary.available.clear();wait_for('backup');request();self.assertGreater(backup.hits,0)
                primary.available.set();wait_for('primary');before=primary.hits;request();self.assertGreater(primary.hits,before)
                primary.available.clear();backup.available.clear()
                with self.assertRaises(OSError):request()
                self.assertGreater(egress.hits,0)
                print('PASS native Mihomo: primary -> backup -> primary; same egress; all down fails closed')
            finally:
                process.terminate()
                try:process.wait(5)
                except subprocess.TimeoutExpired:process.kill();process.wait(5)
                log.close()
