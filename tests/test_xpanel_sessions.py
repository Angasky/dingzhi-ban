"""Real loopback HTTP sessions over mocked SSH forwarding; no production panel."""
import copy
import http.client
import json
import socket
import tempfile
import threading
import unittest
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest.mock import Mock, patch

import remote_manager as remote


class XPanelSessionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.manager = remote.RemoteManager(Path(self.temp.name), None, None)
        self.payload = dict(name='XPanel', host='198.51.100.30', sshPort=22, sshUser='root',
                            sshPassword='ssh-private', fingerprint='SHA256:'+'a'*43,
                            connectionMode='existing', installType='x-panel', panelUser='admin',
                            panelPassword='existing-password', panelAddress='http://panel.example.com:13145/private/')
        self.identity = self.manager.save(self.payload)['id']

    def test_discovery_only_reads_database_without_cli_or_service_changes(self):
        self.manager.save({**self.payload, 'id': self.identity, 'panelAddress': ''})
        with patch.object(self.manager, 'execute', return_value=(0, json.dumps({
            'webPort': '13145', 'webBasePath': '/private/', 'webCertFile': '', 'webDomain': ''}))) as execute:
            api = self.manager.panel(Mock(), copy.deepcopy(self.manager.record(self.identity)))
        self.assertEqual(api.record['panelPort'], 13145)
        self.assertEqual(execute.call_count, 1)
        script = execute.call_args.args[2]
        self.assertIn('?mode=ro', script)
        self.assertIn('query_only=ON', script)
        for forbidden in ('./x-ui', 'setting -', 'restart', 'UPDATE ', 'INSERT ', 'DELETE ', 'users'):
            self.assertNotIn(forbidden, script)

    def test_browser_and_controller_cookies_coexist_and_controller_reuses_session(self):
        state = {'logins': 0, 'login_attempts': 0, 'valid': set(), 'headers': [], 'adds': 0}

        class Handler(BaseHTTPRequestHandler):
            def reply(self, value=None, status=200, cookie=None):
                body = json.dumps({'success': status == 200, 'obj': value}).encode()
                self.send_response(status)
                if cookie:
                    self.send_header('Set-Cookie', '3x-ui='+cookie+'; Path=/; HttpOnly')
                self.send_header('Content-Length', str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def do_GET(self):
                if self.path.endswith('/csrf-token'):
                    self.reply(status=404)
                    return
                cookie = SimpleCookie(self.headers.get('Cookie', ''))
                session = cookie.get('3x-ui')
                state['headers'].append(self.headers.get('Cookie', ''))
                self.reply([], status=200 if session and session.value in state['valid'] else 401)

            def do_POST(self):
                self.rfile.read(int(self.headers.get('Content-Length', '0')))
                if self.path.endswith('/login'):
                    state['login_attempts'] += 1
                    if state.get('deny_login'):
                        return self.reply(status=401)
                    state['logins'] += 1
                    token = 'session-'+str(state['logins'])
                    state['valid'].add(token)
                    self.reply(cookie=token)
                else:
                    cookie = SimpleCookie(self.headers.get('Cookie', ''))
                    session = cookie.get('3x-ui')
                    if not session or session.value not in state['valid']:
                        return self.reply(status=401)
                    state['adds'] += 1
                    self.reply({'id': state['adds']})

            def log_message(self, *_):
                pass

        httpd = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        thread = threading.Thread(target=httpd.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(httpd.server_close)
        self.addCleanup(httpd.shutdown)
        ssh, transport = Mock(), Mock()
        transport.open_channel.side_effect = lambda *args, **kwargs: socket.create_connection(httpd.server_address)
        ssh.get_transport.return_value = transport

        def browser(path, cookie=None):
            client = http.client.HTTPConnection(*httpd.server_address)
            client.request('POST' if path == 'login' else 'GET', '/private/'+path,
                           body=b'' if path == 'login' else None,
                           headers={'Cookie': cookie} if cookie else {})
            response = client.getresponse()
            status, new_cookie = response.status, response.getheader('Set-Cookie')
            response.read()
            client.close()
            return status, new_cookie

        def panel():
            return self.manager.panel(ssh, copy.deepcopy(self.manager.record(self.identity)))

        with patch.object(self.manager, 'execute', side_effect=AssertionError('no setting CLI for XPanel address')):
            self.assertEqual(panel().request('panel/api/inbounds/list'), [])
            self.assertEqual(state['logins'], 1)
            status, cookie = browser('login')
            self.assertEqual(status, 200)
            for _ in range(3):
                self.assertEqual(panel().request('panel/api/inbounds/list'), [])
                self.assertEqual(browser('panel/', cookie.split(';')[0])[0], 200)
            self.assertEqual(state['logins'], 2, 'one controller login and one browser login')
            self.assertNotIn('session-2', state['headers'][0])
            # Server expires only the controller session. One explicit 401 can
            # reauthenticate and retry; the browser session remains valid.
            state['valid'].discard('session-1')
            self.assertEqual(panel().request('panel/api/inbounds/add', {'port': 443}), {'id': 1})
            self.assertEqual(state['adds'], 1)
            self.assertEqual(state['logins'], 3)
            self.assertEqual(browser('panel/', cookie.split(';')[0])[0], 200)
            self.manager.save({**self.payload, 'id': self.identity, 'panelPassword': 'changed-password'})
            self.assertFalse(panel().logged_in, 'credential changes invalidate cached login')
            state['deny_login'] = True
            attempts = state['login_attempts']
            with self.assertRaises(ValueError):
                panel().request('panel/api/inbounds/list')
            with self.assertRaisesRegex(ValueError, '暂停重试'):
                panel().request('panel/api/inbounds/list')
            self.assertEqual(state['login_attempts'], attempts+1)
            self.assertEqual(browser('panel/', cookie.split(';')[0])[0], 200)
        self.assertNotIn('cookies', self.manager.public(self.manager.record(self.identity)))
        self.assertNotIn('session-3', self.manager.path.read_text(encoding='utf-8'))
        self.manager.remove(self.identity)
        self.assertFalse(self.manager.panel_sessions)

    def test_auth_retry_is_bounded_and_ambiguous_mutations_are_never_replayed(self):
        record = copy.deepcopy(self.manager.record(self.identity))
        api = remote.PanelAPI(Mock(), record, '', 'password')
        api._request = Mock(side_effect=remote.PanelSessionExpired('expired'))
        with self.assertRaises(remote.PanelSessionExpired):
            api.request('panel/api/inbounds/add', {'port': 443})
        self.assertEqual(api._request.call_count, 2)
        api._request = Mock(side_effect=http.client.RemoteDisconnected('lost response'))
        with self.assertRaises(http.client.RemoteDisconnected):
            api.request('panel/api/inbounds/add', {'port': 443})
        self.assertEqual(api._request.call_count, 1)


if __name__ == '__main__':
    unittest.main()
