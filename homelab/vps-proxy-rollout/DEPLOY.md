# 新环境部署手册

目标是让 Reality 接管公网 IPv4 的 TCP 443，让普通 HTTPS 回落到本机 Web 服务，并提供同机 Hysteria 2 和私有的面板管理通路。每次只变更一台。先向操作者汇报只读审查结果和切换窗口，确认域名、影响范围、出口策略与回退后再写入配置。

## 1. 只读审查并记录基线

把实际取值、证书和订阅保存在受限的运维记录中。提交到仓库的审查结果只用 SSH 别名和占位符。

| 要确认的项目 | 检查方法与输出 |
| --- | --- |
| 主机和独立管理通路 | 用 SSH profile 关联正确主机和 SSH 端口。核对主机密钥、系统版本和服务来源。测试独立管理会话或供应商控制台，确认会话的出口未经过正在变更的节点。OpenWrt 有重复 LAN 地址时，按 Tailnet 地址和 SSH 主机密钥核对设备 |
| 端口和防火墙 | `ss -lntup` 分别核对 IPv4/IPv6 上的 TCP 80、TCP/UDP 443、候选回落端口、管理、订阅、旧代理和 Hysteria 2。用 `nft list ruleset` 或现有 iptables 工具核对活跃规则、持久化来源、云防火墙和 UDP 转发目标 |
| Web 服务 | 查 1Panel Compose 与 `docker inspect`，确认 host 或 bridge。查配置和挂载里的所有站点、默认站点、TCP/QUIC 监听、反代、跳转、证书路径、续签和 reload。记录 Headscale/DERP、Relay、管理反代、订阅聚合等在用服务及其正常应答 |
| DNS 与证书 | 用 `dig +short A`、`dig +short AAAA` 核对候选域名。Reality 使用证书覆盖、由自己管理、解析到本机且关闭 CDN 代理的域名。检查证书到期、TLS 1.3、ALPN 和续签方式 |
| 3x-ui 与 Xray | 记录实际运行版本、服务单元、面板 TLS 与绑定、订阅监听、启用的入站、协议、流控、目标、SNI 和出口规则。SQLite 读取要包含 WAL。对照持久化模板、运行配置和实际套接字。检查旧 UDP 端口是否仍在订阅或 NAT 中 |
| 客户端 | 列出 v2rayN、v2rayNG、daed、Passwall2 等实际内核版本、订阅链、在用分组、直连例外及备用节点。分别从日常网络和独立网络实测现有代理的 HTTPS 与 IPv4 出口。记录原有故障，作为切换后的对照 |

