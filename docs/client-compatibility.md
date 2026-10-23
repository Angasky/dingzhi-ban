# 电脑、安卓与 iPhone 客户端

入口为「总览 → 客户端订阅与扫码导入」、订阅列表中的「客户端订阅」，以及链式代理页的「客户端导入」。三套主题使用同一份实际兼容性报告。尚无有效服务器主备组时，主备导出显示具体原因，不提供空配置下载。

| 客户端 | 普通节点订阅 | 多入口主备专用格式 | 用量与有效期 |
| --- | --- | --- | --- |
| 电脑 v2rayN | `/convert/<token>/v2rayn` | `/convert/<token>/v2rayn-ha`，Xray JSON 数组 | 标准响应头；首个真实节点名称和自定义配置备注附带摘要 |
| 安卓 v2rayNG | `/convert/<token>/v2rayng` | `/convert/<token>/v2rayng-ha`，Xray JSON 数组 | 标准响应头；自定义配置备注也附带摘要 |
| iPhone Shadowrocket | `/convert/<token>/shadowrocket` | 先 `/shadowrocket-ha` 节点订阅，再 `/shadowrocket-config` 策略配置（均位于同一 `/convert/<token>/` 下） | 节点订阅提供标准响应头；策略文件含生成时的摘要注释 |

旧 `/v2ray` 和 `/shadowrocket` 节点列表地址继续可用。普通节点列表不能表达主备策略；Mihomo 的 `dialer-proxy` 节点仍不会被误导出为独立节点。安卓专用节点订阅会过滤 TUIC 并报告原因；桌面 TUIC/Hysteria2 节点还需要客户端选择支持这些协议的内核。

## v2rayN 与 v2rayNG 主备导入

1. 先在面板建立「多入口主备」，所有服务器须已登记，并成功创建订阅专属中转入站。
2. 在客户端新增一个独立订阅，填入对应的 `v2rayn-ha` 或 `v2rayng-ha` 地址，更新订阅。不要将该地址当作 Base64 节点列表。新版上游导入器支持 JSON 对象数组，每个主备组对应一份自定义配置。
3. 选中需要的主备组，使用 Xray 内核启动。配置监听本机 `127.0.0.1:10808` SOCKS 和 `127.0.0.1:10809` HTTP。App 的自定义配置/本地 SOCKS 端口设置需匹配；修改过 App 默认端口时，应调回 10808 或同步修改下载的 JSON。不能同时启动占用这些端口的其他配置。
4. 此配置将流量发往所选主备组，不复制面板原有的分流规则或 DNS 配置。客户端代理/VPN 模式本身仍需开启。

实现使用 Xray `observatory` 对所有入口转发路径进行检测。每一级 balancer 只有一个候选路径，使用带健康过滤的 `random` 策略；不可用时通过 `loopback` 转到下一级，最后一级转 `blackhole`。单候选并不产生随机入口分配，因此保留严格的主备顺序，主入口恢复后新连接再次选择主入口。所有实际路径都是服务器转发到同一出口的独立节点。

缺失、撤销、配置顺序不一致或无法无损转换的组会整体拒绝导出，不会静默删除坏路径后改变主备结构。不同组可以分别报告兼容性。当前原生路径转换支持 VLESS（含 Reality）、VMess、Trojan、Shadowsocks、SOCKS5、HTTP，以及 TCP/WebSocket/gRPC 传输；包含尚未适配的证书固定、插件或扩展传输参数时不会静默丢弃这些设置。

## Shadowrocket 两步导入

1. 在首页添加「小火箭主备 · 第一步：节点订阅」并更新。该订阅仅含受管主备组的专属转发路径。
2. 在「配置」页添加「第二步：策略配置」地址，下载并启用，使用配置路由。在 `MX-Select` 组选择目标主备组。
3. 不要对第一步节点使用重命名过滤、额外前后缀；配置按准确名称引用这些节点。导出名称含按订阅隔离的稳定哈希和可读标签，并过滤配置分隔符及 Unicode 换行符；不同订阅的同名节点不会共用策略别名。用普通小火箭节点列表替代第一步会导致名称不匹配。
4. 不要开启可能选择其他出口的全局回退。生成的 fallback 组仅列出该组的入口路径；没有 DIRECT 或其他出口兜底。更新节点订阅和策略配置后重新启用配置。

