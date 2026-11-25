# VPS 代理配置教程

## 背景与取舍

适用于在同一台 VPS 上同时运行网站和代理的部署。这里的 `raksmart`、`bitsflow`、`lightlayer` 和 `lightlayer-zh` 都是 SSH 别名。`nosla` 已于 2026-11-20 退役：3x-ui 不可达，源订阅与 MySub 成员当日删除，规则 gist 中的 nosla 条目也已于 2026-11-23 清理。3x-ui 是管理 Xray 的面板，Xray 负责接收代理连接，nginx 或 OpenResty 负责网站。

原来，网站占用公网 TCP 443，Reality 节点另用一个端口，管理面板也能从公网直连。这套方案让网站和 Reality 共用公网 IPv4 的 TCP 443，用同机 Hysteria 2 提供 UDP 备线，并把 3x-ui 的管理入口收回 VPS 本机。这样可以保留网站原来的 HTTPS 地址，让代理客户端统一使用 TCP 443，并减少公网管理入口。

这也带来三项运维取舍。**3x-ui 需要经 SSH 隧道访问。** 公网 IPv4 网站现在也经过 Xray，重启 Xray 可能让这些网站短暂中断，Web 后端默认看到的是回环来源地址。Hysteria 2 能应对部分 TCP 线路故障，同一台 VPS 或同一个公网 IP 出故障时则需要其他 VPS。

