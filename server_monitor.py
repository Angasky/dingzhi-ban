"""Read-only Linux host sampling over the existing pinned SSH transport."""
import copy
import json
import threading
import time

# No package installation, external IP service, listening socket or persistent agent.
REMOTE_SAMPLE = r'''import json, os, platform, re, socket, subprocess, time
from pathlib import Path

def read(path):
    return Path(path).read_text(errors='replace')
cpus = {}
for line in read('/proc/stat').splitlines():
    fields = line.split()
    if fields and re.fullmatch(r'cpu\d*', fields[0]):
        ticks = [int(x) for x in fields[1:9]]
        cpus[fields[0]] = {'total': sum(ticks), 'idle': ticks[3] + ticks[4]}
mem = {}
for line in read('/proc/meminfo').splitlines():
    key, value = line.split(':', 1)
    mem[key] = int(value.split()[0]) * 1024
available = mem.get('MemAvailable', mem.get('MemFree', 0) + mem.get('Buffers', 0) + mem.get('Cached', 0))
interfaces = []
for line in read('/proc/net/dev').splitlines()[2:]:
    name, values = line.split(':', 1)
    name = name.strip()
    if name == 'lo': continue
    fields = values.split()
    interfaces.append({'name': name, 'rxBytes': int(fields[0]), 'txBytes': int(fields[8])})
defaults = set()
try:
    for line in read('/proc/net/route').splitlines()[1:]:
        fields = line.split()
        if fields[1] == '00000000': defaults.add(fields[0])
except (OSError, IndexError): pass
try:
    for line in read('/proc/net/ipv6_route').splitlines():
        fields = line.split()
        if fields[0] == '0' * 32 and fields[1] == '00': defaults.add(fields[-1])
except (OSError, IndexError): pass
if not defaults:
    defaults = {x['name'] for x in interfaces if not x['name'].startswith(('veth', 'docker', 'br-', 'tun', 'tap'))}
addresses = []
try:
    output = subprocess.run(['ip', '-j', 'address', 'show'], capture_output=True, text=True, timeout=3)
    if output.returncode == 0:
        for nic in json.loads(output.stdout):
            if nic.get('ifname') == 'lo': continue
            for address in nic.get('addr_info', []):
                if address.get('scope') == 'global':
                    addresses.append({'interface': nic['ifname'], 'address': address['local'],
                                      'family': address['family'], 'prefix': address.get('prefixlen')})
except (OSError, ValueError, subprocess.TimeoutExpired): pass
disks, seen = [], set()
for line in read('/proc/self/mounts').splitlines():
    fields = line.split()
    mount = fields[1].replace('\\040', ' ')
    if fields[2] in ('proc', 'sysfs', 'devtmpfs', 'tmpfs', 'cgroup', 'cgroup2', 'squashfs', 'nsfs') and mount != '/': continue
    if not os.path.isdir(mount): continue
    try:
        stat = os.statvfs(mount)
        device = os.stat(mount).st_dev
        if device in seen or not stat.f_blocks: continue
        seen.add(device)
        total, free, available_disk = stat.f_blocks * stat.f_frsize, stat.f_bfree * stat.f_frsize, stat.f_bavail * stat.f_frsize
        disks.append({'mount': mount, 'filesystem': fields[2], 'total': total, 'used': total-free,
                      'available': available_disk, 'percent': round((total-free)/total*100, 1)})
    except OSError: pass
    if len(disks) >= 32: break
cpu_model = next((line.split(':', 1)[1].strip() for line in read('/proc/cpuinfo').splitlines()
                  if line.startswith(('model name', 'Hardware'))), platform.machine())
os_name = platform.system()
try:
    match = re.search(r'^PRETTY_NAME=(.*)$', read('/etc/os-release'), re.M)
    if match: os_name = match.group(1).strip('"')
except OSError: pass
print(json.dumps({'sampledAt': time.time(), 'uptime': float(read('/proc/uptime').split()[0]),
    'bootId': read('/proc/sys/kernel/random/boot_id').strip(), 'hostname': socket.gethostname(),
    'os': os_name, 'kernel': platform.release(), 'cpuModel': cpu_model, 'cpuTicks': cpus,
    'cores': len(cpus)-1, 'load': list(os.getloadavg()),
    'memory': {'total': mem['MemTotal'], 'available': available, 'used': max(0, mem['MemTotal']-available),
               'swapTotal': mem.get('SwapTotal', 0), 'swapUsed': mem.get('SwapTotal', 0)-mem.get('SwapFree', 0)},
    'interfaces': interfaces, 'summaryInterfaces': sorted(defaults), 'addresses': addresses, 'disks': disks}))
'''


