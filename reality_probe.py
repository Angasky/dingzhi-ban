"""Disposable loopback-only REALITY test; no production nodes/counters involved."""
import json
import secrets
import uuid


def probe_script(domain, address):
    import ipaddress
    target = ipaddress.ip_address(address)
    if not target.is_global:
        raise ValueError('Reality 探测仅支持已验证的公网地址')
    destination = f'[{target}]:443' if target.version == 6 else f'{target}:443'
    from core_adapter import key_pair
    keys = key_pair()
    identity, short_id = str(uuid.uuid4()), secrets.token_hex(8)
    server_port, socks_port = secrets.SystemRandom().sample(range(20001, 65536), 2)
    server = {'log': {'loglevel': 'none'}, 'inbounds': [{
        'listen': '127.0.0.1', 'port': server_port, 'protocol': 'vless',
        'settings': {'clients': [{'id': identity, 'flow': 'xtls-rprx-vision'}], 'decryption': 'none'},
        'streamSettings': {'network': 'tcp', 'security': 'reality', 'realitySettings': {
            'target': destination, 'serverNames': [domain], 'privateKey': keys['privateKey'],
            'shortIds': [short_id], 'minClientVer': '1.8.2'}}}],
        'outbounds': [{'protocol': 'blackhole', 'settings': {'response': {'type': 'http'}}}]}
    client = {'log': {'loglevel': 'none'}, 'inbounds': [{
        'listen': '127.0.0.1', 'port': socks_port, 'protocol': 'socks', 'settings': {'auth': 'noauth'}}],
        'outbounds': [{'protocol': 'vless', 'settings': {'vnext': [{
            'address': '127.0.0.1', 'port': server_port,
            'users': [{'id': identity, 'encryption': 'none', 'flow': 'xtls-rprx-vision'}]}]},
            'streamSettings': {'network': 'tcp', 'security': 'reality', 'realitySettings': {
                'serverName': domain, 'fingerprint': 'chrome', 'publicKey': keys['publicKey'], 'shortId': short_id}}}]}
    return f'''set -eu
command -v curl >/dev/null && command -v timeout >/dev/null || exit 12
binary=''
for candidate in /usr/local/x-ui/bin/xray* /usr/local/bin/xray; do
 if test -f "$candidate" && test -x "$candidate"; then binary="$candidate"; break; fi
done
test -n "$binary" || exit 13
server_pid=''; client_pid=''
cleanup() {{
 test -z "$client_pid" || kill "$client_pid" 2>/dev/null || true
 test -z "$server_pid" || kill "$server_pid" 2>/dev/null || true
 wait 2>/dev/null || true
}}
trap cleanup EXIT
trap 'exit 130' INT TERM HUP
timeout 18 "$binary" run -config stdin: <<'MXIOC_TEST_SERVER' >/dev/null 2>&1 &
{json.dumps(server, separators=(',', ':'))}
MXIOC_TEST_SERVER
server_pid=$!
timeout 18 "$binary" run -config stdin: <<'MXIOC_TEST_CLIENT' >/dev/null 2>&1 &
{json.dumps(client, separators=(',', ':'))}
MXIOC_TEST_CLIENT
client_pid=$!
sleep 0.6
for attempt in 1 2; do
 result=$(curl --disable --noproxy '' --socks5-hostname 127.0.0.1:{socks_port} --silent --head --output /dev/null --connect-timeout 4 --max-time 6 --write-out '%{{http_code}}' http://reality-probe.invalid/) || exit 14
 test "$result" = 403 || exit 15
done
printf 'MXIOC_REALITY_OK\\n'
'''