基线至少列出计划占用的端口、要迁移的站点、每个真实测试的来源和预期结果。缺少 3x-ui 或 Web 服务时，按官方说明部署适用架构的稳定版本，先在非冲突端口验证服务。没有 1Panel 的主机可直接用 nginx。迁移前保留旧入站或至少一个已测通的跨 VPS 节点。 [3x-ui](https://github.com/MHSanaei/3x-ui) · [Xray 发布页](https://github.com/XTLS/Xray-core/releases) · [nginx 安装说明](https://nginx.org/en/linux_packages.html)

在 root 专用目录做变更前快照。用 SQLite Backup API 或 `.backup` 保存一致的 3x-ui 数据库并做完整性检查，同时保存生效的模板和配置、Xray 二进制和 Geo 数据、Web 配置及挂载、续签钩子、防火墙和路由器或订阅配置。记录每项的恢复路径和重启顺序。新环境按本节准备备份和恢复操作，现有实例的专用脚本见文末。

## 2. 建立管理通路

1. 确认原生 3x-ui 的管理端口、证书域名与登录路径。先从独立管理路径建立 SSH 转发，并实际打开登录页。手动方式见项目首页。复测已存在的 1Panel HTTPS 反代。
2. 把 3x-ui 管理端口绑定到 `127.0.0.1`。1Panel 按反代需求绑定回环，或用原有防火墙限制原生管理端口的非回环访问。仅修改对应端口的 IPv4/IPv6 规则及其持久化配置，保留 SSH、Docker 和其他服务的规则。
3. 从外部复测原生管理端口关闭。从 SSH 转发复测登录，明确 HTTPS 订阅仍可下载。重启 x-ui 后再验收一遍。SSH 若经正在维护的 VPS 出口转发，重启它会断开该路径上的多个面板隧道。先换出口，恢复后再重建转发。

**浏览器接入示例。** 在客户端已建立 SSH 转发时，可以用 Chromium 系浏览器启动独立配置，将证书域名解析到本地。把保留域名 `panel.example.com`、示例端口和路径替换成私有部署值。创建桌面快捷方式时，让它先启动或重建 SSH 转发，再使用这组浏览器参数。新电脑要先验证原生 Windows 或 WSL 到本机回环转发是否可达。

```powershell
# 在 PowerShell 中运行。仅为这个 Edge 配置映射证书域名。
$edge = Join-Path ${env:ProgramFiles(x86)} 'Microsoft\Edge\Application\msedge.exe'
$profile = Join-Path $env:LOCALAPPDATA 'VPS-3xUI-Private'
& $edge "--user-data-dir=$profile" '--no-proxy-server' `
  '--host-resolver-rules=MAP panel.example.com 127.0.0.1' `
  'https://panel.example.com:28443/private-path/'
```

也可从客户端验证原证书和 HTTP 应答。变量使用当前私有记录，`PANEL_PATH` 包含开头的斜杠。

```bash
curl --noproxy '*' --max-time 8 -fsS -o /dev/null -w '%{http_code}\n' \
  --resolve "$PANEL_HOST:$PANEL_PORT:127.0.0.1" \
  "https://$PANEL_HOST:$PANEL_PORT$PANEL_PATH"
```

## 3. 暂存 TLS 后端和 Xray 配置

1. 在 `127.0.0.1:8443` 或经确认的备用回环端口并行建立 TLS 后端。host 模式下在 Web 服务的所有现有 IPv4 HTTPS 站点与默认站点中配置对应的回环监听。bridge 模式下可映射宿主机回环到容器 TLS 端口。从 Xray 所在的网络命名空间验证可达性。不开公网后端端口。用新增站点验证拟用 Reality 域名。
2. Web 站点保留证书、TLS 版本、ALPN、SNI、HTTP 跳转、WebSocket/Upgrade 和反代。80 上需要 HTTPS 的站点按协议跳转，并保留 ACME 校验。反代使用客户端看到的 HTTPS 端口 443，例如对应的 `X-Forwarded-Port`，避免把回落端口写入跳转或请求头。原有 IPv6 监听和 UDP 443 单独处理。若站点依赖真实来源 IP，先解决接入路径与受信头部。`xver=0` 时到达 Web 服务的来源通常是回环。采用 PROXY protocol 时，发送端和回落监听必须一起配置，并单独验收。
3. 校验拟用 Xray 版本及发布包摘要，在暂存配置上运行 `xray run -test -c "$STAGED_CONFIG"`。通过当前版本支持的 3x-ui 接口或面板写入持久化配置，用生成的运行配置确认变更。先准备 VLESS/TCP 测试入站，安全传输选 Reality，流控选 `xtls-rprx-vision`，`target` 指向回环 TLS 后端，`serverNames` 填候选域名。新入站生成自己的 UUID、Reality 密钥对与 Short ID，客户端使用对应公钥和 Short ID。保留已有最低版本限制与强制握手行为，用实际客户端内核与指纹验证兼容性。
4. 对照已有规则，只在设计范围内修改代理出口。保留本地 API、私网和 BT 处理。按确认的域名后缀配置直连例外，健康检查例外限制到确切 IP、协议和端口。后面的 `geoip:cn` 阻断按出口 IP 匹配。若采用 `IPOnDemand`，同时测直接 IP、待解析域名及 DNS。整类域名 `geosite:cn` 曾误拦正常应用与移动端连通性测试，当前配置采用 IP 规则和已确认的例外。 [daed 分流顺序](../openwrt-dns-daed/docs/principles.md)

用本地 TLS 测试和普通 HTTPS 请求核对每个站点。临时测试入站使用非冲突端口，验证实际认证和多个 SNI 回落后，由变更窗口接管正式端口。

```bash
# ORIGIN_DOMAIN 为拟用 Reality 域名，值来自私有部署记录。
openssl s_client -connect 127.0.0.1:8443 -servername "$ORIGIN_DOMAIN" \
  -verify_hostname "$ORIGIN_DOMAIN" -verify_return_error -tls1_3 -alpn h2 </dev/null
curl --noproxy '*' --max-time 8 -sS -o /dev/null \
  --connect-to "$ORIGIN_DOMAIN:443:127.0.0.1:8443" \
  -w '%{http_code} %{http_version}\n' "https://$ORIGIN_DOMAIN/"
```

## 4. 切换 TCP 443 并配置 UDP

1. 确认测试通过后，验证 Web 配置语法并 reload，释放公网 IPv4 的 TCP 443。以 `ss -4 -lnt` 核对 TCP，再用 `ss -6 -lnt` 与 `ss -lnu` 核对原 IPv6/UDP 分工。将 Reality 入站绑定到指定的公网 IPv4 和 TCP 443，保留回环 TLS 目标。核对每个旧站点及 HTTPS 重定向。检查 1Panel 保存、重载或更新时是否会重新占用 443。
2. 优先复用现有 Hysteria 2。新环境创建独立 UDP 入站，配置 v2 协议、可验证的 TLS 证书和独立认证。启用 Salamander 时，服务端与各客户端参数保持一致。先发布能正常连通的固定端口，核对数据库、真实监听、源订阅和防火墙。旧主端口仍有人使用时，可按实际情况保留限于该端口的兼容转发。
3. 需要跳跃时，通过现有防火墙将专用 UDP 范围转发到该固定监听端口，并保存到现有持久化机制中。确认端口范围、跳跃配置、固定端口和 SNI 在源订阅与转换后的客户端配置中一致。仅在部署使用 IPv6 时同步验证 IPv6 UDP。先测固定端口，再抽样范围两端和中间，最后运行跨多个跳跃周期的请求。检查服务端目标端口和实际返回的 IPv4。 [Hysteria 2 端口跳跃](https://v2.hysteria.network/docs/advanced/Port-Hopping/)
4. 把证书加载纳入续签流程。确认 Web 服务与 Hysteria 2 使用的证书来源、挂载或镜像路径。私钥只供必要服务读取。使用现有正式证书演练 reload，并确认替换证书后的新进程仍提供预期证书。复测同一公网 443 上的所有站点。

## 5. 分发与验收

客户端地址优先填该机公网 IPv4，TLS/Reality SNI 填真实证书域名。先检查源订阅，再检查聚合端、每种客户端导入后的运行配置和实际使用的分组。Sub-Store 档案中用 `sni` 或 `sniRegex` 匹配仅在 SNI 出现的域名，刷新规则和聚合缓存后确认节点实际可见。daed 为节点端点配置精确的 IP、协议和端口。同 IP 的 Headscale 等服务保留域名规则优先级，DNS 的代理承载跨 VPS。Passwall2 同时核对实际内核和选用的节点。 [Sub-Store 安装与缓存](../substore-operators/README.md) · [daed 新环境重放](../openwrt-dns-daed/docs/reapply-runbook.md)

| 验收项 | 通过标准 |
| --- | --- |
| 管理与订阅 | 独立 SSH 管理通路可用，公网原生面板端口关闭，经 SSH 转发的 3x-ui 登录页和现有 1Panel HTTPS 反代可用。源订阅和聚合订阅均能下载、解析并包含预期节点 |
| 网站 | 日常网络和独立网络均可用预期证书访问。核对 HTTP 到 HTTPS 跳转、ALPN、每个已有服务及其基线响应。Headscale 检查 `/health` 和 `/derp/probe`，以及实际使用的协议升级路径 |
| Reality 与 Hysteria 2 | 以真实认证分别发起经代理的 HTTPS 请求并核对 IPv4 出口。对 UDP 核对固定端口和跳跃周期。Windows、Android 和路由器分别记录结果，未连到的设备标记待验收 |
| 分流与故障转移 | 实测允许访问、应阻断的目标和 DNS 健康检查。检查实际选用的分组和故障转移。确认停止旧入口后客户端可使用已验收的其他入口 |
| 持久化 | 重新加载配置后复查监听、证书、站点、面板、订阅和防火墙。记录主机别名、测试来源、结果及备份位置，日志与订阅凭据留在私有环境 |

TCP SYN 到不了服务器时，比较日常网络和独立网络，并用服务端抓包核对路径。本地 TUN/TPROXY 可能先应答探测连接。Hysteria 2 若表现为服务器收到了 QUIC Initial 并发送回复、客户端却持续重传，优先对照 UDP 回程和不同线路。由某一端口或某一网络测通，只能证明该条路径可用。

## 6. 退役和回退

验收通过并确认现有流量具备备用线路后，逐台备份和停用或删除旧 Reality 入站。复测旧端口从外网关闭，源订阅只保留计划中的节点，443、UDP、网站和面板都正常。待客户端与 Sub-Store 完成订阅刷新后再清理遗留的路由引用。

| 何时回退 | 怎么恢复 |
| --- | --- |
| TCP 443 或原站点回归 | 先撤下新 Reality 443，再恢复并 reload 原 Web 443，恢复相关证书钩子。复测站点、旧代理和订阅 |
| UDP 或出口规则回归 | 先恢复该台变更前的入站、Xray 模板、NAT 与持久化规则。重新加载并复测固定 UDP 入口、受影响的应用和 DNS |
| 管理连接失效 | 用独立 SSH 或控制台确认服务状态，按备份恢复管理监听、反代或相关防火墙规则。逐项收口前先验证新的私有通路 |
| 旧入站退役后需要恢复 | 用退役前备份定向恢复旧入站、对应的模板引用及订阅。若整库恢复，先保护备份后发生的账户、配置与统计变更 |

现有四台的部署备份在各 VPS 的 `/root/vps-proxy-rollout-20261118/`。该目录下的 `rollout.py <profile> rollback-cutover` 恢复原 TCP 443，`rollback-harden` 用于恢复原管理和路由配置，应在切换已回退后执行。最近一次旧入站备份使用 `retire-<旧端口>-<时间戳>/` 命名。`retire.py <profile> rollback <时间戳>` 会恢复该次退役前的 3x-ui 数据库。路由器的备份在各自同名目录内。上述脚本是现有实例的运维工具，新环境按本手册准备自己的备份和恢复操作。