def derive_metrics(raw, previous=None):
    result = copy.deepcopy(raw)
    same_boot = previous is not None and previous.get('bootId') == raw.get('bootId')
    elapsed = raw['uptime'] - previous['uptime'] if same_boot else 0
    valid_interval = same_boot and 0 < elapsed <= 60
    result['cpuPercent'], result['perCpu'] = None, []
    for name, ticks in raw['cpuTicks'].items():
        old = previous.get('cpuTicks', {}).get(name) if valid_interval else None
        percent = None
        if old:
            total, idle = ticks['total']-old['total'], ticks['idle']-old['idle']
            if total > 0 and 0 <= idle <= total:
                percent = round((total-idle)/total*100, 1)
        if name == 'cpu': result['cpuPercent'] = percent
        else: result['perCpu'].append({'name': name, 'percent': percent})
    old_nics = {x['name']: x for x in previous.get('interfaces', [])} if valid_interval else {}
    for nic in result['interfaces']:
        old = old_nics.get(nic['name'])
        for counter, rate in (('rxBytes', 'rxBytesPerSecond'), ('txBytes', 'txBytesPerSecond')):
            delta = nic[counter]-old[counter] if old else -1
            nic[rate] = round(delta/elapsed, 1) if delta >= 0 else None
    selected = [n for n in result['interfaces'] if n['name'] in raw['summaryInterfaces']]
    result['network'] = {'interfaces': raw['summaryInterfaces']}
    for field in ('rxBytes', 'txBytes', 'rxBytesPerSecond', 'txBytesPerSecond'):
        values = [n[field] for n in selected]
        result['network'][field] = sum(values) if values and all(v is not None for v in values) else None
    memory = result['memory']
    memory['percent'] = round(memory['used']/memory['total']*100, 1) if memory['total'] else None
    result.update(warmingUp=not valid_interval, stale=False, error='')
    result.pop('cpuTicks', None)
    result.pop('bootId', None)
    return result


class HostMonitor:
    def __init__(self, ttl=5, clock=time.monotonic):
        self.ttl, self.clock = ttl, clock
        self.lock = threading.Lock()
        self.entries, self.inflight, self.failures = {}, {}, {}

    def sample(self, manager, identity):
        with manager.lock:
            record = copy.deepcopy(manager.record(identity))
        key = (identity, record['host'], record['sshPort'], record['sshUser'], record['fingerprint'], record.get('sshPassword'))
        with self.lock:
            cached = self.entries.get(key)
            if cached and self.clock()-cached['time'] < self.ttl:
                return copy.deepcopy(cached['view'])
            failure = self.failures.get(key)
            if failure and self.clock()-failure['time'] < self.ttl:
                if cached:
                    return {**copy.deepcopy(cached['view']), 'stale': True, 'error': failure['error']}
                raise ValueError(failure['error'])
            guard = self.inflight.setdefault(key, threading.Lock())
        if not guard.acquire(timeout=1):
            if cached:
                return {**copy.deepcopy(cached['view']), 'stale': True, 'error': '正在刷新，暂时显示上次采样'}
            raise ValueError('正在采集该服务器，请稍后重试')
        try:
            with self.lock:
                cached = self.entries.get(key)
                if cached and self.clock()-cached['time'] < self.ttl:
                    return copy.deepcopy(cached['view'])
                failure = self.failures.get(key)
                if failure and self.clock()-failure['time'] < self.ttl:
                    if cached:
                        return {**copy.deepcopy(cached['view']), 'stale': True, 'error': failure['error']}
                    raise ValueError(failure['error'])
            ssh = manager.connect(record)
            try:
                code, output = manager.execute(ssh, record, "python3 - <<'MXIOC_METRICS'\n"+REMOTE_SAMPLE+"\nMXIOC_METRICS", timeout=15, max_output=512*1024)
                if code:
                    raise ValueError('硬件采样失败：需要 Linux /proc 和 Python 3，请检查 SSH 权限')
                raw = json.loads(output)
            finally:
                ssh.close()
            view = derive_metrics(raw, cached['raw'] if cached else None)
            view.update(serverId=identity, name=record['name'], managementIp=record['host'])
            with self.lock:
                self.entries[key] = {'raw': raw, 'view': view, 'time': self.clock()}
                self.failures.pop(key, None)
                # Keep at most 128 inactive host histories; never evict a locked sampler.
                if len(self.entries) > 128:
                    for old in list(self.entries):
                        if old != key and not self.inflight[old].locked():
                            self.entries.pop(old, None)
                            if len(self.entries) <= 128: break
            return copy.deepcopy(view)
        except Exception as exc:
            with self.lock:
                self.failures[key] = {'time': self.clock(), 'error': '采样失败：'+str(exc)[:200]}
            if cached:
                return {**copy.deepcopy(cached['view']), 'stale': True, 'error': '采样失败：'+str(exc)[:200]}
            raise ValueError('无法采集服务器监控：'+str(exc)[:200]) from exc
        finally:
            guard.release()