从空白机开始，可以填写 [Agent 部署 Prompt](AGENT-PROMPT.md)，让 Agent 从 SSH 初始化做到两条节点的外网验收。手动部署可从[安装 3x-ui 并创建首个入站](DEPLOY.md#空白-vps-安装和准备)开始。已有网站和节点时，先做[只读审查](DEPLOY.md#1-只读审查并记录基线)。

## 访问 3x-ui

### 为什么要建立 SSH 隧道

3x-ui 只监听 VPS 自己的 `127.0.0.1`，所以旧公网地址直接打开会失败。SSH 登录后，VPS 端的 SSH 服务可以连接本机面板。`ssh -L` 把这条连接转发到你电脑的一个本地端口。浏览器访问这个本地端口，SSH 把流量加密传到 VPS，再交给 3x-ui。

### 为什么用专用浏览器窗口

浏览器要同时知道两件事。**连到哪里**是你电脑上的 `127.0.0.1`，因为隧道入口在那里。**证书属于谁**仍是面板域名，例如 `panel.example.com`。直接在地址栏填 `https://127.0.0.1:28443/` 会让浏览器用 IP 核对证书，证书上的域名就对不上了。

所以地址栏保留面板域名，启动浏览器时单独告诉它「这个域名连到本机，直接走隧道」。专用窗口用自己的数据目录，也就是这里说的浏览器 profile。这样 Edge 或 Chrome 会启动一份带这条规则的浏览器实例，日常窗口继续使用平时的 DNS 和代理。面板里仍用原账号登录。已经给这个域名配置了本地解析和代理直连时，日常窗口也能打开。 [浏览器数据目录说明](https://chromium.googlesource.com/chromium/src/+/HEAD/docs/user_data_dir.md)

### Windows 上用引导脚本

在仓库的 `homelab/vps-proxy-rollout` 目录下，从 WSL 终端运行下面的命令。脚本默认使用 `Ubuntu` 里的 SSH 别名，并优先打开 Windows 上的 Edge，找不到时再用 Chrome。使用其他 WSL 发行版时，在命令末尾加 `-Distro <发行版名>`。

```sh
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "$(wslpath -w "$PWD/Open-3xUI.ps1")"
```

1. 第一次运行时，如果找到了旧 Edge 快捷方式的面板清单，按回车即可沿用。导入会跳过已经退役的 `nosla`。
2. 输入面板编号，或按 `N` 添加面板。脚本会询问 WSL 里的 SSH 别名、原 HTTPS 登录地址和 VPS 上的面板管理端口。输入已有别名可更新该项。旧清单里没有 `lightlayer-zh`，可以用这个方式添加。
3. 脚本会分配本地端口、建立隧道、验证 HTTPS 并打开专用窗口。用完后回到终端按回车关闭本次隧道。连接成功的配置保存在本机的 `%LOCALAPPDATA%\VPS-3xUI-Guide\panels.json`，下次选编号即可。

旧桌面快捷方式调用 `%LOCALAPPDATA%\VPS-3xUI\Open-Panels.ps1`。这个 PowerShell 脚本读取同目录的 `panels.json`，用 WSL 的 `~/.local/bin/vps-3xui-tunnels` 批量启动 SSH 转发，再用同目录的 `BrowserProfile` 打开 Edge。新脚本沿用清单时会把它存到自己的目录。 [查看引导脚本](Open-3xUI.ps1)

### 换电脑后手动连接

1. 准备 OpenSSH 客户端和自己的 SSH 凭据。先用 `ssh lightlayer-zh` 确认能登录。新电脑还没有 SSH 别名时，可从私有运维记录取主机、用户和端口，在命令里用 `-p 2222 user@vps.example.com` 代替 `lightlayer-zh`。这里的地址和端口都是示例。
2. 在客户端终端运行下方命令。第一个 `28443` 是你电脑上的空闲端口，第二个 `28443` 要换成 VPS 的实际 3x-ui 管理端口。保留终端运行，按 `Ctrl+C` 就会关闭隧道。

   ```sh
   ssh -o ExitOnForwardFailure=yes -NT -L 127.0.0.1:28443:127.0.0.1:28443 lightlayer-zh
   ```

3. 用证书上的域名、本地转发端口和原登录路径打开面板，例如 `https://panel.example.com:28443/private-path/`。让浏览器把面板域名连到本机，并绕过代理。首次配置或排障时，可以先用 `curl` 验证。 [浏览器命令](DEPLOY.md#用浏览器打开登录页) · [终端验收](DEPLOY.md#先用终端验证隧道和-https)

如果维护期间面板一起失联，先确认 SSH 连接是否还在。比如 SSH 流量经 `raksmart` 出口时，重启该机的 x-ui 可能断开多条隧道。维护前准备独立管理通路，服务恢复后重新运行转发命令。

## 访问 1Panel

带 1Panel 的三台 VPS 用 `panel-<主机别名>` 这样的一级子域名访问，并经 CDN 代理：请求先到 CDN 边缘，再到源站 443 的 Reality 回落、OpenResty 的回环 8443，最后反代到面板端口。zone 的 CDN 通用证书只覆盖一级子域，`panel.<节点域名>` 这类两级名字开不了代理，2026-11-24 已统一改为一级子域。`lightlayer` 未装 1Panel。实际主机名、端口与入口路径保存在各机的私有运维记录中。

三台面板都已开启二次验证（TOTP），并绑定域名（BindDomain）。绑定只接受 Host 为该域名的 API 请求，按 IP 或其他域名直达面板端口的请求会被拦截。域名在公开 DNS 与证书日志里，所以这一层是纵深防御，真正的边界是 CDN、回环监听加防火墙。

在对应机器上查看验证器密钥并添加到验证器应用后，才能登录面板：

```sh
ssh <主机别名> cat /root/1panel-mfa-enroll.txt
```

设备丢失时在机上执行 `1pctl reset mfa` 关闭二次验证，忘记域名绑定值时执行 `1pctl reset domain`。

### 登录被锁时

1Panel 对同一来源的登录失败计数：失败 10 次锁 5 分钟，首次失败后要求验证码。它只认 TCP 对端，而面板在 OpenResty 后面，公网请求和普通隧道在它眼里都是回环地址。所以任何人连续失败 10 次，都会把这些入口一起锁住，并且可以反复触发。已登录的会话不受影响。

源站的 `limit_req` 按回环地址计数，是同一个共用的桶，只会多一个把登录打成 429 的途径。按真实来源的限速只能在 CDN 侧做。

公网入口不可用或被锁时，改走 SSH 隧道，并把**目标写成本机的公网 IPv4**，不要写 `127.0.0.1`。这样面板看到的对端是公网地址，不会被公网上的失败连坐。1Panel 比对 BindDomain 时忽略端口，本地端口可以任选。先用 `ssh <主机别名>` 确认能登录，再把下面的占位符换成实际值。

```sh
HOST='raksmart'; ORIGIN_IP='<本机公网 IPv4>'; PANEL_PORT='<面板端口>'; LOCAL_PORT=28443
ssh -o ExitOnForwardFailure=yes -NT -L "127.0.0.1:${LOCAL_PORT}:${ORIGIN_IP}:${PANEL_PORT}" "$HOST"
```

保持隧道运行，另开终端验证。收到预期登录页的状态码后再打开浏览器。面板未开 HTTPS 的机器把 `https` 换成 `http`。

```sh
PANEL_HOST='panel-<主机别名>'; PANEL_SCHEME='https'; PANEL_PATH='<入口路径>'
curl --noproxy '*' --max-time 8 -fsS -o /dev/null -w '%{http_code}\n' \
  --resolve "${PANEL_HOST}:${LOCAL_PORT}:127.0.0.1" "${PANEL_SCHEME}://${PANEL_HOST}:${LOCAL_PORT}${PANEL_PATH}/"
```

浏览器按[手动指定域名映射的方式](DEPLOY.md#用浏览器打开登录页)打开，把示例域名换成 `panel-<主机别名>`，地址带本地端口。

## 配置如何工作

| 配置 | 用途 | 影响范围 |
| --- | --- | --- |
| Reality 使用公网 IPv4 的 TCP 443 | 同端口承载 Reality 与普通 HTTPS。通过认证的客户端进入 Xray，网站访问由本机 Web 服务处理 | 原有非 443 Reality 入站已退役。旧订阅缓存或手工导入节点需要刷新或换用新节点 |
| Web 的 IPv4 HTTPS 监听移到 `127.0.0.1:8443` | 把公网 IPv4 TCP 443 交给 Xray，同时保留网站的域名、证书和反代 | 由站点按 SNI 提供证书和响应。现有 TCP 80、IPv6 网站监听和 UDP 443 按原部署保留。默认未传递访客源 IP，依赖源 IP 的访问控制、限速和日志需要单独验收 |
| 同机 Hysteria 2 | 用 UDP 作为 TCP 线路的备用通道，并复用同机 IPv4 出口 | 固定端口和跳跃范围需要分别实测。切换协议时应用连接会重建。整台 VPS 或公网 IP 故障仍需切到其他 VPS |
| 管理面板收口 | 通过回环和防火墙限制原生端口，减少公网管理入口 | 3x-ui 走 SSH 转发。现有 1Panel HTTPS 反代继续连回本机。HTTPS 订阅使用独立监听 |
| Xray 出口规则 | 保留本地 API、私网与 BT 规则，按 `geoip:cn` 匹配出口 IP，并保留已确认的域名和 UDP 健康检查例外 | 规则作用于已进入 VPS 的代理流量。客户端负责把应该直连的流量留在本地。各条规则的用途和取舍见[3x-ui 路由说明](ROUTING.md) |

三台 VPS（`raksmart`、`bitsflow`、`lightlayer-zh`）使用 1Panel OpenResty，`lightlayer` 使用 nginx。1Panel 新建或保存站点、更新 OpenResty 或续签证书后，复查站点仍使用回环 8443、原有 IPv6 监听正常、Xray 仍独占 IPv4 TCP 443。证书的续签需要让 Web 服务和 Hysteria 2 都加载到新证书。

`lightlayer-zh`（2026-11-23 上线，CN 优化线路）按同一手册部署，几点差异：节点域名沿用机器自带的域名（泛域名证书，acme.sh `dns_cf` 续签），不遵循 `<主机别名>.haoqi90.top` 的命名；原 Hysteria 2 入站改名 `lightlayer-zh-hysteria2`（UDP 端口、跳跃范围与 Salamander 不变）；原非 443 的 Reality 入站于 2026-11-24 退役，现网只有 TCP 443 的 Reality 与同机 Hysteria 2。上线同日加装 1Panel v2.5.1 对齐 raksmart/bitsflow：OpenResty 应用经 agent.sock 自动化安装并接管回环 8443 回源（rollout 续签链路已切到 OpenResty，apt nginx 已停用）；面板管理端口仅回环/SSH 隧道可达（防火墙 REJECT 非回环），公网入口是经 CDN 代理的 1Panel 受管站点，使用面板自己签发的证书。

带 1Panel 的三台（`raksmart`、`bitsflow`、`lightlayer-zh`）的面板访问方式见[访问 1Panel](#访问-1panel)。

用 1Panel 以 DNS 账号方式签发或续期证书前，确认 `_acme-challenge.<域名>` 已有一条常驻占位 TXT。1Panel 的传播预检在记录创建后立即查询，过早拿到的否定答案会被公共解析器缓存 30 分钟，预检可能卡满 30 分钟后失败；名字始终可解析就不会产生这种缓存。现有五条记录覆盖三台上全部以 DNS 方式签发的证书，acme.sh 签发的证书不受影响。

## 选节点和排查

- **刷新订阅后缺少 TCP 443 节点**，先对照 3x-ui 源订阅，再检查 Sub-Store 分组和缓存。Reality 链接用 IPv4 连接，域名在 SNI 字段里，节点档案规则可用 `sni` 或 `sniRegex`。导入 daed 或 Passwall2 后还要核对实际使用的节点组。[Sub-Store 订阅说明](../substore-operators/README.md)
- **新节点能导入但连不上**，在实际客户端网络上发起经代理的 HTTPS 请求，再与独立网络结果对照。TCP 探测可能被本机 TUN 或路由器透明代理提前应答。UDP 跳跃也需要覆盖多个周期。只从服务器或另一台 VPS 测通，仍需验证日常使用的线路。
- **选择备用线路**。此前 `raksmart` 在受测 Windows 和家庭线路上双协议可用。受测线路上的 `lightlayer` 可用 Hysteria 2，Reality TCP 443 存在超时；`lightlayer-zh` 的 Reality TCP 443 于 2026-11-23 在家庭线路实测经代理出网正常（其 Hysteria 2 沿用原有入站、仅改名，未重测）。`bitsflow` 的 Reality 可用，Hysteria 2 在不同线路和端口上的结果有差异。线路变化后以实测为准。Android 的 v2rayNG 实机验收仍待完成。

备份位置、逐台回退顺序和故障判定标准都在[新环境部署手册](DEPLOY.md)。
