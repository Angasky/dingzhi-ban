"""Client-specific exports. Never turns ordered failover into latency balancing."""
import copy
import hashlib
import re
from datetime import datetime, timezone

XRAY_TYPES = {'vless', 'vmess', 'trojan', 'ss', 'socks5', 'http'}
V2RAYNG_TYPES = XRAY_TYPES | {'hysteria2'}


def usage_label(info):
    if not info.get('configured'):
        return ''
    def size(value):
        for unit in ('B', 'KiB', 'MiB', 'GiB', 'TiB'):
            if value < 1024 or unit == 'TiB':
                return f'{value:.2f} {unit}'
            value /= 1024
    remaining = size(info['remaining']) if info.get('remaining') is not None else '不限量'
    expiry = datetime.fromtimestamp(info['expire'], timezone.utc).strftime('%Y-%m-%d %H:%M UTC') if info.get('expire') is not None else '长期有效'
    return f"已用 {size(info['used'])} · 剩余 {remaining} · 到期 {expiry}"


def shadowrocket_name(name, group=False, scope=''):
    # No configuration separators or control characters can enter a policy line.
    digest = hashlib.sha256((scope + '\0' + name).encode('utf-8')).hexdigest()[:16]
    label = re.sub(r'[\x00-\x1f\x7f-\x9f\u2028\u2029,=;#\[\]\\"\']', ' ', name).strip()[:64]
    return f'MX-{"HA" if group else "N"}-{digest} {label}'


def xray_path(node, tag):
    from remote_manager import node_outbound
    if node.get('dialer-proxy'):
        raise ValueError('主备导出需要服务器转发节点，不能把 Mihomo dialer-proxy 当作独立节点')
    # Unsupported options must fail the whole group, not silently change its transport.
    smux = node.get('smux') or {}
    if not isinstance(smux, dict):
        raise ValueError('smux 配置必须为对象')
    if node.get('plugin') or smux.get('enabled') or node.get('packet-encoding') is not None:
        raise ValueError('此节点含尚未适配的 Xray 传输选项')
    if node.get('fingerprint') or node.get('http-opts') or node.get('h2-opts'):
        raise ValueError('此节点含尚未适配的证书固定或传输选项')
    ws = node.get('ws-opts') or {}
    if any(key not in {'path', 'headers'} for key in ws):
        raise ValueError('WebSocket 扩展选项不能无损转换为此 Xray 配置')
    if any(key != 'grpc-service-name' for key in (node.get('grpc-opts') or {})):
        raise ValueError('gRPC 扩展选项不能无损转换为此 Xray 配置')
    if node.get('alpn') is not None and not isinstance(node['alpn'], list):
        raise ValueError('ALPN 必须为数组')
    source = copy.deepcopy(node)
    if 'alterId' not in source and 'alter-id' in source:
        source['alterId'] = source['alter-id']
    outbound = node_outbound(source, tag)
    tls = outbound.get('streamSettings', {}).get('tlsSettings')
    if tls is not None:
        if node.get('alpn'):
            tls['alpn'] = copy.deepcopy(node['alpn'])
        if node.get('client-fingerprint'):
            tls['fingerprint'] = node['client-fingerprint']
    return outbound


