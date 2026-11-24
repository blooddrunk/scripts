# VPS 代理配置教程

## 背景与取舍

适用于在同一台 VPS 上同时运行网站和代理的部署。这里的 `raksmart`、`bitsflow`、`lightlayer` 和 `lightlayer-zh` 都是 SSH 别名。`nosla` 已于 2026-11-20 退役：3x-ui 不可达，源订阅与 MySub 成员当日删除，规则 gist 中的 nosla 条目也已于 2026-11-23 清理。3x-ui 是管理 Xray 的面板，Xray 负责接收代理连接，nginx 或 OpenResty 负责网站。

原来，网站占用公网 TCP 443，Reality 节点另用一个端口，管理面板也能从公网直连。这套方案让网站和 Reality 共用公网 IPv4 的 TCP 443，用同机 Hysteria 2 提供 UDP 备线，并把 3x-ui 的管理入口收回 VPS 本机。这样可以保留网站原来的 HTTPS 地址，让代理客户端统一使用 TCP 443，并减少公网管理入口。

这也带来三项运维取舍。**3x-ui 需要经 SSH 隧道访问。** 公网 IPv4 网站现在也经过 Xray，重启 Xray 可能让这些网站短暂中断，Web 后端默认看到的是回环来源地址。Hysteria 2 能应对部分 TCP 线路故障，同一台 VPS 或同一个公网 IP 出故障时则需要其他 VPS。

