"""SSH provisioning and authenticated 3x-ui control. No browser-held SSH secrets."""

import base64
import copy
import hashlib
import http.client
from http.cookies import SimpleCookie
import ipaddress
import json
import os
import re
import secrets
import shlex
import socket
import ssl
import threading
import time
import uuid
from pathlib import Path
from urllib.parse import urlencode

INSTALL_URL = "https://raw.githubusercontent.com/Teminuosi/3x-ui/main/install.sh"
MAX_LOG = 64 * 1024
INBOUND_FIELDS = {"up", "down", "total", "remark", "enable", "expiryTime", "listen",
                  "port", "protocol", "settings", "streamSettings", "sniffing", "allocate"}


def libraries():
    try:
        import paramiko
        from cryptography.fernet import Fernet
        return paramiko, Fernet
    except ImportError as exc:
        raise ValueError("服务器管理需要 Python 3.9+ 和 Paramiko，请先运行 vpn --update-only 安装依赖") from exc


def valid_ip(value):
    try:
        address = ipaddress.ip_address(str(value).strip().strip("[]"))
    except ValueError as exc:
        raise ValueError("请输入服务器 IPv4 或 IPv6 地址") from exc
    if address.is_loopback or address.is_link_local or address.is_multicast or address.is_unspecified:
        raise ValueError("服务器地址不能是回环、链路本地、多播或未指定地址")
    return str(address)


def valid_port(value):
    if isinstance(value, bool) or not str(value).isdigit() or not 1 <= int(value) <= 65535:
        raise ValueError("端口必须是 1–65535 的整数")
    return int(value)


