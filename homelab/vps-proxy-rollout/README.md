# VPS 代理与 HTTPS 迁移

适用于 `nosla`、`raksmart`、`bitsflow` 和 `lightlayer`。先保留现有节点与站点，逐台新增本机域名的 Reality TCP 443，复用同机 Hysteria 2。默认用 IPv4 连接，继续保留当前站点所用的 IPv6 服务。此文档记录 2026-11-18 的只读基线和获批后的操作顺序。实例域名、地址、订阅和密钥保存在实际部署环境中；文中 `<reality-port>`、`<hy2-port>`、`<old-hy2-port>`、`<hop-range>`、`<1panel-port>`、`<3xui-port>`、`<sub-port>` 为各台实际端口的占位符，可查 3x-ui 面板与各 VPS `/root/vps-proxy-rollout-20261118/` 备份。

## 2026-11-18 上线记录

四台按 `lightlayer`、`bitsflow`、`nosla`、`raksmart` 的顺序完成服务端迁移。Xray 均为 `26.11.9`，新 Reality 在公网 IPv4 TCP 443，旧 Reality 继续提供服务。独立 VPS 实测新 Reality 和固定端口 Hysteria 2 均能返回对应 VPS 的公网 IPv4，现有 HTTPS 站点的响应状态和 ALPN 保持原基线。`lightlayer` 使用 Debian nginx，另三台沿用 1Panel OpenResty。回落端口是 `127.0.0.1:8443`。

| SSH profile | 旧 Reality TCP | Hysteria 2 固定端口 | 跳跃范围 |
| --- | ---: | ---: | ---: |
| `lightlayer` | `<reality-port>` | `<hy2-port>` | `<hop-range>/udp` |
| `bitsflow` | `<reality-port>` | `<hy2-port>`（兼容原 `<old-hy2-port>`） | `<hop-range>/udp` |
| `nosla` | `<reality-port>` | `<hy2-port>` | `<hop-range>/udp` |
| `raksmart` | `<reality-port>` | `<hy2-port>` | `<hop-range>/udp` |

- 3x-ui 绑定回环。3x-ui 和三台 1Panel 的原生管理端口均有 IPv4/IPv6 防火墙规则拒绝非回环访问。现有 1Panel 的 HTTPS 反代继续工作，`<sub-port>` HTTPS 订阅继续对原客户端开放。
- 路由保留原 API、私网、BT 规则。两台 daed 的 `PROXY_CN_DOMAINS` 现有域名后缀允许代理访问；`223.5.5.5` 只放行 UDP 53 供现有 daed 检测使用。其余代理流量按 `geosite:cn`、`geoip:cn` 匹配阻断。LightLayer 已实测国内例外站点有 HTTP 响应，普通大陆域名和 IP 主动测试失败。Reality 现有的显式最低版本未调整。
- `bitsflow` 在带 WAL 的数据库复核中已是 `<hy2-port>`，实际偏差在持久化 NAT 和客户端旧入口。现在两族跳跃都转到 `<hy2-port>`，旧 `<old-hy2-port>` 也转到 `<hy2-port>`。固定端口、旧端口和两个跳跃样本均已实测。
- `lightlayer` 的 Hysteria 2 使用 Salamander。初次只读探测未包含该参数，修正后从独立 VPS 验证了固定端口。从独立 VPS 对四台分别跑了四次跨周期的 Hysteria 2 请求，每台观测到至少四个不同的目标 UDP 端口，请求均返回各自 VPS 的 IPv4。
- 两个 Headscale 站点的 `/health` 和 `/derp/probe` 均正常。Raksmart 的九个 Sub-Store 保热下载均返回 HTTP 200。四台 3x-ui 源订阅都已包含新 443 节点和原有节点；检查时所测 Sub-Store 聚合输出尚未检出新节点（后查为 443 节点按本设计以裸 IPv4 作连接地址、域名仅在 SNI，节点档案规则按域名 host 匹配不到、`profile` 为空被过滤；已为 `node-profile-filter.js` 增加 `sni` 匹配并在规则文件补 443 条目，见 [substore-operators](../substore-operators/README.md)）。

