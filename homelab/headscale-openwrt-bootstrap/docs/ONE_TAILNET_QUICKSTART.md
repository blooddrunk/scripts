# 单 Tailnet 快速开始（日常使用）

面向日常操作：登录管理后台、添加/管理设备、常用命令与健康自查。
实现细节、架构与灾备见
[迁移计划](ONE_TAILNET_PORTABLE_CONTROL_PLANE.md)、
[执行记录](ONE_TAILNET_2026-11-20_EXECUTION.md)、
[接入与管理入口](ONE_TAILNET_ACCESS.md)。

- 控制服务器（稳定地址，不随 VPS 变化）：`https://hs.haoqi90.top`
- 管理后台（Headplane）：<https://hs.haoqi90.top/admin/>
- 当前 Active 主机：raksmart（`ssh -J bitsflow raksmart`，经 bitsflow 跳板）
- Tailnet 现状：单用户 `home`；三台路由器各广播一条 LAN 子网路由

## 1. 登录 Headplane 管理后台

Headplane 登录用的是 **Headscale API key**（与设备入网的 preauth key 是
两种不同的密钥）：

```sh
# 1. 在自己的终端生成一把短期 API key（2 小时有效，只用一次）
ssh -J bitsflow raksmart 'headscale apikeys create --expiration 2h'

# 2. 浏览器打开 https://hs.haoqi90.top/admin/，把 key 粘进登录页
```

登录后可以：查看/搜索节点在线状态与 IP、删除或过期节点、管理用户与
preauth key、批准子网路由。**不要**在 Headplane 的 Settings 里改
Headscale 配置（当前部署未授予写入权限）；公司网络若打不开 /admin/，
备用通道见[接入与管理入口](ONE_TAILNET_ACCESS.md#公司网络访问-admin)。

用完可以在后台或服务器上撤销 key：`headscale apikeys expire --prefix <前缀>`。

## 2. 添加新设备

### 第一步：生成入网密钥（一次性，20 分钟内要用掉）

```sh
ssh -J bitsflow raksmart 'headscale preauthkeys create --user 1 --expiration 20m'
```

`--user 1` 是用户 `home` 的 ID（`headscale users list` 可核对）。20 分钟指
密钥可用时间，设备入网后长期在线不受影响。密钥不要发聊天或提交仓库。

### Android

1. Tailscale App → 账户设置 → **Use an alternate server**，填
   `https://hs.haoqi90.top`；
2. 若自动弹出浏览器登录页，关闭它回到 App；账户菜单选
   **Use an auth key**，粘贴 preauth key，允许 VPN。

### Windows / macOS / Linux 桌面

```powershell
tailscale up --login-server=https://hs.haoqi90.top --accept-dns=false --accept-routes=false
# 弹出注册提示后，优先直接用 auth key（各平台 UI 可粘贴 key）；
# 若走浏览器注册 URL，在服务器执行审批：
#   ssh -J bitsflow raksmart 'headscale auth register --auth-id <key> --user home'
```

LAN 内（路由器所在网段）的设备固定加 `--accept-dns=false
--accept-routes=false`，避免 DNS/路由接管冲突（原因见主 README
「与透明代理共存」）。放在公司 LAN 但需要被家里访问的机器（如
WORK-PC）再加 `--unattended`。

### OpenWrt 路由器（新站点）

用本仓库 `tailscale-openwrt.sh`：`discover → install → join
（--auth-key-stdin）→ enable-subnet`，之后在服务端批准路由（见下）。
完整流程见主 README「快速开始 / OpenWrt 端」。

## 3. 日常管理

### 批准子网路由（新路由器入网后必做）

```sh
ssh -J bitsflow raksmart 'headscale nodes list-routes'
ssh -J bitsflow raksmart 'headscale nodes approve-routes --identifier <节点ID> --routes 192.168.X.0/24'
```

当前已批准：家庭 `192.168.100.0/24`（istoreos）、公司两站
`192.168.6.0/24`（router-qi-workspace）与 `192.168.10.0/24`
（router-my-workspace）。站点对端要互访，双方都要
`enable-site-to-site`（accept-routes=true）。

### 节点管理

```sh
headscale nodes list                                   # 在线/离线、IP、Last seen
headscale nodes rename 新名字 --identifier <ID>
headscale nodes expire --identifier <ID>               # 吊销节点密钥（设备需重新认证）
headscale nodes delete --identifier <ID>               # 删除节点（重新加入会拿新身份）
```

Headplane 的 Nodes 页面提供同样的操作。删除前先确认设备不再使用；
删除后设备要重新走第 2 节流程入网。

## 4. 常用命令速查

### 客户端（任意已入网设备）

```sh
tailscale status                       # 对端列表、direct/DERP、在线状态
tailscale ping <对端IP或主机名>          # 连通性；出现 direct 即 P2P 直连
tailscale ping --tsmp --c 2 --until-direct=false <IP>   # WireGuard 层探测（排障用）
tailscale netcheck                     # NAT/STUN/各 DERP 区延迟
tailscale ip -4                        # 本机 tailnet IP
tailscale set --accept-dns=false --accept-routes=false  # LAN 内设备收敛开关
tailscale switch --list                # 多 profile 时列出网络
```

### 服务端（ssh -J bitsflow raksmart）

```sh
headscale nodes list / list-routes / approve-routes    # 节点与路由
headscale preauthkeys list --user 1                    # 入网密钥
headscale apikeys list                                 # 管理 API key
headscale users list
systemctl status headscale headplane                   # 服务状态
```

## 5. 健康自查（1 分钟）

```sh
curl -s https://hs.haoqi90.top/health            # 200 = 控制面正常
curl -sk https://hs.haoqi90.top/derp/probe       # 200 = 自建 DERP 中继正常
tailscale netcheck                               # 客户端视角：UDP/STUN/DERP
```

自建 region 999（raksmart）的 STUN 从国内部分路径收不到回包是
**已知运营商回程丢包**（服务端抓包确认已应答），客户端会自动用官方
Tokyo 等区兜底，不影响使用。注意：内嵌 DERP 的 STUN **只回应
Tailscale 客户端形态的请求**——普通 STUN 探测器（含 stunclient、
简易脚本）按设计被拒收，不要据此误判服务故障（详见
[执行记录](ONE_TAILNET_2026-11-20_EXECUTION.md) 2026-11-20 晚间验收）。

## 6. 什么时候看哪份文档

| 场景 | 文档 |
| --- | --- |
| 日常登录/加设备/批路由 | 本文档 |
| 节点接入细节、1Panel/证书、/admin/ 打不开 | [接入与管理入口](ONE_TAILNET_ACCESS.md) |
| 换 VPS、主备切换、备份恢复 | [执行记录](ONE_TAILNET_2026-11-20_EXECUTION.md) + 主 README「备份与回退」 |
| 架构决策、边界与红线 | [迁移计划](ONE_TAILNET_PORTABLE_CONTROL_PLANE.md) |
| 历史事件与根因 | [ops-log-*.md](.) |