def valid_domain(value):
    value = str(value or "").strip().lower().rstrip(".")
    if not value:
        return ""
    try:
        value = value.encode("idna").decode("ascii")
    except UnicodeError as exc:
        raise ValueError("绑定域名格式无效") from exc
    if len(value) > 253 or "." not in value or any(
        not re.fullmatch(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?", label) for label in value.split(".")
    ):
        raise ValueError("域名只填写主机名，例如 panel.example.com，不包含协议、端口或路径")
    return value


def text(value, name, minimum=1, maximum=256):
    value = str(value or "")
    if not minimum <= len(value) <= maximum or any(c in value for c in "\r\n\0"):
        raise ValueError(f"{name}需要 {minimum}–{maximum} 个字符，且不能包含换行")
    return value


def fingerprint(key):
    return "SHA256:" + base64.b64encode(hashlib.sha256(key.asbytes()).digest()).decode().rstrip("=")


def clean_log(value, secret_values=()):
    value = re.sub(r"\x1b\[[0-?]*[ -/]*[@-~]", "", str(value)).replace("\r", "\n")
    for secret in sorted((str(x) for x in secret_values if x), key=len, reverse=True):
        value = value.replace(secret, "[已隐藏]")
    value = re.sub(r"(?im)^.*?(?:password|passwd|api\s*token|private.?key|secret)[\"'\s]*[:=].*$",
                   "[凭证信息已隐藏]", value)
    return value


def installation_error(log, stage, code):
    lines = [line.strip() for line in log.splitlines() if line.strip() and not line.startswith("MXIOC_")]
    errors = [line for line in lines if re.search(r"error|failed|fatal|curl:|失败|拒绝|无法|NXDOMAIN|timed out|not found", line, re.I)]
    detail = "；".join((errors or lines)[-3:])[-700:]
    return f"安装在 {stage} 阶段失败（退出码 {code}）" + ("：" + detail if detail else "，请查看安装日志")


def install_script(record, password):
    env = "XUI_DOMAIN=" + shlex.quote(record["domain"]) if record["domain"] else "XUI_AUTO=1"
    return f"""set -Eeuo pipefail
stage=preflight
printf 'MXIOC_STAGE=preflight\\n'
trap 'rc=$?; printf "\\nMXIOC_FAILURE_STAGE=%s EXIT=%s\\n" "$stage" "$rc"; exit "$rc"' ERR
test "$(id -u)" = 0
command -v bash >/dev/null
if ! command -v curl >/dev/null || ! command -v flock >/dev/null; then
  if command -v apt-get >/dev/null; then apt-get update && apt-get install -y curl util-linux
  elif command -v dnf >/dev/null; then dnf install -y curl util-linux
  elif command -v yum >/dev/null; then yum install -y curl util-linux
  elif command -v apk >/dev/null; then apk add --no-cache curl util-linux
  else echo 'Missing curl/flock and no supported package manager'; exit 12; fi
fi
mkdir -p /var/lock
exec 9>/var/lock/mxioc-xui-install.lock
flock -n 9 || {{ echo 'Another MXIOC 3x-ui installation is still running'; exit 75; }}
stage=download
printf 'MXIOC_STAGE=download\\n'
installer=$(mktemp /tmp/mxioc-xui.XXXXXXXX)
chmod 700 "$installer"
trap 'rm -f -- "$installer"' EXIT
curl -fLsS --retry 2 --connect-timeout 20 --max-time 180 {shlex.quote(INSTALL_URL)} -o "$installer"
test -s "$installer"
stage=install
printf 'MXIOC_STAGE=install\\n'
{env} bash "$installer" </dev/null
stage=credentials
printf 'MXIOC_STAGE=credentials\\n'
cd /usr/local/x-ui
./x-ui setting -username {shlex.quote(record['panelUser'])} -password {shlex.quote(password)} >/dev/null
stage=service
printf 'MXIOC_STAGE=service\\n'
if command -v systemctl >/dev/null; then
  systemctl restart x-ui
  for attempt in $(seq 1 20); do systemctl is-active --quiet x-ui && break; sleep 1; done
  systemctl is-active --quiet x-ui
else
  rc-service x-ui restart
  rc-service x-ui status
fi
printf 'MXIOC_STAGE=installed\\n'
"""


def parse_panel_settings(output, record):
    port = re.search(r"(?m)^port:[ \t]*(\d+)[ \t]*$", output)
    path = re.search(r"(?m)^webBasePath:[ \t]*(\S+)[ \t]*$", output)
    if not port or not path:
        raise ValueError("无法读取 3x-ui 面板端口或访问路径，请查看诊断日志")
    port = valid_port(port.group(1))
    segment = path.group(1).strip("/")
    base = "/" + segment + "/" if segment else "/"
    if not re.fullmatch(r"/(?:[A-Za-z0-9_.~-]+/)*", base) or ".." in base:
        raise ValueError("面板访问路径格式无效")
    cert = re.search(r"(?m)^cert:[ \t]*(\S+)[ \t]*$", output)
    secure = bool(cert and cert.group(1) not in ("<nil>", "null"))
    host = record["domain"] if secure and record.get("domain") else record["host"]
    authority = f"[{host}]" if ":" in host else host
    return {"panelPort": port, "panelPath": base, "panelTLS": secure,
            "panelUrl": f"{'https' if secure else 'http'}://{authority}:{port}{base}"}


class PanelAPI:
    """HTTP runs inside a host-key-verified SSH channel to remote loopback only."""

    def __init__(self, ssh, record, token, password=""):
        self.ssh, self.record, self.token = ssh, record, token
        self.cookies, self.csrf = {}, ""
        self.password, self.logged_in = password, False

    def request(self, path, payload=None, *, method=None, bearer=True):
        # Xray configuration routes are session-only, unlike /panel/api.
        if path.lstrip("/").startswith("panel/xray/"):
            if not self.logged_in:
                self.request("login", {"username": self.record["panelUser"],
                                       "password": self.password}, bearer=False)
            bearer = False
        if not bearer and payload is not None and not self.csrf:
            self.request("csrf-token", bearer=False)
            if not self.csrf:
                raise ValueError("3x-ui 未返回登录 CSRF 令牌，暂不能验证预设账号")
        channel = self.ssh.get_transport().open_channel(
            "direct-tcpip", ("127.0.0.1", self.record["panelPort"]), ("127.0.0.1", 0), timeout=20)
        client, bridge = socket.socketpair()
        client.settimeout(30)

        def forward(source, destination):
            try:
                while True:
                    chunk = source.recv(32768)
                    if not chunk:
                        break
                    destination.sendall(chunk)
            except (OSError, EOFError):
                pass
            finally:
                try:
                    destination.shutdown(socket.SHUT_WR)
                except (OSError, AttributeError):
                    pass

        threads = [threading.Thread(target=forward, args=(bridge, channel), daemon=True),
                   threading.Thread(target=forward, args=(channel, bridge), daemon=True)]
        for thread in threads:
            thread.start()
        connection = http.client.HTTPConnection("127.0.0.1", self.record["panelPort"], timeout=30)
        try:
            if self.record.get("panelTLS"):
                # Peer authenticity comes from pinned SSH. TLS is only on the
                # authenticated remote loopback hop, never on an arbitrary URL.
                context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
                context.check_hostname = False
                context.verify_mode = ssl.CERT_NONE
                client = context.wrap_socket(client, server_hostname=self.record.get("domain") or self.record["host"])
            connection.sock = client
            headers = {"X-Requested-With": "XMLHttpRequest", "Connection": "close"}
            hostname = self.record.get("domain") or self.record["host"]
            headers["Host"] = (f"[{hostname}]" if ":" in hostname else hostname) + ":" + str(self.record["panelPort"])
            if self.cookies:
                headers["Cookie"] = "; ".join(f"{key}={value}" for key, value in self.cookies.items())
            if self.csrf and not bearer:
                headers["X-CSRF-Token"] = self.csrf
            if bearer:
                headers["Authorization"] = "Bearer " + self.token
            body = None
            if payload is not None:
                body = urlencode({key: json.dumps(value, ensure_ascii=False) if isinstance(value, (dict, list))
                                  else str(value).lower() if isinstance(value, bool) else value
                                  for key, value in payload.items()}).encode()
                headers["Content-Type"] = "application/x-www-form-urlencoded"
            connection.request(method or ("POST" if payload is not None else "GET"),
                               self.record["panelPath"].rstrip("/") + "/" + path.lstrip("/"), body, headers)
            response = connection.getresponse()
            for header in response.headers.get_all("Set-Cookie", []):
                cookie = SimpleCookie()
                cookie.load(header)
                self.cookies.update({key: morsel.value for key, morsel in cookie.items()})
            raw = response.read(4 * 1024 * 1024 + 1)
            if len(raw) > 4 * 1024 * 1024:
                raise ValueError("3x-ui 返回的数据超过 4 MB，请减少单面板入站数量")
            if response.status != 200:
                raise ValueError(f"3x-ui 接口 HTTP {response.status}，请检查面板状态、访问路径和 API 令牌")
            try:
                result = json.loads(raw)
            except ValueError as exc:
                raise ValueError("3x-ui 返回非 JSON 内容，请确认面板版本和访问路径") from exc
            if not isinstance(result, dict) or result.get("success") is not True:
                message = str(result.get("msg", "接口操作未成功") if isinstance(result, dict) else "响应无效")
                raise ValueError("3x-ui：" + clean_log(message, [self.password, self.token, self.csrf, *self.cookies.values()])[:500])
            value = result.get("obj")
            if path == "csrf-token" and isinstance(value, str) and len(value) <= 256:
                self.csrf = value
            if path == "login":
                self.logged_in = True
            return value
        finally:
            connection.close()
            client.close()
            channel.close()
            bridge.close()


class RemoteManager:
    def __init__(self, root, parse_link, node_uri):
        _, Fernet = libraries()
        self.root = Path(root) / "remote"
        self.root.mkdir(parents=True, exist_ok=True)
        os.chmod(self.root, 0o700)
        self.path = self.root / "servers.json"
        key = self.root / "credentials.key"
        try:
            descriptor = os.open(key, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        except FileExistsError:
            pass
        else:
            with os.fdopen(descriptor, "wb") as stream:
                stream.write(Fernet.generate_key())
        self.cipher = Fernet(key.read_bytes())
        self.lock = threading.RLock()
        self.server_locks = {}
        self.parse_link, self.node_uri = parse_link, node_uri
        self.data = json.loads(self.path.read_text(encoding="utf-8")) if self.path.exists() else {"servers": [], "jobs": []}
        for job in self.data["jobs"]:
            if job["status"] == "running":
                job.update(status="interrupted", error="总面板已重启。远端安装进程可能仍在运行，请先诊断后再重试。", finishedAt=int(time.time()))
        self.flush()

    def flush(self):
        temporary = self.path.with_name(".servers-" + secrets.token_hex(6))
        try:
            descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
                json.dump(self.data, stream, ensure_ascii=False)
            os.replace(temporary, self.path)
        finally:
            temporary.unlink(missing_ok=True)

    def seal(self, value):
        return self.cipher.encrypt(str(value).encode()).decode()

    def secret(self, record, key):
        try:
            return self.cipher.decrypt(record[key].encode()).decode()
        except Exception as exc:
            raise ValueError("服务器凭证解密失败，请检查 credentials.key 或重新填写凭证") from exc

    def record(self, server_id):
        item = next((x for x in self.data["servers"] if x["id"] == str(server_id)), None)
        if not item:
            raise ValueError("服务器不存在")
        return item

    def public(self, record):
        keys = ("id", "name", "host", "sshPort", "sshUser", "fingerprint", "domain", "panelUser",
                "panelUrl", "panelPort", "panelPath", "panelTLS", "createdAt", "lastError")
        result = {key: record.get(key) for key in keys}
        jobs = [x for x in self.data["jobs"] if x["serverId"] == record["id"]]
        if jobs:
            result["job"] = {k: jobs[-1].get(k) for k in ("id", "status", "stage", "error", "startedAt", "finishedAt")}
        result["hasSSHPassword"] = bool(record.get("sshPassword"))
        result["hasPanelPassword"] = bool(record.get("panelPassword"))
        return result

    def list(self):
        with self.lock:
            return [self.public(record) for record in self.data["servers"]]

    def save(self, payload):
        with self.lock:
            existing = self.record(payload["id"]) if payload.get("id") else None
            if existing and self.server_locks.get(existing["id"], threading.Lock()).locked():
                raise ValueError("该服务器正在处理节点操作，请稍后修改连接信息")
            if existing and any(j["serverId"] == existing["id"] and j["status"] == "running" for j in self.data["jobs"]):
                raise ValueError("安装过程中不能修改服务器连接信息")
            record = copy.deepcopy(existing) if existing else {"id": secrets.token_hex(12), "createdAt": int(time.time())}
            for key, value in {"host": valid_ip(payload.get("host")), "sshPort": valid_port(payload.get("sshPort", 22)),
                               "name": text(payload.get("name"), "服务器名称", maximum=80),
                               "sshUser": text(payload.get("sshUser"), "SSH 用户名", maximum=64),
                               "domain": valid_domain(payload.get("domain")),
                               "panelUser": text(payload.get("panelUser"), "面板用户名", 3, 64)}.items():
                record[key] = value
            pin = str(payload.get("fingerprint", ""))
            if not re.fullmatch(r"SHA256:[A-Za-z0-9+/]{43}", pin):
                raise ValueError("请先检测并核对 SSH 主机指纹")
            record["fingerprint"] = pin
            if existing and (existing["host"], existing["sshPort"]) != (record["host"], record["sshPort"]):
                for key in ("panelUrl", "panelPort", "panelPath", "panelTLS", "managed", "lastXrayBackup"):
                    record.pop(key, None)
            if any(item["id"] != record["id"] and (item["host"], item["sshPort"]) == (record["host"], record["sshPort"])
                   for item in self.data["servers"]):
                raise ValueError("该 IP 与 SSH 端口已登记，请编辑现有服务器")
            for key, label, minimum in (("sshPassword", "SSH 密码", 1), ("panelPassword", "面板密码", 12)):
                if payload.get(key):
                    record[key] = self.seal(text(payload[key], label, minimum))
                elif not record.get(key):
                    raise ValueError(f"请填写{label}")
            if existing:
                existing.clear()
                existing.update(record)
            else:
                self.data["servers"].append(record)
            self.flush()
            return self.public(record)

    def remove(self, server_id):
        with self.lock:
            self.record(server_id)
            if self.server_locks.get(server_id, threading.Lock()).locked():
                raise ValueError("该服务器正在处理节点操作，请稍后移除")
            if any(j["serverId"] == server_id and j["status"] == "running" for j in self.data["jobs"]):
                raise ValueError("请等待安装任务结束再移除管理")
            self.data["servers"] = [x for x in self.data["servers"] if x["id"] != server_id]
            self.data["jobs"] = [x for x in self.data["jobs"] if x["serverId"] != server_id]
            self.flush()

    def probe(self, payload):
        paramiko, _ = libraries()
        connection = socket.create_connection((valid_ip(payload.get("host")), valid_port(payload.get("sshPort", 22))), timeout=10)
        transport = paramiko.Transport(connection)
        try:
            transport.start_client(timeout=10)
            return {"fingerprint": fingerprint(transport.get_remote_server_key())}
        finally:
            transport.close()
            connection.close()

    def connect(self, record):
        paramiko, _ = libraries()

        class PinnedKey(paramiko.MissingHostKeyPolicy):
            def missing_host_key(self, client, hostname, key):
                if not secrets.compare_digest(fingerprint(key), record["fingerprint"]):
                    raise ValueError("SSH 主机指纹发生变化，已停止连接；请核对服务器后重新检测指纹")

        ssh = paramiko.SSHClient()
        ssh.set_missing_host_key_policy(PinnedKey())
        try:
            ssh.connect(record["host"], port=record["sshPort"], username=record["sshUser"],
                        password=self.secret(record, "sshPassword"), look_for_keys=False, allow_agent=False,
                        timeout=12, auth_timeout=15, banner_timeout=15)
            ssh.get_transport().set_keepalive(25)
            return ssh
        except paramiko.AuthenticationException as exc:
            ssh.close()
            raise ValueError("SSH 认证失败，请检查账号、密码以及服务器是否允许密码登录") from exc
        except Exception:
            ssh.close()
            raise

    def execute(self, ssh, record, script, *, timeout=60, on_line=None):
        sudo = record["sshUser"] != "root"
        stdin, stdout, _ = ssh.exec_command("sudo -S -p '' bash -s" if sudo else "bash -s", timeout=20)
        channel = stdout.channel
        channel.set_combine_stderr(True)
        write_error = None
        try:
            stdin.write((self.secret(record, "sshPassword") + "\n" if sudo else "") + script + "\n")
            stdin.flush()
            channel.shutdown_write()
        except (OSError, EOFError) as exc:
            write_error = exc
        deadline, pending, output = time.monotonic() + timeout, b"", bytearray()
        try:
            while True:
                if time.monotonic() > deadline:
                    raise ValueError("远端命令超时；安装可能仍在运行，请先读取诊断信息后再重试")
                if channel.recv_ready():
                    chunk = channel.recv(8192)
                    output.extend(chunk)
                    if len(output) > MAX_LOG * 2:
                        del output[:-MAX_LOG * 2]
                    pending += chunk
                    while b"\n" in pending:
                        line, pending = pending.split(b"\n", 1)
                        if on_line:
                            on_line(line.decode("utf-8", errors="replace") + "\n")
                    if len(pending) > MAX_LOG:
                        raise ValueError("远端输出单行异常过长，已停止读取")
                elif channel.exit_status_ready():
                    break
                else:
                    time.sleep(.08)
            if pending and on_line:
                on_line(pending.decode("utf-8", errors="replace"))
            code = channel.recv_exit_status()
            if code == 0 and write_error is not None:
                raise ValueError("SSH 在发送命令时断开，请检查远端 Shell 和 sudo 状态") from write_error
            return code, output.decode("utf-8", errors="replace")
        finally:
            channel.close()
            stdin.close()
            stdout.close()

    def diagnostics(self, record, ssh=None):
        own = ssh is None
        ssh = ssh or self.connect(record)
        try:
            _, output = self.execute(ssh, record, """set +e
echo '=== Service ==='
if command -v systemctl >/dev/null; then systemctl status x-ui --no-pager -l; journalctl -u x-ui -n 45 --no-pager; else rc-service x-ui status; fi
echo '=== Listeners ==='
ss -lntup 2>/dev/null | head -35
echo '=== DNS ==='
""" + ("getent ahosts " + shlex.quote(record["domain"]) if record.get("domain") else "true"), timeout=40)
            return clean_log(output, [self.secret(record, "sshPassword"), self.secret(record, "panelPassword")])[-MAX_LOG:]
        finally:
            if own:
                ssh.close()

    def panel(self, ssh, record):
        code, output = self.execute(ssh, record, "cd /usr/local/x-ui && ./x-ui setting -show true && ./x-ui setting -getCert true", timeout=30)
        if code:
            raise ValueError("尚未找到可用的 3x-ui 设置，请先安装或读取诊断")
        settings = parse_panel_settings(output, record)
        with self.lock:
            self.record(record["id"]).update(settings)
            self.flush()
        record.update(settings)
        code, output = self.execute(ssh, record, "cd /usr/local/x-ui && ./x-ui setting -getApiToken true", timeout=30)
        token = re.search(r"(?m)^apiToken:[ \t]*(\S+)", output)
        if code or not token:
            raise ValueError("无法读取 3x-ui API 令牌，请确认使用指定的 Teminuosi/3x-ui 分支")
        return PanelAPI(ssh, record, token.group(1), self.secret(record, "panelPassword"))

    def test(self, server_id):
        record = copy.deepcopy(self.record(server_id))
        ssh = self.connect(record)
        try:
            code, _ = self.execute(ssh, record, "test $(id -u) = 0", timeout=20)
            if code:
                raise ValueError("SSH 已连接，但账号没有 root/sudo 执行权限")
            return {"connected": True, "message": "SSH 和提权权限检查通过"}
        finally:
            ssh.close()

    def job(self, job_id):
        with self.lock:
            item = next((j for j in self.data["jobs"] if j["id"] == job_id), None)
            if not item:
                raise ValueError("安装任务不存在")
            return copy.deepcopy(item)

    def start_install(self, server_id, confirmed):
        if confirmed is not True:
            raise ValueError("请确认在该服务器安装/更新 3x-ui 并设置管理员凭证")
        with self.lock:
            record = copy.deepcopy(self.record(server_id))
            self.secret(record, "sshPassword")
            self.secret(record, "panelPassword")
            if self.server_locks.get(server_id, threading.Lock()).locked():
                raise ValueError("该服务器正在处理节点操作，请稍后安装")
            running = [j for j in self.data["jobs"] if j["status"] == "running"]
            if any(j["serverId"] == server_id for j in running):
                raise ValueError("该服务器已有安装任务正在运行")
            if len(running) >= 2:
                raise ValueError("已有两个安装任务运行，请等待其中一个结束")
            job = {"id": secrets.token_hex(12), "serverId": server_id, "status": "running",
                   "stage": "connecting", "startedAt": int(time.time()), "log": "", "error": ""}
            self.data["jobs"] = [j for j in self.data["jobs"] if j["status"] == "running"] + [
                j for j in self.data["jobs"] if j["status"] != "running"][-47:]
            self.data["jobs"].append(job)
            self.flush()
            threading.Thread(target=self.install_worker, args=(record, job), daemon=True).start()
            return copy.deepcopy(job)

    def install_worker(self, record, job):
        ssh, last_flush = None, [0]
        secret_values = [self.secret(record, "sshPassword"), self.secret(record, "panelPassword")]

        def log(line):
            with self.lock:
                match = re.search(r"MXIOC_STAGE=(\w+)", line)
                if match:
                    job["stage"] = match.group(1)
                job["log"] = (job["log"] + clean_log(line, secret_values))[-MAX_LOG:]
                if time.monotonic() - last_flush[0] > 1:
                    self.flush()
                    last_flush[0] = time.monotonic()

        try:
            ssh = self.connect(record)
            log("SSH 已连接，开始安装。\n")
            code, _ = self.execute(ssh, record, install_script(record, secret_values[1]), timeout=2700, on_line=log)
            job["exitCode"] = code
            if code:
                raise ValueError(installation_error(job["log"], job["stage"], code))
            job["stage"] = "api-check"
            for attempt in range(15):
                try:
                    api = self.panel(ssh, record)
                    api.request("login", {"username": record["panelUser"], "password": secret_values[1]}, bearer=False)
                    api.request("panel/api/inbounds/list")
                    break
                except (OSError, EOFError, http.client.HTTPException):
                    if attempt == 14:
                        raise
                    time.sleep(2)
            if record.get("domain") and not record.get("panelTLS"):
                raise ValueError("面板已安装，但域名 HTTPS 证书未配置成功；请检查域名解析、80 端口和证书日志")
            with self.lock:
                job.update(status="succeeded", stage="complete", panelUrl=record["panelUrl"])
                self.record(record["id"])["lastError"] = ""
            log("安装、预设凭证登录和 API 验证均已通过。\n")
        except Exception as exc:
            message = clean_log(str(exc), secret_values)
            with self.lock:
                job.update(stage="diagnostics", error=message)
                self.record(record["id"])["lastError"] = message
                if record.get("panelUrl"):
                    job["panelUrl"] = record["panelUrl"]
            log("\n失败原因：" + message + "\n")
            if ssh:
                if not record.get("panelUrl"):
                    try:
                        self.panel(ssh, record)
                    except Exception:
                        pass
                    if record.get("panelUrl"):
                        job["panelUrl"] = record["panelUrl"]
                try:
                    log("\n=== 自动诊断 ===\n" + self.diagnostics(record, ssh))
                except Exception as diagnostic_error:
                    log("诊断读取失败：" + str(diagnostic_error) + "\n")
        finally:
            if ssh:
                ssh.close()
            with self.lock:
                if job["status"] == "running":
                    job["status"] = "failed"
                job["finishedAt"] = int(time.time())
                self.flush()

    def with_panel(self, server_id, action):
        with self.lock:
            record = copy.deepcopy(self.record(server_id))
            if any(j["serverId"] == server_id and j["status"] == "running" for j in self.data["jobs"]):
                raise ValueError("请等待安装任务完成后再操作节点")
            operation_lock = self.server_locks.setdefault(server_id, threading.Lock())
        if not operation_lock.acquire(blocking=False):
            raise ValueError("该服务器正在处理其他操作，请稍后重试")
        ssh = None
        try:
            ssh = self.connect(record)
            return action(self.panel(ssh, record), record)
        except Exception as exc:
            raise ValueError(clean_log(str(exc), [self.secret(record, "sshPassword"), self.secret(record, "panelPassword")])) from exc
        finally:
            if ssh:
                ssh.close()
            operation_lock.release()

    def inbounds(self, server_id):
        def action(api, record):
            rows = api.request("panel/api/inbounds/list") or []
            if not isinstance(rows, list):
                raise ValueError("3x-ui 入站列表格式无效")
            for row in rows:
                if not isinstance(row, dict):
                    raise ValueError("3x-ui 入站记录格式无效")
                row["id"] = positive_id(row.get("id"))
                row["port"] = valid_port(row.get("port"))
                for field in ("up", "down"):
                    if type(row.get(field, 0)) is not int or row.get(field, 0) < 0:
                        raise ValueError("3x-ui 流量字段无效")
                row["shareLinks"] = []
                try:
                    row["shareLinks"] = [self.node_uri(n) for n in inbound_nodes(row, record)]
                except (ValueError, KeyError, TypeError):
                    pass
            return {"inbounds": rows, "server": self.public(self.record(server_id))}
        return self.with_panel(server_id, action)

    def create_inbound(self, server_id, payload):
        def action(api, record):
            body = prepare_inbound(payload["inbound"]) if "inbound" in payload else build_inbound(api, payload)
            created = api.request("panel/api/inbounds/add", body)
            if not isinstance(created, dict) or not created.get("id"):
                raise ValueError("3x-ui 返回的新增入站结果缺少 ID，请刷新列表核对，勿重复提交")
            created["id"] = positive_id(created["id"])
            result = {"inbound": created, "shareLinks": []}
            try:
                result["shareLinks"] = [self.node_uri(n) for n in inbound_nodes(created, record)]
            except (ValueError, KeyError, TypeError):
                pass
            return result
        return self.with_panel(server_id, action)

    def update_inbound(self, server_id, inbound_id, payload):
        inbound_id = positive_id(inbound_id)
        body = prepare_inbound(payload)

        def action(api, record):
            if any(x["inboundId"] == inbound_id for x in record.get("relays", [])):
                old = api.request(f"panel/api/inbounds/get/{inbound_id}")
                if any(body.get(key) != old.get(key) for key in ("port", "listen", "protocol")):
                    raise ValueError("中转入站的端口、监听地址和协议关联路由，请保持不变；如需修改请重新创建中转")
            return api.request(f"panel/api/inbounds/update/{inbound_id}", body)
        return self.with_panel(server_id, action)

    def delete_inbound(self, server_id, inbound_id):
        inbound_id = positive_id(inbound_id)

        def action(api, record):
            managed = next((x for x in record.get("relays", []) if x["inboundId"] == inbound_id), None)
            if not managed:
                api.request(f"panel/api/inbounds/del/{inbound_id}", {})
                return {"deleted": True}
            original, wrapper = read_xray(api)
            changed = copy.deepcopy(original)
            changed["outbounds"] = [x for x in changed.get("outbounds", []) if x.get("tag") != managed["outboundTag"]]
            changed.setdefault("routing", {})["rules"] = [x for x in changed.get("routing", {}).get("rules", [])
                                                           if x.get("outboundTag") != managed["outboundTag"]]
            self.backup_xray(server_id, original)
            try:
                write_xray(api, changed, wrapper)
                api.request(f"panel/api/inbounds/del/{inbound_id}", {})
            except Exception:
                write_xray(api, original, wrapper)
                raise
            with self.lock:
                self.record(server_id)["relays"] = [x for x in record.get("relays", []) if x["inboundId"] != inbound_id]
                self.flush()
            return {"deleted": True}
        return self.with_panel(server_id, action)

    def backup_xray(self, server_id, configuration):
        with self.lock:
            self.record(server_id)["lastXrayBackup"] = self.seal(json.dumps(configuration))
            self.flush()

    def create_relay(self, server_id, payload):
        landing = self.parse_link(text(payload.get("landingLink"), "落地分享链接", maximum=12000))
        outbound = node_outbound(landing, "mxioc-relay-" + secrets.token_hex(8))

        def action(api, record):
            original, wrapper = read_xray(api)
            self.backup_xray(server_id, original)
            body = build_inbound(api, {**payload, "template": "vless-reality"})
            created = api.request("panel/api/inbounds/add", body)
            if not isinstance(created, dict) or not created.get("id") or not created.get("tag"):
                raise ValueError("中转入口返回的 ID/tag 不完整，请到入站列表核对")
            created["id"] = positive_id(created["id"])
            changed = copy.deepcopy(original)
            changed.setdefault("outbounds", []).append(outbound)
            changed.setdefault("routing", {}).setdefault("rules", []).insert(0, {
                "type": "field", "inboundTag": [created["tag"]], "outboundTag": outbound["tag"]})
            try:
                write_xray(api, changed, wrapper)
            except Exception as exc:
                failures = []
                for label, revert in (("恢复 Xray 配置", lambda: write_xray(api, original, wrapper)),
                                      ("删除本次入口", lambda: api.request(f"panel/api/inbounds/del/{created['id']}", {}))):
                    try:
                        revert()
                    except Exception:
                        failures.append(label)
                suffix = "；已撤回本次创建" if not failures else "；部分撤回失败：" + "、".join(failures)
                raise ValueError(str(exc) + suffix) from exc
            with self.lock:
                self.record(server_id).setdefault("relays", []).append({"inboundId": created["id"], "outboundTag": outbound["tag"]})
                self.flush()
            return {"inbound": created, "shareLinks": [self.node_uri(n) for n in inbound_nodes(created, record)]}
        return self.with_panel(server_id, action)


def positive_id(value):
    if isinstance(value, bool) or not str(value).isdigit() or int(value) <= 0:
        raise ValueError("入站 ID 无效")
    return int(value)


def json_object(value):
    result = json.loads(value) if isinstance(value, str) else copy.deepcopy(value)
    if not isinstance(result, dict):
        raise ValueError("配置必须是 JSON 对象")
    return result


def prepare_inbound(payload):
    if not isinstance(payload, dict):
        raise ValueError("入站配置必须是 JSON 对象")
    result = {key: value for key, value in payload.items() if key in INBOUND_FIELDS}
    result["port"] = valid_port(result.get("port"))
    if result.get("protocol") not in {"vless", "vmess", "trojan", "shadowsocks", "socks", "http", "dokodemo-door", "mixed", "tunnel"}:
        raise ValueError("不支持该 Xray 入站协议")
    for key in ("settings", "streamSettings", "sniffing"):
        if key in result:
            result[key] = json.dumps(json_object(result[key]), ensure_ascii=False)
    if len(json.dumps(result)) > 256 * 1024:
        raise ValueError("单条入站配置不能超过 256 KB")
    return result


def build_inbound(api, payload):
    port = valid_port(payload.get("port"))
    remark = text(payload.get("remark", "新建节点"), "节点名称", maximum=100)
    template = payload.get("template", "vless-reality")
    client = {"id": str(uuid.uuid4()), "email": "mxioc-" + secrets.token_hex(6), "enable": True,
              "totalGB": 0, "expiryTime": 0, "limitIp": 0, "subId": secrets.token_hex(8), "reset": 0, "tgId": ""}
    stream = {"network": "tcp", "security": "none", "tcpSettings": {"header": {"type": "none"}}}
    if template == "vless-reality":
        sni = valid_domain(payload.get("serverName", "www.microsoft.com"))
        if not sni:
            raise ValueError("Reality 需要填写伪装域名")
        keys = api.request("panel/api/server/getNewX25519Cert")
        if not isinstance(keys, dict) or not keys.get("privateKey") or not keys.get("publicKey"):
            raise ValueError("3x-ui 未返回可用的 Reality 密钥")
        client["flow"] = "xtls-rprx-vision"
        protocol, settings = "vless", {"clients": [client], "decryption": "none", "fallbacks": []}
        stream.update(security="reality", realitySettings={"show": False, "xver": 0, "target": sni + ":443",
                      "serverNames": [sni], "privateKey": keys["privateKey"], "shortIds": [secrets.token_hex(8)],
                      "settings": {"publicKey": keys["publicKey"], "fingerprint": "chrome", "serverName": sni, "spiderX": "/"}})
    elif template == "vmess-tcp":
        protocol, settings = "vmess", {"clients": [client], "disableInsecureEncryption": False}
    elif template == "shadowsocks":
        protocol, settings = "shadowsocks", {"method": "aes-128-gcm", "password": secrets.token_urlsafe(24), "network": "tcp,udp"}
    else:
        raise ValueError("未知节点模板")
    return prepare_inbound({"remark": remark, "port": port, "protocol": protocol, "enable": True,
                            "listen": "", "up": 0, "down": 0, "total": 0, "expiryTime": 0,
                            "settings": settings, "streamSettings": stream,
                            "sniffing": {"enabled": True, "destOverride": ["http", "tls", "quic"]}})


def inbound_nodes(inbound, record):
    settings = json_object(inbound.get("settings", {}))
    stream = json_object(inbound.get("streamSettings", {}))
    protocol = inbound.get("protocol")
    base = {"name": inbound.get("remark") or "3x-ui 节点", "server": record["host"], "port": inbound["port"]}
    if protocol == "shadowsocks" and settings.get("password"):
        return [{**base, "type": "ss", "cipher": settings["method"], "password": settings["password"]}]
    if protocol not in ("vless", "vmess", "trojan"):
        return []
    result = []
    for client in settings.get("clients", []):
        if client.get("enable") is False:
            continue
        node = {**base, "type": protocol, "name": base["name"] + (" / " + str(client.get("email", "")) if len(settings["clients"]) > 1 else "")}
        node["password" if protocol == "trojan" else "uuid"] = client.get("password" if protocol == "trojan" else "id", "")
        if protocol == "vmess":
            node.update(cipher="auto", alterId=0)
        if client.get("flow"):
            node["flow"] = client["flow"]
        network = stream.get("network", "tcp")
        if network != "tcp":
            node["network"] = network
        if network == "ws":
            node["ws-opts"] = {"path": stream.get("wsSettings", {}).get("path", "/"), "headers": stream.get("wsSettings", {}).get("headers", {})}
        if network == "grpc":
            node["grpc-opts"] = {"grpc-service-name": stream.get("grpcSettings", {}).get("serviceName", "")}
        security = stream.get("security")
        if security in ("tls", "reality"):
            node["tls"] = True
            tls = stream.get("tlsSettings", {})
            node["servername"] = tls.get("serverName") or record.get("domain", "")
        if security == "reality":
            reality = stream.get("realitySettings", {})
            public_key = reality.get("settings", {}).get("publicKey")
            if not public_key and reality.get("privateKey"):
                from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey
                from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat
                raw = base64.urlsafe_b64decode(reality["privateKey"] + "=" * (-len(reality["privateKey"]) % 4))
                public_key = base64.urlsafe_b64encode(X25519PrivateKey.from_private_bytes(raw).public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)).decode().rstrip("=")
            if not public_key:
                continue
            node.update(servername=(reality.get("serverNames") or [""])[0], **{"client-fingerprint": "chrome", "reality-opts": {
                "public-key": public_key, "short-id": (reality.get("shortIds") or [""])[0]}})
        result.append(node)
    return result


def node_outbound(node, tag):
    kind = node.get("type")
    if kind not in ("vless", "vmess", "trojan", "ss", "socks5", "http"):
        raise ValueError("中转落地支持 VLESS、VMess、Trojan、Shadowsocks、SOCKS5 和 HTTP；Xray 不支持此落地协议")
    host, port = node["server"], valid_port(node["port"])
    if kind == "vless":
        settings = {"address": host, "port": port, "id": node["uuid"], "encryption": node.get("encryption", "none"), "flow": node.get("flow", "")}
    elif kind == "vmess":
        settings = {"vnext": [{"address": host, "port": port, "users": [{"id": node["uuid"], "security": node.get("cipher", "auto"), "alterId": node.get("alterId", 0)}]}]}
    else:
        endpoint = {"address": host, "port": port}
        if kind in ("trojan", "ss"):
            endpoint["password"] = node["password"]
        if kind == "ss":
            endpoint["method"] = node["cipher"]
        if kind in ("socks5", "http") and node.get("username") is not None:
            endpoint["users"] = [{"user": node.get("username", ""), "pass": node.get("password", "")}]
        settings = {"servers": [endpoint]}
    outbound = {"tag": tag, "protocol": {"ss": "shadowsocks", "socks5": "socks"}.get(kind, kind), "settings": settings}
    network = node.get("network", "tcp")
    if network not in ("tcp", "ws", "grpc"):
        raise ValueError("该落地传输暂不支持，请使用 TCP、WebSocket 或 gRPC")
    stream = {"network": network, "security": "none"}
    if node.get("reality-opts"):
        r = node["reality-opts"]
        stream.update(security="reality", realitySettings={"serverName": node.get("servername", ""), "fingerprint": node.get("client-fingerprint", "chrome"),
                      "publicKey": r.get("public-key", ""), "shortId": r.get("short-id", ""), "spiderX": "/"})
    elif node.get("tls"):
        stream.update(security="tls", tlsSettings={"serverName": node.get("servername") or node.get("sni", ""), "allowInsecure": bool(node.get("skip-cert-verify"))})
    if network == "ws":
        opts = node.get("ws-opts") or {}
        stream["wsSettings"] = {"path": opts.get("path", "/"), "headers": opts.get("headers", {})}
    if network == "grpc":
        stream["grpcSettings"] = {"serviceName": (node.get("grpc-opts") or {}).get("grpc-service-name", "")}
    outbound["streamSettings"] = stream
    return outbound


def read_xray(api):
    wrapper = json_object(api.request("panel/xray/", {}))
    configuration = json_object(wrapper["xraySetting"])
    return configuration, wrapper


def write_xray(api, configuration, wrapper):
    api.request("panel/xray/update", {"xraySetting": json.dumps(configuration, ensure_ascii=False),
                "outboundTestUrl": wrapper.get("outboundTestUrl", "https://www.google.com/generate_204")})
    api.request("panel/api/server/restartXrayService", {})