### 客户端验收

| VPS | Windows 的 v2rayN Xray 内核 | 家庭路由器上的 Xray 内核 |
| --- | --- | --- |
| `nosla` | Reality 和 Hysteria 2 均通过 | Reality 和 Hysteria 2 均通过 |
| `raksmart` | Reality 和 Hysteria 2 均通过 | Reality 和 Hysteria 2 均通过 |
| `bitsflow` | Reality 通过，Hysteria 2 可用跳跃范围两端端口，主端口失败 | Reality 通过，Hysteria 2 主端口及两个跳跃样本失败 |
| `lightlayer` | Hysteria 2 通过，Reality TCP 443 超时 | Hysteria 2 通过，Reality TCP 443 未通过 |

Windows 使用 v2rayN `7.25.6` 自带的 Xray `26.11.9` 独立测试。若 Windows 上需要使用 Bitsflow 的 UDP 备线，可复制现有 Hysteria 2 节点，仅把远端端口改成跳跃范围两端端口，沿用原 SNI 和认证。家庭路由器经 Tailnet 访问，实机 Passwall2 为 `26.8.20-r1`，已通过其导入器添加四个新 443 节点。

两台 daed 已更新正在使用的入口规则，并直接导入了四个新节点。Headscale 域名分流优先于共享 IP 的 TCP 443 直连，两台本机 DNS 均通过验证。原订阅刷新在其中一台 daed 路由器成功 4 项，另一台成功 2 项、失败 2 项。直接导入的节点需要在对应客户端中选用。Android 的 v2rayNG 实机连通性待设备上线后验收。

### Windows 访问 3x-ui

在这台电脑双击桌面的 **3x-ui 面板** 快捷方式。它使用 WSL `Ubuntu` 的四个 SSH profile 建立回环转发，并在独立 Edge 配置中打开四个登录页。浏览器保留原证书域名与端口，登录仍使用原有账号。重启电脑或 WSL 后，双击快捷方式即可重建转发。普通浏览器窗口仍使用公网解析，因此请从快捷方式打开面板。

四个登录页已通过 Windows 本地转发和 HTTPS 证书校验。转发脚本位于 WSL 的 `~/.local/bin/vps-3xui-tunnels`，启动脚本和私有登录地址保存在 Windows 的 `%LOCALAPPDATA%\VPS-3xUI`。这条管理通路只依赖 SSH，适用于公网 443 超时的 LightLayer。

备份、阶段日志和恢复工具在各 VPS 的 `/root/vps-proxy-rollout-20261118/`，包含敏感配置。`acme.sh` 的原有续签钩子仍刷新 Xray 证书，并调用 `/usr/local/sbin/vps-proxy-origin-renew` 更新 nginx/OpenResty 的本机站点证书镜像。已用当前正式证书演练站点 reload。保留该目录供续签和恢复使用。

回退 TCP 443 切换时，在对应 VPS 执行 `python3 /root/vps-proxy-rollout-20261118/rollout.py <profile> rollback-cutover`。它恢复阶段前 3x-ui 数据库和原站点监听，运行前先保存此后新增的面板设置与统计。管理入口与路由的回退命令是 `rollback-harden`，应在 TCP 443 已回退后执行。日常在 1Panel 新建或保存站点后，检查生成的 vhost 仍采用 `127.0.0.1:8443` 和原 IPv6 监听。两台 daed 的 `before/` 备份及 Passwall2 导入前的 `passwall2-before-import` 也保存在各路由器的 `/root/vps-proxy-rollout-20261118/`。

## 只读基线

四台均为 Debian 13、3x-ui `3.11.0`、Xray `26.10.21`。四台 SSH 和 IPv4 HTTPS 出网正常。`nosla`、`bitsflow` 有公网 IPv6 和默认路由，IPv6 HTTPS 出网成功。两机之间的 IPv6 TCP 443 能收到 TLS 响应，但 Headscale 域名在 IPv6 默认站点上被拒绝握手。国内 IPv6 路径质量待实测。`raksmart`、`lightlayer` 只有链路本地 IPv6。

