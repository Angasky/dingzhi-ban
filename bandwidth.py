"""Dedicated inbound policing; never replaces a host's global firewall or qdisc."""
import base64
from decimal import Decimal, InvalidOperation


def scaled_number(value, scale, minimum, maximum, label):
    if isinstance(value, bool) or not isinstance(value, (int, float, str)):
        raise ValueError(f'{label}必须为有效数字')
    try:
        number=Decimal(str(value))
        scaled=number*scale
        if not number.is_finite() or not minimum <= number <= maximum or scaled != scaled.to_integral_value():
            raise ValueError()
        return int(scaled)
    except (InvalidOperation, ValueError, OverflowError):
        raise ValueError(f'{label}范围为 {minimum}–{maximum}，最多支持三位小数') from None


def multiplier_units(value):
    return scaled_number(value,1000,0,100,'流量倍率')


def rate_kbps(value):
    if value is None or value == '':
        return 0
    return scaled_number(value,1000,0,10000,'带宽 Mbps')


def policy(row, upload_kbps, download_kbps):
    for rate in (upload_kbps, download_kbps):
        if type(rate) is not int or not 0 <= rate <= 10_000_000 or (rate and rate < 100):
            raise ValueError('带宽须为 0.1–10000 Mbps，零或留空表示不限')
    port=row.get('port')
    if type(port) is not int or not 1 <= port <= 65535:
        raise ValueError('入站端口无效')
    kind=row.get('protocol')
    if kind in ('hysteria2','tuic'):
        protocols=['udp']
    elif kind in ('shadowsocks','socks'):
        protocols=['tcp','udp']
    elif kind in ('vless','vmess','trojan','http'):
        import json
        stream=row.get('streamSettings') or {}
        if isinstance(stream,str):stream=json.loads(stream)
        network=stream.get('network','tcp')
        if network not in ('tcp','ws','grpc','httpupgrade','xhttp','kcp','quic'):
            raise ValueError('该传输尚不支持按端口限速')
        protocols=['udp'] if network in ('kcp','quic') else ['tcp']
    else:
        raise ValueError('该入站协议尚未适配带宽限制')
    return {'port':port,'protocols':protocols,'uploadKbps':upload_kbps,'downloadKbps':download_kbps}


def rules(policies):
    lines=['table inet mxioc_bandwidth {','chain upload { type filter hook input priority -5; policy accept;']
    for direction,chain in [('upload','upload'),('download','download')]:
        if chain=='download':lines+=['}', 'chain download { type filter hook output priority -5; policy accept;']
        for identity,item in sorted(policies.items(),key=lambda entry:int(entry[0])):
            if not str(identity).isdigit() or int(identity)<=0:raise ValueError('限速入站 ID 无效')
            checked=policy({'port':item['port'],'protocol':'shadowsocks'},item['uploadKbps'],item['downloadKbps'])
            protocols=item['protocols']
            if not protocols or any(p not in ('tcp','udp') for p in protocols):raise ValueError('限速协议无效')
            kbps=checked[direction+'Kbps']
            if not kbps:continue
            byte_rate=kbps*125
            burst=max(65536,byte_rate//5)
            match='dport' if direction=='upload' else 'sport'
            lines.append(f'meta l4proto {{ {", ".join(protocols)} }} th {match} {item["port"]} limit rate over {byte_rate} bytes/second burst {burst} bytes counter drop comment "mxioc-{identity}-{direction}"')
    return '\n'.join(lines+['}','}',''])


def apply_script(policies):
    encoded=base64.b64encode(rules(policies).encode()).decode()
    return r'''set -Eeuo pipefail
command -v nft >/dev/null || { echo 'Install nftables before setting bandwidth'; exit 12; }
command -v systemctl >/dev/null || { echo 'Bandwidth persistence requires systemd'; exit 12; }
mkdir -p /var/lock
exec 9>/var/lock/mxioc-bandwidth.lock
flock -n 9 || { echo 'Bandwidth operation busy'; exit 75; }
work=$(mktemp -d /tmp/mxioc-bandwidth.XXXXXXXX)
had_table=0
had_rules=0
had_unit=0
was_enabled=0
applied=0
persisted=0
unit=/etc/systemd/system/mxioc-bandwidth-restore.service
saved=/etc/mxioc-bandwidth/rules.nft
nft list table inet mxioc_bandwidth > "$work/previous.nft" 2>/dev/null && had_table=1
[ ! -f "$saved" ] || { cp -p "$saved" "$work/saved.nft"; had_rules=1; }
[ ! -f "$unit" ] || { cp -p "$unit" "$work/unit"; had_unit=1; }
systemctl is-enabled --quiet mxioc-bandwidth-restore.service && was_enabled=1
cleanup() {
  rc=$?
  if [ "$rc" != 0 ]; then
    if [ "$applied" = 1 ]; then
      { echo 'delete table inet mxioc_bandwidth'; [ "$had_table" = 0 ] || cat "$work/previous.nft"; } > "$work/rollback.nft"
      nft -f "$work/rollback.nft" || echo 'MXIOC_BANDWIDTH_ROLLBACK_FAILED'
    fi
    if [ "$persisted" = 1 ]; then
      if [ "$had_rules" = 1 ]; then cp -p "$work/saved.nft" "$saved"; else rm -f "$saved"; fi
      if [ "$had_unit" = 1 ]; then cp -p "$work/unit" "$unit"; else rm -f "$unit"; fi
      [ "$was_enabled" = 1 ] || systemctl disable mxioc-bandwidth-restore.service >/dev/null 2>&1 || true
      systemctl daemon-reload >/dev/null 2>&1 || true
    fi
  fi
  rm -rf -- "$work"
  exit "$rc"
}
trap cleanup EXIT
printf '%s' 'ENCODED_RULES' | base64 -d > "$work/new.nft"
{ [ "$had_table" = 0 ] || echo 'delete table inet mxioc_bandwidth'; cat "$work/new.nft"; } > "$work/transaction.nft"
nft -c -f "$work/transaction.nft"
nft -f "$work/transaction.nft"
applied=1
mkdir -p /etc/mxioc-bandwidth
persisted=1
install -m 600 "$work/new.nft" "$saved.tmp"
mv "$saved.tmp" "$saved"
cat > "$unit" <<'UNIT'
[Unit]
Description=Restore MXIOC dedicated inbound bandwidth limits
After=nftables.service network-pre.target
Before=xray.service sing-box.service x-ui.service
[Service]
Type=oneshot
RemainAfterExit=yes
ExecStart=/bin/sh -c 'nft list table inet mxioc_bandwidth >/dev/null 2>&1 || nft -f /etc/mxioc-bandwidth/rules.nft'
[Install]
WantedBy=multi-user.target
UNIT
systemctl daemon-reload
systemctl enable mxioc-bandwidth-restore.service
'''.replace('ENCODED_RULES',encoded)
