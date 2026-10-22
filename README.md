# 🚀 MXIOC VPN Manager

一个面向 Mihomo / Clash 的可视化订阅管理后台。无需数据库，通过网页即可管理节点、链式代理、策略组、路由规则、DNS 分流和多份订阅配置。

## ⚡ 一键安装

在 Linux 服务器中执行：

```bash
curl -fsSL https://raw.githubusercontent.com/Angasky/mxioc-vpn-manager/main/install.sh | sudo bash
```

安装完成后，服务器会自动创建快捷命令：

```bash
vpn
```

以后输入 `vpn` 就能重新打开管理菜单，不需要再次复制安装命令。普通用户执行时会自动调用 `sudo`。

```text
1) 🌐 IP 部署模式
2) 🔒 域名 HTTPS 模式
3) ⚡ 极速安装模式
4) ⬆️ 升级 / 修复
5) 🔑 重置管理员凭证
6) 📦 卸载并保留数据
7) 🗑️ 彻底卸载
0) 👋 退出
```

> 推荐选择域名 HTTPS 模式。脚本会检查域名的 A/AAAA 记录，识别 Cloudflare 小黄云状态，并自动申请和续期 Let's Encrypt 证书。

### 三种部署方式

- 🌐 **IP 模式**：默认使用 80 端口；直接回车保留默认值，也可以填写其他端口。
- 🔒 **域名 HTTPS 模式**：默认使用 443 端口，也可以自定义 HTTPS 端口；80 端口始终保留给证书验证和续期。
- ⚡ **极速模式**：不询问域名和端口，默认使用 5656；如果端口已占用，会自动选择其他空闲端口。

使用 Cloudflare 托管域名时，如果检测到小黄云代理已开启，安装器会停止申请证书并提示先切换到“仅 DNS（灰色云朵）”。

## ✨ 主要功能

### 🧩 节点与订阅

- VLESS、TUIC、Hysteria2、Trojan、Shadowsocks、VMess、SOCKS5、HTTP 和 HTTPS 节点管理
- 节点分享链接一键导入，兼容 IPv4 与 IPv6
- Clash YAML / Base64 订阅批量导入
- 总览集中展示 Clash、V2Ray/v2rayN 与 Shadowrocket 订阅，并为每个链接生成本地二维码
- 公开订阅地址无需后台登录 Cookie，可直接粘贴或扫码导入客户端
- 节点拖动排序、上移、下移、全选和批量删除
- 根据节点公网 IP 自动添加国家旗帜，重复执行不会叠加
- 多订阅的新建、复制、重命名、切换和删除
- 独立“订阅管理”页面：搜索名称/标识、筛选状态、按名称或创建顺序排序，并按页显示订阅卡片
- 顶部显示当前订阅，点击进入管理页面切换；不再通过长下拉列表选择配置
- 订阅卡片可直接进入节点配置、设置流量、复制链接、显示二维码、复制为新订阅或重命名
- 新安装与新建订阅默认使用约 256-bit 强度的随机访问密钥，不再暴露固定 `/clash` 地址
- 需要固定路径时可以手动开启自定义 Slug，但长度必须为 32–80 位
- 转换为 v2rayN 与 Shadowrocket 可用的 Base64 订阅
- 转换前显示协议兼容性，无法转换的节点会明确提示
- 每份订阅独立设置流量额度、已用上传/下载流量、到期时间；总览显示用量与剩余额度
- Clash / Mihomo、V2Ray 和 Shadowrocket 三种订阅统一返回标准 `Subscription-Userinfo`，支持 GET 与 HEAD
- 未配置流量时不再返回虚假的总额度和到期日期

### 📊 流量与到期

选择当前订阅后，打开侧栏“流量与到期”，填写总额度、已用上传与下载流量，以及本地时间的到期日期。
支持 GiB、MiB、TiB 单位；总额度留空表示不限，到期日期留空表示长期有效。保存后，在客户端手动更新订阅即可读取新的信息。

这是一份由管理员维护的展示数据，不会自动统计节点真实传输流量，也不自动限制连接或封停节点。
三个导出格式使用相同的统计来源；客户端是否显示额度卡片取决于其版本和对订阅响应头的支持。
数据持久保存在现有 `profiles.json` 中；复制订阅模板时不会复制其他订阅的流量用量。

### 🎨 界面与性能