| SSH profile | Reality TCP | Hysteria 2 主端口与跳跃 | HTTPS 站点 | 公网可达的管理端口 |
| --- | ---: | --- | --- | --- |
| `nosla` | `<reality-port>` | `<hy2-port>`；`<hop-range>` 转发到 `<hy2-port>` | Headscale、Magpie、1Panel、Shot2Agent Relay | 1Panel `<1panel-port>` HTTP、3x-ui `<3xui-port>` HTTPS |
| `raksmart` | `<reality-port>` | `<hy2-port>`；`<hop-range>` 转发到 `<hy2-port>` | Hermes、Komari、1Panel、Sub-Store | 1Panel `<1panel-port>` HTTPS、3x-ui `<3xui-port>` HTTPS |
| `bitsflow` | `<reality-port>` | 运行态 `<hy2-port>`；面板数据库和跳跃规则仍指向 `<old-hy2-port>` | Headscale、1Panel | 1Panel `<1panel-port>` HTTP、3x-ui `<3xui-port>` HTTPS |
| `lightlayer` | `<reality-port>` | `<hy2-port>`；`<hop-range>` 转发到 `<hy2-port>` | TCP 443 空闲，无站点 | 3x-ui `<3xui-port>` HTTPS |

三台 OpenResty 的 Compose 和容器运行态均为 `host`，站点从 `/opt/1panel/www/conf.d/` 挂载到容器内 `conf.d/`。应用目录 `conf/default/00.default.conf` 的默认站点额外监听 TCP 443 和 QUIC UDP 443。现有站点证书在本机均通过验证，全部协商 TLS 1.3。除 Relay 协商 HTTP/1.1 外，各站点协商 HTTP/2。受检站点的 HTTP 强制跳转基于 `$scheme`。Headscale、1Panel 等反代设置了 `X-Forwarded-Port $server_port`，迁移到回环 8443 后应继续向上游传递外部端口 443。保留 Headscale 的 Upgrade、DERP 和 UDP 3478，以及 Relay 的连接升级和请求限制。

