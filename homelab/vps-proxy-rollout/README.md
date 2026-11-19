# VPS 代理配置教程

## 背景与取舍

适用于在同一台 VPS 上同时运行网站和代理的部署。这里的 `nosla`、`raksmart`、`bitsflow` 和 `lightlayer` 都是 SSH 别名。3x-ui 是管理 Xray 的面板，Xray 负责接收代理连接，nginx 或 OpenResty 负责网站。

原来，网站占用公网 TCP 443，Reality 节点另用一个端口，管理面板也能从公网直连。这套方案让网站和 Reality 共用公网 IPv4 的 TCP 443，用同机 Hysteria 2 提供 UDP 备线，并把 3x-ui 的管理入口收回 VPS 本机。这样可以保留网站原来的 HTTPS 地址，让代理客户端统一使用 TCP 443，并减少公网管理入口。

这也带来三项运维取舍。**3x-ui 需要经 SSH 隧道访问。** 公网 IPv4 网站现在也经过 Xray，重启 Xray 可能让这些网站短暂中断，Web 后端默认看到的是回环来源地址。Hysteria 2 能应对部分 TCP 线路故障，同一台 VPS 或同一个公网 IP 出故障时则需要其他 VPS。

## 访问 3x-ui

### 为什么要建立 SSH 隧道

3x-ui 只监听 VPS 自己的 `127.0.0.1`，所以旧公网地址直接打开会失败。SSH 登录后，VPS 端的 SSH 服务可以连接本机面板。`ssh -L` 把这条连接转发到你电脑的一个本地端口。浏览器访问这个本地端口，SSH 把流量加密传到 VPS，再交给 3x-ui。

### 换电脑后手动连接

1. 准备 OpenSSH 客户端和自己的 SSH 凭据。先用 `ssh nosla` 确认能登录。新电脑还没有 SSH 别名时，可从私有运维记录取主机、用户和端口，在命令里用 `-p 2222 user@vps.example.com` 代替 `nosla`。这里的地址和端口都是示例。
2. 在客户端终端运行下方命令。第一个 `28443` 是你电脑上的空闲端口，第二个 `28443` 要换成 VPS 的实际 3x-ui 管理端口。保留终端运行，按 `Ctrl+C` 就会关闭隧道。

   ```sh
   ssh -o ExitOnForwardFailure=yes -NT -L 127.0.0.1:28443:127.0.0.1:28443 nosla
   ```

3. 用 Chrome 打开面板。当前面板使用 HTTPS，地址需保留证书上的域名，再用本地端口和原登录路径访问，例如 `https://panel.example.com:28443/private-path/`。在本机让该域名解析到 `127.0.0.1`，浏览器才会走这条隧道，同时继续按原域名校验证书。打开登录页后使用原面板账号。 [Chrome 命令](DEPLOY.md#用-chrome-打开登录页) · [终端验收](DEPLOY.md#先用终端验证隧道和-https)

SSH 隧道由终端里的 `ssh` 独立建立，打开浏览器前就可以用 `curl` 验证。手册示例使用单独的 Chrome 数据目录，只为了把临时域名映射限制在那个窗口，避免影响日常浏览器。已有合适的本地域名解析和代理直连规则时，直接用平时的 Chrome 打开面板即可。偶尔维护时手工运行这些命令就够了，桌面快捷方式可在确认能连接后再做。

如果维护期间面板一起失联，先确认 SSH 连接是否还在。比如 SSH 流量经 `raksmart` 出口时，重启该机的 x-ui 可能断开多条隧道。维护前准备独立管理通路，服务恢复后重新运行转发命令。

## 配置如何工作

| 配置 | 用途 | 影响范围 |
| --- | --- | --- |
| Reality 使用公网 IPv4 的 TCP 443 | 同端口承载 Reality 与普通 HTTPS。通过认证的客户端进入 Xray，网站访问由本机 Web 服务处理 | 原有非 443 Reality 入站已退役。旧订阅缓存或手工导入节点需要刷新或换用新节点 |
| Web 的 IPv4 HTTPS 监听移到 `127.0.0.1:8443` | 把公网 IPv4 TCP 443 交给 Xray，同时保留网站的域名、证书和反代 | 由站点按 SNI 提供证书和响应。现有 TCP 80、IPv6 网站监听和 UDP 443 按原部署保留。默认未传递访客源 IP，依赖源 IP 的访问控制、限速和日志需要单独验收 |
| 同机 Hysteria 2 | 用 UDP 作为 TCP 线路的备用通道，并复用同机 IPv4 出口 | 固定端口和跳跃范围需要分别实测。切换协议时应用连接会重建。整台 VPS 或公网 IP 故障仍需切到其他 VPS |
| 管理面板收口 | 通过回环和防火墙限制原生端口，减少公网管理入口 | 3x-ui 走 SSH 转发。现有 1Panel HTTPS 反代继续连回本机。HTTPS 订阅使用独立监听 |
| Xray 出口规则 | 保留本地 API、私网与 BT 规则，按 `geoip:cn` 匹配出口 IP，并保留已确认的域名和 UDP 健康检查例外 | 规则作用于代理出口。客户端仍负责国内网站直连和分流。域名级的 `geosite:cn` 全量阻断曾误伤 Android 连通性测试和正常站点，现已移除 |

三台 VPS 使用 1Panel OpenResty，`lightlayer` 使用 nginx。1Panel 新建或保存站点、更新 OpenResty 或续签证书后，复查站点仍使用回环 8443、原有 IPv6 监听正常、Xray 仍独占 IPv4 TCP 443。证书的续签需要让 Web 服务和 Hysteria 2 都加载到新证书。

## 选节点和排查

- **刷新订阅后缺少 TCP 443 节点**，先对照 3x-ui 源订阅，再检查 Sub-Store 分组和缓存。Reality 链接用 IPv4 连接，域名在 SNI 字段里，节点档案规则可用 `sni` 或 `sniRegex`。导入 daed 或 Passwall2 后还要核对实际使用的节点组。[Sub-Store 订阅说明](../substore-operators/README.md)
- **新节点能导入但连不上**，在实际客户端网络上发起经代理的 HTTPS 请求，再与独立网络结果对照。TCP 探测可能被本机 TUN 或路由器透明代理提前应答。UDP 跳跃也需要覆盖多个周期。只从服务器或另一台 VPS 测通，仍需验证日常使用的线路。
- **选择备用线路**。此前 `nosla`、`raksmart` 在受测 Windows 和家庭线路上双协议可用。受测线路上的 `lightlayer` 可用 Hysteria 2，Reality TCP 443 存在超时。`bitsflow` 的 Reality 可用，Hysteria 2 在不同线路和端口上的结果有差异。线路变化后以实测为准。Android 的 v2rayNG 实机验收仍待完成。

备份位置、逐台回退顺序和故障判定标准都在[新环境部署手册](DEPLOY.md)。
