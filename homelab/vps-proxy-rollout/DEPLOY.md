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

基线至少列出计划占用的端口、要迁移的站点、每个真实测试的来源和预期结果。已有业务的主机要保留旧入站或至少一个已测通的跨 VPS 节点。空白机记录将使用的公网 IPv4、域名和端口，并按下一节准备服务。

在 root 专用目录备份已有配置。用 SQLite Backup API 或 `.backup` 保存一致的 3x-ui 数据库并做完整性检查，同时保存生效的模板和配置、Xray 二进制和 Geo 数据、Web 配置及挂载、续签钩子、防火墙和路由器或订阅配置。记录每项的恢复路径和重启顺序。空白机完成安装和基本验证后做第一份配置基线。现有实例的专用脚本见文末。

### 空白 VPS 安装和准备

如果由 Agent 执行，使用 [Agent 部署 Prompt](AGENT-PROMPT.md)，从首次 SSH 登录和 BBRv3 开始。

1. 先确认 SSH 或供应商控制台可用。给节点域名做指向该 VPS 的直连 A 记录，准备节点域名和面板域名所需的可信 TLS 证书。节点域名可以复用于 Web 后端和 Hysteria 2，面板也可使用同一份覆盖该域名的证书。已有 ACME 流程可以复用。用 DNS-01 可避免占用 TCP 80，使用 HTTP-01 时先确认校验能到达现有 Web 服务或 ACME 监听。
2. 在云防火墙或主机防火墙保持 SSH 可达，并限制面板管理端口的公网访问。选择与客户端兼容的 3x-ui 正式版。下面以现网用过的版本为例，先下载并审阅官方安装脚本，再执行。安装提示中选择未占用的管理端口，保存生成的账号和私有登录路径。已有证书时可选自定义证书路径。 [3x-ui 安装说明与发布包校验](https://github.com/MHSanaei/3x-ui/tree/v3.11.0)

   ```bash
   version=v3.11.0
   installer=$(mktemp)
   curl -fsSL "https://raw.githubusercontent.com/MHSanaei/3x-ui/$version/install.sh" -o "$installer"
   less "$installer"
   sudo bash "$installer" "$version"
   ```

3. 用 `systemctl is-active x-ui` 检查服务。先经 SSH 隧道进入面板，再按第 2 节收口管理监听和验收。面板负责保存入站，Xray 由 3x-ui 管理。记录内核版本、证书路径和续签重载方式。
4. 安装 Web 服务。Debian 空白机可用 `sudo apt install nginx`，将下面的站点片段放到 `/etc/nginx/conf.d/reality-origin.conf`，确认主配置在 `http` 中包含该目录。替换域名、证书路径并确认安装包提供 HTTP/2 模块。运行 `sudo nginx -t && sudo systemctl reload nginx`，再用第 3 节的 TLS 测试确认回环目标。已有 1Panel 的主机按第 3 节继续使用原来管理的 OpenResty。 [nginx 安装说明](https://nginx.org/en/linux_packages.html)

```nginx
server {
    listen 127.0.0.1:8443 ssl;
    server_name vpn.example.com;
    ssl_certificate /path/to/fullchain.pem;
    ssl_certificate_key /path/to/privkey.pem;
    ssl_protocols TLSv1.2 TLSv1.3;
    http2 on;
    location / {
        default_type text/plain;
        return 200 "Service online\n";
    }
}
```

`http2 on;` 适用于 nginx 1.25.1 及以上。较早版本在对应的 `listen` 上加 `http2`。TLS 后端验证完成后，按[入站字段对照](#新建-reality-和-hysteria-2-入站)创建节点。若公网 TCP 443 空闲，可直接让首个 Reality 入站使用这个端口。未部署旧节点的主机跳过旧入站退役。

## 2. 建立管理通路

1. 确认原生 3x-ui 的管理端口、证书域名与登录路径。先从独立管理路径建立 SSH 转发，并实际打开登录页。 [SSH 转发命令与参数](README.md#换电脑后手动连接)
2. 把 3x-ui 管理端口绑定到 `127.0.0.1`。1Panel 按反代需求绑定回环，或用原有防火墙限制原生管理端口的非回环访问。仅修改对应端口的 IPv4/IPv6 规则及其持久化配置，保留 SSH、Docker 和其他服务的规则。
3. 从外部复测原生管理端口关闭。从 SSH 转发复测登录，并检查现有 1Panel HTTPS 反代和 HTTPS 订阅。重启 x-ui 后再验收一遍。SSH 若经正在维护的 VPS 出口转发，重启它可能断开该路径上的多个面板隧道。先换出口，恢复后再重建转发。
4. 1Panel 面板开启二次验证并绑定面板域名。它的登录失败锁定只按 TCP 对端计数，放在反向代理后面时所有来源都是回环地址，任何人都能触发全局锁定；被锁时的访问方式见 [README](README.md#登录被锁时)。

### 先用终端验证隧道和 HTTPS

保持 `ssh -L` 运行，在客户端另开终端测试。这里的 `28443` 是 SSH 命令左侧的本地端口。`--resolve` 让这一次请求连接本机，同时保留 URL 里的证书域名。把示例域名、端口和路径替换成自己的值。若隧道开在 WSL、Chrome 跑在 Windows，请在 PowerShell 中测试回环端口，确认 Windows 也能连通。

```sh
curl --noproxy '*' --max-time 8 -fsS -o /dev/null -w '%{http_code}\n' --resolve panel.example.com:28443:127.0.0.1 'https://panel.example.com:28443/private-path/'
```

```powershell
curl.exe --noproxy '*' --max-time 8 -fsS -o NUL -w '%{http_code}\n' --resolve panel.example.com:28443:127.0.0.1 'https://panel.example.com:28443/private-path/'
```

证书校验通过并收到预期登录页的状态码后，再打开浏览器。如果遇到证书域名不匹配，核对 URL 的域名与面板证书。出现 `404` 时检查登录路径。遇到连接拒绝或超时，先核对 SSH 会话、本地监听和 VPS 上的面板监听。

### 用浏览器打开登录页

日常访问 3x-ui 可以直接用 [Windows 引导脚本](README.md#windows-上用引导脚本)。手动连接时，地址栏保留证书域名，用 `--host-resolver-rules` 让浏览器把这个域名连到本机隧道，用 `--no-proxy-server` 让它直连。`--user-data-dir` 为这个窗口使用单独的数据目录，使这些启动参数只应用在专用浏览器实例上。 [为什么用专用浏览器窗口](README.md#为什么用专用浏览器窗口)

下面是在 Windows PowerShell 里手动打开 Edge 的例子。按实际安装位置、证书域名、本地端口和登录路径替换示例值。使用 Chrome 时，把 `$browser` 改为 `chrome.exe` 的实际路径。

```powershell
$panelHost = 'panel.example.com'
$localPort = 28443
$panelPath = '/private-path/'
$browser = "${env:ProgramFiles(x86)}\Microsoft\Edge\Application\msedge.exe"
$dataDir = Join-Path $env:LOCALAPPDATA "VPS-Panel-$panelHost"
Start-Process -FilePath $browser -ArgumentList @(
    "--user-data-dir=`"$dataDir`""
    "--host-resolver-rules=`"MAP $panelHost 127.0.0.1`""
    '--no-proxy-server'
    "`"https://${panelHost}:$localPort$panelPath`""
)
```

## 3. 暂存 TLS 后端和 Xray 配置

1. 在 `127.0.0.1:8443` 或经确认的备用回环端口并行建立 TLS 后端。host 模式下在 Web 服务的所有现有 IPv4 HTTPS 站点与默认站点中配置对应的回环监听。bridge 模式下可映射宿主机回环到容器 TLS 端口。从 Xray 所在的网络命名空间验证可达性。不开公网后端端口。用新增站点验证拟用 Reality 域名。
2. Web 站点保留证书、TLS 版本、ALPN、SNI、HTTP 跳转、WebSocket/Upgrade 和反代。80 上需要 HTTPS 的站点按协议跳转，并保留 ACME 校验。反代使用客户端看到的 HTTPS 端口 443，例如对应的 `X-Forwarded-Port`，避免把回落端口写入跳转或请求头。原有 IPv6 监听和 UDP 443 单独处理。若站点依赖真实来源 IP，先解决接入路径与受信头部。`xver=0` 时到达 Web 服务的来源通常是回环。采用 PROXY protocol 时，发送端和回落监听必须一起配置，并单独验收。
3. 校验拟用 Xray 版本及发布包摘要，在暂存配置上运行 `xray run -test -c "$STAGED_CONFIG"`。通过当前版本支持的 3x-ui 接口或面板写入持久化配置，用生成的运行配置确认变更。先准备 VLESS/TCP 测试入站，安全传输选 Reality，流控选 `xtls-rprx-vision`，`target` 指向回环 TLS 后端，`serverNames` 填候选域名。新入站生成自己的 UUID、Reality 密钥对与 Short ID，客户端使用对应公钥和 Short ID。保留已有最低版本限制与强制握手行为，用实际客户端内核与指纹验证兼容性。
4. 先保留 3x-ui 的本地 API 规则和适用的私网保护，再逐项决定 BT、地区 IP 策略和健康检查例外。根据客户端实际分流选择策略，空白机的基本节点无需导入既有办公域名列表。采用 `IPOnDemand` 时，用直接 IP 和待解析域名分别做允许、拒绝与 DNS 测试。 [3x-ui 路由说明](ROUTING.md)

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

### 新建 Reality 和 Hysteria 2 入站

在 3x-ui 的入站列表分别新增两个入站，并各添加一个客户端。下表用于核对字段。面板不同版本的字段名可能有差异，以生成的 Xray 配置和实际监听为准。

| 项目 | 常见独立节点 | 本教程的配置 |
| --- | --- | --- |
| Reality 协议与认证 | VLESS、TCP、Reality，客户端使用 UUID、公钥、Short ID 和兼容的指纹。使用 Vision 时两端流控一致 | 仍用这些字段。服务端创建 UUID、密钥对和 Short ID，流控选 `xtls-rprx-vision`。服务端保管私钥，客户端填写对应公钥 |
| Reality 监听和 TLS 目标 | 可选择一个空闲 TCP 端口及独立的 TLS 目标。目标常为另一站点 | 绑定 VPS 的公网 IPv4 TCP 443，`target` 填 `127.0.0.1:8443`，`serverNames` 填自己站点的主机名。客户端连接 IPv4，SNI 填同一主机名。普通 HTTPS 经过该本机后端，所以要一并验收网站 |
| Hysteria 2 与证书 | 独立 UDP 监听，Hysteria v2，客户端使用认证密码与 TLS SNI | 保持标准 Hysteria 2 字段。协议选择 Hysteria v2，选择未占用的固定 UDP 端口，使用节点域名的可信证书及私钥，为客户端设置独立认证密码。客户端填 VPS IPv4、固定端口及证书域名 SNI，并启用证书校验 |
| Hysteria 2 跳跃与混淆 | 固定端口可以直接使用，也可选用跳跃或 Salamander | 先验证固定端口。需要跳跃时再配置 UDP 转发和客户端范围。若启用 Salamander，两端使用相同参数。与 Reality 共用 VPS 和出网策略，TLS 及 UDP 监听各自独立 |

### 正式监听、跳跃与续签

1. 若 Web 服务已占用公网 IPv4 TCP 443，验证回环站点配置后 reload，释放原监听。空白机先用 `ss -4 -lnt` 确认端口空闲，已有网站的主机同时用 `ss -6 -lnt` 和 `ss -lnu` 核对原 IPv6/UDP 分工。将 Reality 入站绑定到指定的公网 IPv4 TCP 443，保留回环 TLS 目标。核对新站点及每个旧站点的 HTTPS 响应。检查 1Panel 保存、重载或更新时是否会重新占用 443。
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

`lightlayer-zh`（2026-11-23 上线）的备份与工具在 `/root/vps-proxy-rollout-20261123/`（适配版源码副本在仓库 `tools/rollout-lightlayer-zh.py`），`rollout.py` 由四台版本适配而来：域名固定为机器自带的节点域名、路由模板直接采用移除 `geosite:cn` 后的规则链、Xray 与先行各台统一为 26.11.9（`xray-new` 随目录预置）。`web`、`harden`、`cutover`、`publish` 与 `rollback-*` 语义不变。其 `cutover` 事务额外完成两项：把旧 Reality 入站置为 `exclude_from_sub`、把原 Hysteria 2 入站的备注改为 `lightlayer-zh-hysteria2`；`rollback-cutover` 会一并还原。旧 Reality 入站已于 2026-11-24 按第 6 节退役：同目录的 `retire.py`（由四台在用的版本适配）执行 `lightlayer-zh delete <时间戳>`，备份在 `retire-<旧端口>-<时间戳>/`，`retire.py lightlayer-zh rollback <时间戳>` 恢复退役前的数据库。面板管理端口已收口回环，订阅仍由原有的公网订阅端口提供，均使用同一泛域名证书。

`lightlayer-zh` 上线同日加装 1Panel v2.5.1（对齐 raksmart/bitsflow）：无交互安装（面板端口、随机入口路径与凭据存于机内 `/root/1panel-credentials`，面板端口防火墙 REJECT 非回环并持久化）；OpenResty 应用经 `/etc/1panel/agent.sock` 的 `POST /api/v2/apps/install` 安装（params 必须带全量 env，缺 `WEBSITE_DIR` 或 `PANEL_APP_PORT_HTTPS` 会分别导致卷路径为空、默认站点 `listen 0` 崩溃循环）；此后回环 8443 回源、`.vps-proxy-origin` 证书版本目录与续签换链（WRAP → `rollout.py renew`）全部迁至 OpenResty，apt nginx 已 disable 保留作回退。面板另以 `panel-<主机别名>` 这类一级子域名经 CDN 代理暴露：站点为 1Panel 受管站点，使用面板签发的证书（2026-11-24 由开不了代理的两级子域名迁来，迁移前经 agent.sock 建了 Cloudflare DNS 账户）。三台的迁移与回滚脚本在各自 `/root/panel-rename-<主机别名>.sh`。1Panel 应用商店目录在 agent 启动时若 CDN 超时会挂起，`systemctl restart 1panel-agent` 可重触发同步。