fallback 的探测周期、超时和节点顺序已写入策略文件。Shadowrocket 为闭源 iOS App，当前环境无法验证它的运行行为，特别是主入口恢复后的切回与 App 自身排序/回退开关的交互；应在实际 iPhone 上断开入口并恢复验证，不把文件生成测试当作客户端实测。

## 用量与有效期

所有格式的 GET/HEAD 都继续提供 `Subscription-Userinfo: upload=…; download=…; total=…; expire=…`，字节为累计值、到期时间为 Unix 秒。已用为上传加下载，剩余为总额度减已用，最低为零。总额度未设置时表示不限量；未设置到期时间时为长期有效。未配置计量时，不伪造流量或到期摘要。

新版 v2rayNG 源码包含此响应头的解析和订阅信息界面。不能保证所有 v2rayN 版本提供同一信息面板，因此其专用订阅在首个真实节点名称中附带「已用 / 可用 / 有效至」摘要，没有添加不可连接的假节点，也不改动该节点的连接参数。「可用/有效至」避免被 v2rayN 默认的「剩余/到期」信息节点过滤规则误排除。自定义 JSON 配置的备注也携带摘要。

这些都是整个订阅的服务端统计，随订阅刷新更新，不是每个客户端独占的实时统计。是否显示原生用量组件取决于 App 版本；备注摘要用于兼容。启用服务器强制限制后，到期/超额会阻止所有格式订阅下载，已经下载的配置仍由服务器撤销专属入站来阻断；不能仅依靠客户端隐藏节点来限制访问。小火箭策略注释是生成时快照，实际用量请刷新其节点订阅。

## 验证范围和上游依据

2026-10-23 核对了这些源文件，而非宣称用户设备上的 App 已测试：

- [v2rayN `57f17d5` 自定义 JSON 导入](https://github.com/2dust/v2rayN/blob/57f17d5/v2rayN/ServiceLib/Handler/Fmt/V2rayFmt.cs)：对象/数组、`inbounds`/`outbounds`、`remarks` 与 Xray 内核选择。
- [v2rayNG `01b4630` 导入器](https://github.com/2dust/v2rayNG/blob/01b4630/V2rayNG/app/src/main/java/com/v2ray/ang/handler/AngConfigManager.kt)、[订阅信息解析器](https://github.com/2dust/v2rayNG/blob/01b4630/V2rayNG/app/src/main/java/com/v2ray/ang/handler/SubscriptionInfoParser.kt)。该源码声明版本 2.3.13；旧版本的支持可能不同。
- [Shadowrocket 公开指南 `9ff0cc1`](https://github.com/LOWERTOP/Shadowrocket/blob/9ff0cc1/README.md#代理分组类型)：组类型、间隔、超时、节点引用及回退边界。这是社区指南，并非闭源 App 的执行测试。
- Xray 26.10.21 Linux 原生验证：七种路径配置通过 `run -test`；三个本地入口共用同一出口，实际验证主入口 → 备用一 → 备用二 → 主入口，以及全部入口失效时连接失败。此测试不依赖外网服务。
- HTTP 回归验证各格式 GET/HEAD 的用量响应头、JSON/CONF 下载类型、到期/超额 403、配置隔离、路径缺失/撤销拒绝导出及小火箭两份文件的节点名称一致性。
- Chromium 使用模拟 API 数据检查三套主题的客户端导入窗口。Windows、Android、iOS App 的实际启动、VPN 接管及公网出口 IP 仍需在用户设备验证。

```sh
MXIOC_XRAY_BIN=/path/to/xray python -m unittest discover -s tests -p test_client_subscriptions.py -v
MXIOC_XRAY_BIN=/path/to/xray python -m unittest discover -s tests -p test_xray_failover.py -v
```
