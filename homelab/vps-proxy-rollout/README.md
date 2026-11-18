# VPS 代理配置教程

适用于 `nosla`、`raksmart`、`bitsflow` 和 `lightlayer`。日常管理先看面板访问和下方的影响表。要给新 VPS 部署这套配置，按[新环境部署手册](DEPLOY.md)完成审查、切换、验收与回退准备。

## 访问 3x-ui

**在已配置的 Windows 电脑上，双击桌面的「3x-ui 面板」快捷方式。** 它用 WSL 的 SSH profile 建立本地转发，在独立的 Edge 窗口打开四个登录页。用原来的 3x-ui 账号登录。

- **旧面板收藏夹打不开**是因为原生管理端口现在只接受回环访问。日常从快捷方式进入。1Panel 继续使用原有 HTTPS 反代，客户端也继续使用原有 HTTPS 订阅入口。
- **重启电脑、WSL 或 x-ui 后打不开**，先重开快捷方式。重启当前代理出口所在 VPS 的 x-ui，可能同时断开经它转发的多条 SSH 隧道。例如正在使用 `raksmart` 出口时，重启它可能让四个面板一起失联。维护前先切换到独立出口，或准备供应商控制台。服务恢复后也可在 WSL 执行 `~/.local/bin/vps-3xui-tunnels` 重建连接。
- **换电脑或没有快捷方式**，使用下方的 SSH 命令。实际管理端口、证书域名和登录路径从私有部署记录取值。新环境建立快捷方式或手工连接的方法见[面板接入步骤](DEPLOY.md#2-建立管理通路)。

```bash
# PROFILE 是 SSH 别名，PANEL_PORT 是该 VPS 的 3x-ui 管理端口。
ssh -o ExitOnForwardFailure=yes -NT \
  -L "127.0.0.1:${PANEL_PORT}:127.0.0.1:${PANEL_PORT}" "$PROFILE"
```

保持命令运行。在独立浏览器配置中把面板证书域名映射到本机 `127.0.0.1`，再用原证书域名、管理端口和登录路径打开 HTTPS。证书校验继续开启。关闭 SSH 会话就会关闭这条本地转发。

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