各机 3x-ui 的 TCP `<sub-port>` 是 HTTPS **订阅**入口，公网可达。管理面板的直连收口须单独处理，现有订阅和 Sub-Store 缓存须一起验收。四台现有 Xray 的路由为 `AsIs`，依序处理本地 API、私网阻断、BitTorrent 阻断，代理入站开启了嗅探。现有 Reality 使用 Vision、第三方目标、`xver=0` 和显式 `minClientVer=1.0.0`。截至体检，Xray 最新正式版为 `26.11.9`，3x-ui 最新正式版为 `3.11.0`。[Xray 正式版](https://github.com/XTLS/Xray-core/releases/tag/v26.11.9) · [3x-ui 正式版](https://github.com/MHSanaei/3x-ui/releases/tag/v3.11.0)

用本地 Xray `26.11.9`、现有节点认证和独立 HTTPS IPv4 目标做了数据面测试。跳跃抽样固定打到跳跃范围两端，用来核验转发链路。各结果只覆盖体检网络和取样端口。

| SSH profile | 旧 Reality | Hysteria 2 主端口 | Hysteria 2 跳跃抽样 |
| --- | --- | --- | --- |
| `nosla` | 通过 | 通过 | 两个通过 |
| `raksmart` | 通过 | 通过 | 两个失败，原因待查 |
| `bitsflow` | 超时 | `<hy2-port>` 通过，`<old-hy2-port>` 失败 | 两个失败，与转发目标不一致吻合 |
| `lightlayer` | 超时 | 失败 | 两个失败，原因待查 |

通过表示 HTTPS 请求返回了同机公网 IPv4。本机分别验证过所有站点 TLS。外网探测中 `bitsflow` 的 Headscale 域名 TLS 和旧 Reality 有异常，因此保留体检基线，另从用户网络做切换前对照。TCP 通、UDP NAT 有计数、DNS 解析正常分别只能说明各自一层。同机两种协议可以复用 IPv4 出口，切换时应用连接仍需重建。IP 级故障需要其他 VPS，平台账户风控由平台自身决定。

用校验过摘要的 Xray `26.11.9` 做了本地试验。含 CN、私网与 BT 的路由模板通过配置检查。使用模拟 TLS 后端时，Reality 对配置内和配置外的两个 SNI 都能回落并协商 TLS 1.3 与 HTTP/2。生产站点、正式证书和实际客户端仍须逐台验收。

## 域名和客户端

- 建议每台使用当前 Hysteria 2 的证书主域名。四个主域名已有与各自 VPS 匹配的直连 A 记录、无 AAAA，并由 `acme.sh` 的 DNS-01 任务续签证书。体检时这些域名均没有自己的 HTTPS 站点。获批后给它们各建一个可正常访问的轻量站点，TLS 1.3 和 HTTP/2 都开启。OpenResty 只读挂载所需证书文件，`lightlayer` 用单独的轻量 Web 服务。先复用现存证书与续签机制，同时验证续签后的 Web reload 和 Xray 证书加载。
- 四个节点都用单独的公网上行 IPv4 作连接地址，SNI 用经确认的证书域名。控制面和已有站点保留当前的 DNS、Cloudflare 模式与访问方式。IPv6 节点作为后续独立配置，等在实际网络测通后再发布。
- Windows `v2rayN` `7.25.6`、Android `v2rayNG` `2.3.15` 的上游版本能导入 Hysteria 2 的 `mport`。先实测设备使用的内核版本、VLESS Vision/Reality 指纹和固定端口 Hysteria 2，再试跳跃，核对导入和生成的运行配置。TLS 保持证书校验。[v2rayN 导入代码](https://github.com/2dust/v2rayN/blob/7.25.6/v2rayN/ServiceLib/Handler/Fmt/Hysteria2Fmt.cs) · [v2rayNG 导入代码](https://github.com/2dust/v2rayNG/blob/2.3.15/V2rayNG/app/src/main/java/com/v2ray/ang/fmt/Hysteria2Fmt.kt)
- 两台单位网络路由器均运行 daed `2026.08.13`。订阅先发布固定端口版本，保留跨 VPS、双协议的 `doh` 组。参照已有配置，为新旧入口添加精确的 `IPv4 + 协议 + 端口` 直连规则；验证订阅冷启动、DNS、Headscale 和实际连通性。路由器需要跳跃时，先验证 daed 内核和订阅转换都能保留端口范围，再配置对应范围直连。[openwrt-dns-daed](../openwrt-dns-daed/README.md) · [Sub-Store](../substore-operators/README.md)
- 家庭路由器与一台单位网络路由器当前解析到同一 LAN 地址，SSH 检查发现相同的 Dropbear 主机密钥指纹，且到达的设备没有 Passwall2 服务。Passwall2 的上游订阅解析器支持 Hysteria 2 和 `mport`，验收还需连接真正的家庭路由器核对运行内核。[Passwall2 订阅解析代码](https://github.com/Openwrt-Passwall/openwrt-passwall2/blob/26.11.11-1/luci-app-passwall2/root/usr/share/passwall2/subscribe.lua)

## 执行顺序

收到域名和执行确认后逐台推进。先 `lightlayer` 试点 TCP 443，再 `bitsflow`、`nosla`、`raksmart`。后两台分别承载 Relay 和订阅汇聚，安排在已完成站点回归后。`bitsflow` 在任何可能重启 Xray 的操作前先处理 UDP 不一致，方法见第 4 节。每台都留一个已知可用的旧节点或跨 VPS 的备用节点。

### 1. 固定基线和回退

1. 开两个 SSH 会话，核对 SSH 端口、站点、代理和订阅最新状态。重跑 `bitsflow` 的 Xray 配置、面板数据库、监听套接字、订阅发布值、IPv4/IPv6 PREROUTING 与其持久化文件对照。从目标客户端网络验证 TCP 443 和至少一条备用代理通路。`lightlayer` 若缺少可用备用通路，先排查现有入站再安排试点。
2. 在 VPS 的仅 root 可读目录保存 SQLite 一致性备份、Xray 二进制与 Geo 数据、1Panel 站点和默认站点配置、证书续签钩子、防火墙变更前快照。预先列出本次修改路径与恢复顺序。用一台未切换的 VPS 或独立网络复测 HTTPS，体检失败的端点记录失败状态。面板直连改动前，先实际登录 SSH 转发和现有反代。
3. 面板数据库与 3x-ui 持久化模板通过面板或受控接口更新，`/usr/local/x-ui/bin/config.json` 用于观察生成结果。逐项应用，验证通过后再推进下一项。`raksmart` 的 SSH 使用该 profile 配置的非默认端口，其他三台当前为 `22`。

SSH 转发可用 `ssh -NT -L "127.0.0.1:${LOCAL_PORT}:127.0.0.1:${PANEL_PORT}" "$PROFILE"`。本地 HTTP 只走回环隧道，HTTPS 验证继续使用证书域名和既有安全入口路径。例行管理也可沿用现有的 HTTPS 反代。

### 2. 收口管理入口与 Xray 路由

1. 3x-ui 管理绑定回环，保留 `<sub-port>` HTTPS 订阅。1Panel 优先使用回环绑定，必要时通过当前持久化防火墙限制非回环访问。优先封住 `nosla` 和 `bitsflow` 的明文管理端口，同时保留原有 HTTPS 反代。防火墙由现有 iptables/nft 后端与 `netfilter-persistent` 管理，修改精确规则与持久化对应行，保留 Docker、Fail2ban 和 SSH。外网、SSH 转发、反代、订阅分别验收。
2. 校验官方 Xray 发布包摘要，用新二进制执行 `xray run -test -c <staged-config>`。一次升级一台，实测现有 Reality 与 Hysteria 2 后才保留升级。Reality `minClientVer` 留空会跳过最低版本比较。当前与拟升级内核的 Reality 握手均要求 `X25519MLKEM768`，客户端版本号和实际指纹都要测。以各客户端实测握手版本为依据设定门槛，保留现有门槛至验证完成，同时保持 Vision 和现有密钥。[REALITY 握手与版本判断](https://github.com/XTLS/REALITY/blob/4bb46b2afd8b/tls.go#L212-L281)
3. 保留本地 API 路由和私网拦截。在代理入站范围内，用独立规则分别匹配 `geosite:cn`、`geoip:cn`、`protocol:bittorrent` 并走现有 `blocked` 出站。用 `IPOnDemand` 复测直接 IP 和待解析域名的规则匹配、DNS、探测和一般代理流量。按用户确认，在 CN 阻断规则之前为 `PROXY_CN_DOMAINS` 现有后缀及 daed 的 UDP 检测地址配置例外。嗅探及 Geo 数据覆盖决定匹配范围。[daed 分流顺序](../openwrt-dns-daed/docs/principles.md)

### 3. 新增 Reality TCP 443

1. 先在 `127.0.0.1:8443` 建立新站点和 HTTPS 回落，核验 `openssl s_client -connect 127.0.0.1:8443 -servername "$domain" -tls1_3 -alpn h2 -verify_hostname "$domain" -verify_return_error`。`lightlayer` 只需要这个新站点。其他三台继续使用 host 网络，先在 8443 并行验证全部现有 vhost、默认站点和站点反代，保留 TCP 80 和 ACME 路径。Headscale、Relay 等保留 HTTP/1.1 协商和协议升级。HTTPS 后端用 443 对外构造跳转和转发头。
2. 用额外测试入站逐个验证不同 SNI 的回落。Reality 对不匹配 `serverNames` 的连接也会转交同一 TLS 目标，由 OpenResty 按 SNI 选择证书与站点。确认现有站点是否依赖来源 IP、Cloudflare 实际地址、限速和审计。默认 `xver=0` 时，本地回落使站点看到回环来源。依赖来源 IP 的站点在未解决前保留原入口。若 1Panel 保存、重载或更新会恢复 TCP 443 或破坏配置，暂停该机切换，先解决配置持久化。[REALITY 回落源码](https://github.com/XTLS/REALITY/blob/4bb46b2afd8b/tls.go#L163-L300)
3. 将 OpenResty 的 IPv4 TCP 443 监听从所有站点和默认站点移到回环，保留已有 IPv6 站点监听及 UDP 443 的现状，分别核对 `ss -4`、`ss -6`。Xray 新增一个显式绑定公网 IPv4 的 TCP 443 入站，启用 Vision 和 Reality，`target=127.0.0.1:8443`，`serverNames` 只设经确认的本站域名。先保留旧 Reality，承载流量从试用配置迁移。分发时保留现有订阅格式、新旧节点标识和端口的路由器直连覆盖。
4. 用独立网络和实际客户端验收真实 Reality 请求、普通浏览器 HTTPS、证书与 ALPN、HTTP 到 HTTPS 跳转、Headscale/DERP、Relay、Magpie、Hermes、Komari、面板反代、Sub-Store 下载及订阅缓存。确认 reload 后仍生效。若新入口或既有服务回归，先撤下新 Xray 443，再恢复 OpenResty 443 并 reload，复测旧节点。旧节点退役等客户端用新入口验收后另行安排。

### 4. 修复与验收同机 UDP

1. 复用四台已部署的 Xray Hysteria 2 入站和 DNS-01 证书。验证固定端口优先。对 `bitsflow` 以实测成功的 `<hy2-port>` 为首选统一运行配置、3x-ui 数据库和两族跳跃规则，先检查现有订阅的主端口；若仍发布 `<old-hy2-port>`，按旧客户端是否仍使用它安排临时兼容转发或订阅更新。精确调整 NAT 和持久化，避免覆盖 Docker 链。`raksmart`、`lightlayer` 从客户端路径、外层防火墙、订阅端口和证书顺次定位故障。
2. 外部客户端先用固定端口建立 TLS 验证通过的会话，再抽样跳跃范围两端和中间端口，最后用真实跳跃配置跑跨多个周期的请求。对同一 IPv4 查询服务分别经 Reality 和 Hysteria 2 测量出口 IP。客户端需要固定同机出口时，使用该台的 TCP/UDP 配对，另外验证 IPv6 或特殊路由流量。回退时恢复该机的原入站、NAT、持久化文件及订阅配置。[Hysteria 2 端口跳跃](https://v2.hysteria.network/docs/advanced/Port-Hopping/)

## 另行确认的可选项

- **保留网站访客地址**。先在并行测试监听成对启用 Xray Reality `xver: 1` 与 OpenResty `listen 127.0.0.1:8443 ssl proxy_protocol;`。有需要的站点仅信任回环 `set_real_ip_from 127.0.0.1;` 与 `real_ip_header proxy_protocol;`，Cloudflare 真实访客地址按每站受信链重新验证。常规本地 TLS 探测须走带 PROXY 头的入口，或另备一个单纯 TLS 的本地监听。等用户确认再改线上。
- **仅密钥登录 SSH**。目前四台都启用公钥和密码登录。另行获批后先用 `ssh -o BatchMode=yes <profile> true` 验证密钥，再用 `PasswordAuthentication no`、`KbdInteractiveAuthentication no`、`PermitRootLogin prohibit-password` 配置密钥登录。通过 `sshd -t` 和 `sshd -T` 检查后，保留独立会话，执行 `systemctl reload ssh`，重新连接验收。日常可以由固定可信出口访问 SSH，确保供应商控制台仍可用。地域来源限制单独评估。