- 统一深色配色、表单、列表、弹窗与手机导航
- 首次访问先验证会话，避免登录页和后台界面相互闪现
- 订阅卡片、节点、链路、策略组、路由规则和 DNS 列表每页最多 50 项
- 搜索延迟 120 ms 合并输入，并保留输入框焦点和光标
- 无外部字体、动画库或新增生产前端依赖
- “设置”中可选择高配主题（经典终端）或低配主题（轻量界面），立即切换并保存在当前浏览器
- 新安装及未保存主题偏好的浏览器默认使用高配主题；低配主题关闭持续闪烁和弹窗背景模糊
- 主题偏好在样式绘制前读取，避免先显示默认主题再切换的闪屏；系统开启“减少动态效果”时停用动画

### 🔗 链式代理

- 可视化选择入口节点和出口节点
- 自定义链式代理名称
- 选择加入一个、多个或全部策略组
- 删除普通节点时自动清理策略组中的失效引用

### 🖥️ 服务器管理与 3x-ui

侧栏“服务器管理”可登记服务器 IP、SSH 账号、密码、SSH 端口，以及预设的 3x-ui 面板账号、密码和可选域名。

已有 X-ui / 3x-ui 时，点击“添加已安装面板的服务器”，填写 SSH 信息和现有面板账号密码（不是设置新密码）。系统仅通过 SSH 读取 `/usr/local/x-ui` 的面板设置，验证登录及入站列表，然后返回管理地址；不运行安装脚本、不重置凭证、不修改原有节点。该模式不提供安装/更新按钮，后端也禁止安装。没有 API 令牌的版本使用登录会话；旧版无 CSRF 接口时仅在明确返回 404 后兼容。需支持 `/panel/api/inbounds/list` 等 X-ui 入站 API；不兼容的版本会显示验证错误，连接信息仍保留以便修正后重试。可在接入后继续管理节点；中转功能仍取决于对应版本是否支持所需的 Xray 配置接口。
SSH 账号需要 root 或 sudo 权限。首次连接先检测、核对并固定 SSH 主机指纹；指纹变化会中止连接。

1. 添加服务器，填写 SSH 信息和至少 12 位的面板密码，选择无域名或绑定域名模式并保存。
2. 使用“测试 SSH”检查认证和提权权限，再点击“安装 / 更新”确认执行。
3. 安装在后台运行，可关闭日志窗口后重新打开。日志显示阶段、真实错误和退出码，密码和令牌会脱敏。
4. 脚本完成后设置预设面板凭证，验证登录、服务和 API，读取远端实际端口及路径并返回面板地址。
5. 失败时自动读取服务状态、最近日志、监听端口和域名解析；排查后可重试。不会自动清空远端数据或把失败的域名安装改成 HTTP。