def xray_failover(name, nodes, interval, usage='', probe_url='https://www.gstatic.com/generate_204'):
    if not 2 <= len(nodes) <= 8:
        raise ValueError('Xray 主备需要 2–8 个服务器转发节点')
    paths = [xray_path(node, f'mx-path-{i}/') for i, node in enumerate(nodes)]
    outbounds = paths + [{'tag': 'mx-reject', 'protocol': 'blackhole', 'settings': {}}]
    balancers, rules = [], []
    for i, path in enumerate(paths):
        next_tag = 'mx-reject' if i == len(paths)-1 else f'mx-next-{i+1}'
        # One healthy candidate per stage gives strict priority, including failback.
        balancers.append({'tag': f'mx-balance-{i}', 'selector': [path['tag']],
                          'fallbackTag': next_tag, 'strategy': {'type': 'random'}})
        if i:
            outbounds.append({'tag': f'mx-next-{i}', 'protocol': 'loopback',
                              'settings': {'inboundTag': f'mx-stage-{i}'}})
            rules.append({'type': 'field', 'inboundTag': [f'mx-stage-{i}'], 'balancerTag': f'mx-balance-{i}'})
    rules.append({'type': 'field', 'network': 'tcp,udp', 'balancerTag': 'mx-balance-0'})
    return {'remarks': name + (f' | {usage}' if usage else ''), 'log': {'loglevel': 'warning'},
            'inbounds': [{'tag': 'socks', 'listen': '127.0.0.1', 'port': 10808, 'protocol': 'socks',
                          'settings': {'auth': 'noauth', 'udp': True}},
                         {'tag': 'http', 'listen': '127.0.0.1', 'port': 10809, 'protocol': 'http', 'settings': {}}],
            'outbounds': outbounds,
            'routing': {'domainStrategy': 'AsIs', 'rules': rules, 'balancers': balancers},
            'observatory': {'subjectSelector': ['mx-path-'], 'probeURL': probe_url,
                            'probeInterval': f'{interval}s', 'enableConcurrency': True}}


def managed_failovers(doc, record):
    nodes = {n['name']: n for n in doc.get('proxies', [])}
    groups = {g['name']: g for g in doc.get('proxy-groups', [])}
    chains = {c['name']: c for c in record.get('serverChains', [])}
    active = {(b['serverId'], b['inboundId']) for b in record.get('remoteNodes', []) if b.get('status') == 'active'}
    result, skipped = [], []
    for item in record.get('failovers', []):
        try:
            if type(item.get('interval')) is not int or not 10 <= item['interval'] <= 600:
                raise ValueError('主备检测间隔无效')
            paths = item.get('paths', [])
            if item.get('status') != 'active' or not 2 <= len(paths) <= 8 or len(set(paths)) != len(paths):
                raise ValueError('主备组尚未完成创建或正在清理')
            group = groups.get(item['name'], {})
            if group.get('type') != 'fallback' or group.get('proxies') != paths:
                raise ValueError('主备组配置与受管路径不一致，请重新创建')
            selected = []
            for path in paths:
                chain = chains.get(path, {})
                if (chain.get('serverId'), chain.get('inboundId')) not in active or chain.get('exit') != item.get('exit'):
                    raise ValueError('路径缺少有效订阅绑定或出口不一致')
                node = nodes.get(path)
                if not node or node.get('dialer-proxy'):
                    raise ValueError('路径节点缺失或不是独立服务器转发')
                selected.append(copy.deepcopy(node))
            result.append((item, selected))
        except ValueError as exc:
            skipped.append({'name': item['name'], 'type': 'fallback', 'reason': str(exc)})
    return result, skipped


def shadowrocket_config(groups, usage='', scope=''):
    if not groups:
        raise ValueError('请先创建有效的多入口服务器主备组')
    lines = ['# Mxioc Shadowrocket: first import the paired node subscription, then activate this configuration.',
             '# Do not enable global fallback: it can select nodes outside these fixed-exit groups.']
    if usage:
        lines.append('# ' + usage)
    lines += ['[General]', 'udp-policy-not-supported-behaviour = REJECT', '', '[Proxy Group]']
    labels = [shadowrocket_name(item['name'], group=True, scope=scope) for item, _ in groups]
    lines.append('MX-Select = select,' + ','.join(labels))
    for (item, nodes), label in zip(groups, labels):
        aliases = [shadowrocket_name(n['name'], scope=scope) for n in nodes]
        lines.append(f'{label} = fallback,' + ','.join(aliases) +
                     f",interval={item['interval']},timeout=5,url=https://www.gstatic.com/generate_204")
    lines += ['', '[Rule]', 'FINAL,MX-Select', '']
    return '\n'.join(lines).encode('utf-8')