从空白机开始可看[安装 3x-ui 并创建首个入站](DEPLOY.md#空白-vps-安装和准备)。已有网站和节点时，可从同一手册的[只读审查](DEPLOY.md#1-只读审查并记录基线)开始。

## 访问 3x-ui

### 为什么要建立 SSH 隧道

3x-ui 只监听 VPS 自己的 `127.0.0.1`，所以旧公网地址直接打开会失败。SSH 登录后，VPS 端的 SSH 服务可以连接本机面板。`ssh -L` 把这条连接转发到你电脑的一个本地端口。浏览器访问这个本地端口，SSH 把流量加密传到 VPS，再交给 3x-ui。

### 换电脑后手动连接

1. 准备 OpenSSH 客户端和自己的 SSH 凭据。先用 `ssh lightlayer-zh` 确认能登录。新电脑还没有 SSH 别名时，可从私有运维记录取主机、用户和端口，在命令里用 `-p 2222 user@vps.example.com` 代替 `lightlayer-zh`。这里的地址和端口都是示例。
2. 在客户端终端运行下方命令。第一个 `28443` 是你电脑上的空闲端口，第二个 `28443` 要换成 VPS 的实际 3x-ui 管理端口。保留终端运行，按 `Ctrl+C` 就会关闭隧道。

   ```sh
   ssh -o ExitOnForwardFailure=yes -NT -L 127.0.0.1:28443:127.0.0.1:28443 lightlayer-zh
   ```

3. 用 Chrome 打开面板。当前面板使用 HTTPS，地址需保留证书上的域名，再用本地端口和原登录路径访问，例如 `https://panel.example.com:28443/private-path/`。在本机让该域名解析到 `127.0.0.1`，浏览器才会走这条隧道，同时继续按原域名校验证书。打开登录页后使用原面板账号。 [Chrome 命令](DEPLOY.md#用-chrome-打开登录页) · [终端验收](DEPLOY.md#先用终端验证隧道和-https)

SSH 隧道由终端里的 `ssh` 独立建立，打开浏览器前就可以用 `curl` 验证。手册示例使用单独的 Chrome 数据目录，只为了把临时域名映射限制在那个窗口，避免影响日常浏览器。已有合适的本地域名解析和代理直连规则时，直接用平时的 Chrome 打开面板即可。偶尔维护时手工运行这些命令就够了，桌面快捷方式可在确认能连接后再做。

如果维护期间面板一起失联，先确认 SSH 连接是否还在。比如 SSH 流量经 `raksmart` 出口时，重启该机的 x-ui 可能断开多条隧道。维护前准备独立管理通路，服务恢复后重新运行转发命令。

## 访问 1Panel

带 1Panel 的三台 VPS 用 `panel.<节点域名>` 访问。记录只做 DNS 解析，请求经公网 443 的 Reality 回落到 OpenResty 的回环 8443，再反代到面板端口。这些记录不能开 CDN 代理，因为 zone 的 CDN 通用证书不覆盖两级子域。实际主机名、端口与入口路径保存在各机的私有运维记录中。

面板已绑定域名（BindDomain），只接受 Host 为该域名的 API 请求，按 IP 或其他域名直达面板端口的请求会被拦截。域名在公开 DNS 与证书日志里，所以这一层是纵深防御，真正的边界仍是回环监听加防火墙。

公网路径不可用时改走 SSH 隧道，并把面板域名解析到本机。1Panel 比对时忽略端口，本地端口可以任选。先用 `ssh <主机别名>` 确认能登录，再把下面的占位符换成实际值。

```sh
HOST='raksmart'; PANEL_PORT='<面板端口>'; LOCAL_PORT=28443
ssh -o ExitOnForwardFailure=yes -NT -L "127.0.0.1:${LOCAL_PORT}:127.0.0.1:${PANEL_PORT}" "$HOST"
```

保持隧道运行，另开终端验证。收到预期登录页的状态码后再打开浏览器。面板未开 HTTPS 的机器把 `https` 换成 `http`。

```sh
PANEL_HOST='panel.<节点域名>'; PANEL_SCHEME='https'; PANEL_PATH='<入口路径>'
curl --noproxy '*' --max-time 8 -fsS -o /dev/null -w '%{http_code}\n' \
  --resolve "${PANEL_HOST}:${LOCAL_PORT}:127.0.0.1" "${PANEL_SCHEME}://${PANEL_HOST}:${LOCAL_PORT}${PANEL_PATH}/"
```

浏览器按 [Chrome 的域名映射方式](DEPLOY.md#用-chrome-打开登录页)打开，把示例域名换成 `panel.<节点域名>`，地址带本地端口。忘记绑定值时在机上执行 `1pctl reset domain` 可解除绑定。

## 配置如何工作

| 配置 | 用途 | 影响范围 |
| --- | --- | --- |
| Reality 使用公网 IPv4 的 TCP 443 | 同端口承载 Reality 与普通 HTTPS。通过认证的客户端进入 Xray，网站访问由本机 Web 服务处理 | 原有非 443 Reality 入站已退役。旧订阅缓存或手工导入节点需要刷新或换用新节点 |
| Web 的 IPv4 HTTPS 监听移到 `127.0.0.1:8443` | 把公网 IPv4 TCP 443 交给 Xray，同时保留网站的域名、证书和反代 | 由站点按 SNI 提供证书和响应。现有 TCP 80、IPv6 网站监听和 UDP 443 按原部署保留。默认未传递访客源 IP，依赖源 IP 的访问控制、限速和日志需要单独验收 |
| 同机 Hysteria 2 | 用 UDP 作为 TCP 线路的备用通道，并复用同机 IPv4 出口 | 固定端口和跳跃范围需要分别实测。切换协议时应用连接会重建。整台 VPS 或公网 IP 故障仍需切到其他 VPS |
| 管理面板收口 | 通过回环和防火墙限制原生端口，减少公网管理入口 | 3x-ui 走 SSH 转发。现有 1Panel HTTPS 反代继续连回本机。HTTPS 订阅使用独立监听 |
| Xray 出口规则 | 保留本地 API、私网与 BT 规则，按 `geoip:cn` 匹配出口 IP，并保留已确认的域名和 UDP 健康检查例外 | 规则作用于已进入 VPS 的代理流量。客户端负责把应该直连的流量留在本地。各条规则的用途和取舍见[3x-ui 路由说明](ROUTING.md) |

三台 VPS（`raksmart`、`bitsflow`、`lightlayer-zh`）使用 1Panel OpenResty，`lightlayer` 使用 nginx。1Panel 新建或保存站点、更新 OpenResty 或续签证书后，复查站点仍使用回环 8443、原有 IPv6 监听正常、Xray 仍独占 IPv4 TCP 443。证书的续签需要让 Web 服务和 Hysteria 2 都加载到新证书。

`lightlayer-zh`（2026-11-23 上线，CN 优化线路）按同一手册部署，几点差异：节点域名沿用机器自带的域名（泛域名证书，acme.sh `dns_cf` 续签），不遵循 `<主机别名>.haoqi90.top` 的命名；原 Hysteria 2 入站改名 `lightlayer-zh-hysteria2`（UDP 端口、跳跃范围与 Salamander 不变）；原非 443 的 Reality 入站于 2026-11-24 退役，现网只有 TCP 443 的 Reality 与同机 Hysteria 2。上线同日加装 1Panel v2.5.1 对齐 raksmart/bitsflow：OpenResty 应用经 agent.sock 自动化安装并接管回环 8443 回源（rollout 续签链路已切到 OpenResty，apt nginx 已停用）；面板管理端口仅回环/SSH 隧道可达（防火墙 REJECT 非回环），同时经公网 443 的 Reality 回落提供登录页，站点证书直接引用 `.vps-proxy-origin/current` 随续签换链。

带 1Panel 的三台（`raksmart`、`bitsflow`、`lightlayer-zh`）的面板地址于 2026-11-23 统一为 `panel.<节点域名>`，`lightlayer` 未装 1Panel。这些记录只做 DNS 解析、不开 CDN 代理：客户端连到公网 443，经 Reality 回落到 OpenResty 的回环 8443，再由按 SNI 匹配的站点反代到面板端口。实际主机名、端口与入口路径保存在各机的私有运维记录中。

用 1Panel 以 DNS 账号方式签发或续期证书前，确认 `_acme-challenge.<域名>` 已有一条常驻占位 TXT。1Panel 的传播预检在记录创建后立即查询，过早拿到的否定答案会被公共解析器缓存 30 分钟，预检可能卡满 30 分钟后失败；名字始终可解析就不会产生这种缓存。现有四条记录覆盖三台上全部以 DNS 方式签发的证书，acme.sh 签发的证书不受影响。

## 选节点和排查

- **刷新订阅后缺少 TCP 443 节点**，先对照 3x-ui 源订阅，再检查 Sub-Store 分组和缓存。Reality 链接用 IPv4 连接，域名在 SNI 字段里，节点档案规则可用 `sni` 或 `sniRegex`。导入 daed 或 Passwall2 后还要核对实际使用的节点组。[Sub-Store 订阅说明](../substore-operators/README.md)
- **新节点能导入但连不上**，在实际客户端网络上发起经代理的 HTTPS 请求，再与独立网络结果对照。TCP 探测可能被本机 TUN 或路由器透明代理提前应答。UDP 跳跃也需要覆盖多个周期。只从服务器或另一台 VPS 测通，仍需验证日常使用的线路。
- **选择备用线路**。此前 `raksmart` 在受测 Windows 和家庭线路上双协议可用。受测线路上的 `lightlayer` 可用 Hysteria 2，Reality TCP 443 存在超时；`lightlayer-zh` 的 Reality TCP 443 于 2026-11-23 在家庭线路实测经代理出网正常（其 Hysteria 2 沿用原有入站、仅改名，未重测）。`bitsflow` 的 Reality 可用，Hysteria 2 在不同线路和端口上的结果有差异。线路变化后以实测为准。Android 的 v2rayNG 实机验收仍待完成。

备份位置、逐台回退顺序和故障判定标准都在[新环境部署手册](DEPLOY.md)。