安装来源固定为用户指定的 [Teminuosi/3x-ui 安装脚本](https://github.com/Teminuosi/3x-ui/blob/main/install.sh)。
域名模式以 `XUI_DOMAIN=域名` 执行，无域名模式以 `XUI_AUTO=1` 执行。
脚本先下载并检查下载退出码再运行，避免下载失败被误认为安装完成。该脚本生成的随机初始凭证会在安装结束后替换为表单中预设的账号密码。
绑定域名时请先完成 DNS 解析并放行 80 端口；还需在云防火墙放行实际面板端口和需要使用的节点端口。

“节点 / 中转”直接调用该分支的 3x-ui 接口：

- 新建 VLESS + Reality/Vision、VMess TCP、Shadowsocks AES-128-GCM 入站。
- 通过 JSON 编辑入站配置，启用、停用、删除，复制分享链接并导入当前订阅。
- 新建中转时以 Reality 为入口，落地支持 VLESS、VMess、Trojan、Shadowsocks、SOCKS5、HTTP 分享链接。
- 中转会创建入口入站、落地出站、最高优先级路由；保留原有配置并加密保存变更前备份。失败时尝试恢复配置并删除本次入口，部分恢复失败会明确提示。
- 删除本面板创建的中转入站时会同步清理其路由和出站；本地已导入的订阅链接仍需单独清理。
- “移除管理”只删除总面板中的登记与凭证，不卸载远端 3x-ui。

管理 API 通过固定主机指纹的 SSH 隧道访问远端回环地址，支持面板 HTTP/HTTPS、Bearer 令牌及登录 CSRF 会话。
SSH 与面板密码加密保存在 `/opt/mxioc-rule-manager/remote/servers.json`，密钥在同目录 `credentials.key`；目录权限为 0700、文件为 0600。
备份或迁移时须同时保存数据和密钥，丢失密钥后需重新填写服务器凭证。保留数据卸载会把该目录一并归档。
总面板重启会把尚未确认完成的任务标记为中断，请先诊断远端运行状态再重试；安装器使用远端锁防止重复安装。
本功能需要总面板运行 Python 3.9+，升级脚本会安装固定版本的 Paramiko 与 Cryptography。

### 🧭 规则与 DNS

- 策略组、路由规则和 DNS 分流的可视化增删改查
- 中国业务直连，境外业务使用加密海外 DNS
- Google、YouTube、TikTok、Facebook、Telegram、Netflix、WhatsApp、X 等独立业务分组
- Google 网页、Android、Play、FCM、Firebase、Drive、Gemini 等业务完整分流
- YouTube 保持独立策略，不与其他 Google 服务混用
- 基础广告拦截与强力广告拦截
- 内置完整无私人节点的 Clash 起始模板

### 🛡️ 系统管理

- 兼容 IP/HTTP 与域名/HTTPS 登录
- 登录会话固定有效 12 小时，同时最多保留 8 个会话
- 网页和 Linux 菜单均可修改管理员用户名与密码
- 每天自动检查 GitHub 更新
- 网页和 `vpn` 菜单均可升级或修复程序
- 修改配置前自动创建快照备份
- 支持保留数据卸载与彻底卸载

## 🖥️ 快捷命令

安装后直接运行：

```bash
vpn
```

也可以跳过菜单，直接指定操作：

```bash
# 🌐 IP 模式
vpn --ip

# 🌐 IP 模式并指定端口
vpn --ip --port 8080

# 🔒 域名 HTTPS 模式
vpn --domain vpn.example.com --email admin@example.com

# 🔒 域名 HTTPS 模式并指定访问端口
vpn --domain vpn.example.com --port 8443 --email admin@example.com

# ⚡ 极速安装，默认端口 5656
vpn --fast

# ⬆️ 升级或修复
vpn --update-only

# 🔑 重置管理员用户名和密码
vpn --reset-auth

# 📦 卸载后台并归档用户数据
vpn --uninstall

# 🗑️ 彻底卸载后台和主订阅
vpn --purge
```

快捷命令每次运行都会获取仓库中的最新管理脚本，因此菜单功能会保持最新。

## 🔐 首次登录

首次安装会生成管理员账号 `admin` 和随机密码：

```text
/opt/mxioc-rule-manager/initial-password.txt
```

登录后可以在“账号与安全”中修改用户名和密码，也可以运行：

```bash
vpn --reset-auth
```

修改凭证后，所有旧登录会话都会自动失效。

## 📦 升级与卸载

### 升级

```bash
vpn --update-only
```

升级只替换后台程序、服务文件和内置模板，不会覆盖正在使用的节点、规则、订阅、管理员凭证、历史备份和 TLS 证书。

### 保留数据卸载

```bash
vpn --uninstall
```

用户数据会先归档至：

```text
/var/backups/mxioc-rule-manager-日期时间/user-data.tar.gz
```

归档失败时会自动取消卸载。

### 彻底卸载

```bash
vpn --purge
```

彻底卸载会删除管理后台、后台运行数据和主 Clash 订阅，但不会删除其他 sing-box 文件或服务器上的 TLS 证书。

## 📁 重要路径

```text
程序目录        /opt/mxioc-rule-manager
快捷命令        /usr/local/bin/vpn
认证文件        /etc/mxioc-rule-manager.auth.json
主订阅          /etc/sing-box/subscribe/clash-cn-route
主订阅备用路径  /etc/sing-box/subscribe/clash-cn-route.yml
Nginx 配置       /etc/nginx/conf.d/mxioc-rule-manager.conf
systemd 服务    /etc/systemd/system/mxioc-rule-manager.service
数据归档        /var/backups/mxioc-rule-manager-日期时间
```

后台程序只监听 `127.0.0.1:62577`，公网访问由 Nginx 反向代理提供。

## 🧰 环境要求

- Debian / Ubuntu
- RHEL / CentOS / Rocky Linux / AlmaLinux
- Python 3.9+
- systemd
- Nginx
- 可访问 GitHub 与系统软件源

安装脚本会自动准备 Python、PyYAML、Nginx、Curl、DNS 工具和证书组件。

## 🚫 广告规则自动更新

需要定时更新本地广告规则时可以启用：

```bash
sudo install -m 0755 work/update-ad-rules.py /usr/local/libexec/update-mxioc-ad-rules.py
sudo install -m 0644 work/mxioc-ad-rules.service work/mxioc-ad-rules.timer /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now mxioc-ad-rules.timer
```

## 🔒 数据安全

仓库只保存程序源码和无私人节点的模板，不包含生产环境中的节点链接、服务器密码、管理员密码、订阅文件、历史备份或 TLS 私钥。

节点国旗识别会把节点服务器 IP 发送给 `api.country.is`，仅用于获取 ISO 国家代码，查询结果会在内存中缓存七天。

## 🧪 开发检查

```bash
python -m pytest -q
bash -n install.sh
```

项目当前由 Python 后端、单页网页前端、Nginx 和 systemd 组成，不依赖数据库。
