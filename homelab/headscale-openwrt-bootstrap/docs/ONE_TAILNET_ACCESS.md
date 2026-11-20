# 节点接入与管理入口

2026-11-20 的现场说明；实际服务和网络状态可能变化。迁移仍暂停，
切勿因新增节点而重置现有设备身份或提前进行主备切换。

## 新 Android 节点

1. 在当前 WSL 的终端生成一个单次使用、20 分钟内有效的入网密钥：

   ```sh
   ssh -J bitsflow raksmart 'headscale preauthkeys create --user 1 --expiration 20m'
   ```

   `1` 是当时权威 Headscale 中 `home` 用户的 ID；后续若用户列表变化，
   先用 `headscale users list` 核实。密钥只在设备加入时使用，20 分钟
   指密钥可用于注册的时间，不代表设备 20 分钟后掉线。不要把密钥
   发到聊天或提交到仓库。
2. 在 Android Tailscale 的账户设置中选择 **Use an alternate server**，
   填入 `https://hs.haoqi90.top`。如果自动打开浏览器登录页，关闭它并
   回到 App；在账户菜单中选择 **Use an auth key**，输入刚生成的入网
   密钥，允许 VPN 连接。界面名称可能随版本略有变化；以
   [Headscale 官方 Android 说明](https://headscale.net/stable/usage/connect/android/)
   为准。
3. 在服务器运行 `headscale nodes list` 或通过 Headplane 检查新节点。
   已存在的设备先确认身份，避免重复注册：现场曾看到 `xiaomi-15-pro`
   节点在线，并完成一次 WireGuard 层探测，但不能仅从服务端节点列表
   判断手机实际设置的 LoginServer。

## Headplane

管理入口：<https://hs.haoqi90.top/admin/>。Headplane 在 Raksmart 上以
`headplane.service` 原生 systemd 服务运行，不是 1Panel 应用；程序位于
`/opt/headplane/releases/0.8.2`，配置为 `/etc/headplane`，持久数据为
`/var/lib/headplane`，只监听 `127.0.0.1:3000`。1Panel OpenResty 将
`/admin/` 反代到它，Headscale 本身监听 `127.0.0.1:8080`。

若需要登录，可在自己的终端临时生成管理 API key：

```sh
ssh -J bitsflow raksmart 'headscale apikeys create --expiration 2h'
```

将输出填入 Headplane 登录页；这个 API key 与节点加入用的 preauth key
用途不同。不要在聊天或仓库中暴露任一种密钥。曾实际验证短期 API key
登录及节点列表；高级配置写入和浏览器 SSH 未开放。

## 1Panel、证书和现状（2026-11-20 起双机纳管）

hs.haoqi90.top 的站点与证书已在两台 VPS 纳入 1Panel 管理（面板可见、
证书自动续期），同时保留公网 IPv4 443 的 Xray 分流与回落（OpenResty
仅监听回环 8443）。要点：

- raksmart（Active）：website id=8、证书 id=10；bitsflow（Standby）：
  website id=5、证书 id=6。两证书均为 CF DNS-01 独立签发、
  `auto_renew=1`，到期 2027-02-18。双机各自续期，无需互拷证书。
- 站点 conf 仍为手工维护的 8443 版本（面板仅登记，不负责写 conf）；
  续期时面板只重写 `sites/<域名>/ssl/*.pem` 并 reload。
- **不要在 1Panel 面板编辑该站点的反代/HTTPS 配置**：面板重写会丢失
  `listen 127.0.0.1:8443`、长超时与 `/admin/` 转发，进而与 Xray 抢
  443。proxy 文件以 DR 工具（`dr-activate-vps.py`）模板为准。
- bitsflow 侧 root.conf 为备机语义 `location ^~ / { return 503; }`，
  提升时由 DR 工具改写；headscale 保持 masked。
- 变更前完整备份（含旧 acme.sh 证书）：两机 `/root/1panel-import-20261120T1122/112333Z`；
  执行脚本 `/root/hs1panel_*.sh`。操作细节与 O_EXCL 建站坑见
  ops-log-2026-11.md 事件七。

主要文件（两机相同布局）：

- `/opt/1panel/www/conf.d/hs.haoqi90.top.conf`
- `/opt/1panel/www/sites/hs.haoqi90.top/proxy/root.conf`
- `/opt/1panel/www/sites/hs.haoqi90.top/proxy/headplane.conf`（仅 raksmart）
- `/opt/1panel/www/sites/hs.haoqi90.top/ssl/fullchain.pem`
- `/opt/1panel/www/sites/hs.haoqi90.top/ssl/privkey.pem`

## 公司网络访问 /admin/（2026-11-20 已修复）

当日曾因 m-w-r daed `tailscale-ctrl` 出口组两个 HY2 候选同时失活，
控制域名（含 /admin/）新建 TLS 全被掐（curl 000）。已通过 GraphQL
热加载重组出口组修复（见 ops-log-2026-11.md 事件八），浏览器现可
直开 <https://hs.haoqi90.top/admin/>；Headplane 登录仍需短期 API key
（见上文）。若再遇同类故障，备用通道：

```sh
ssh -J bitsflow -N -L 19443:127.0.0.1:8443 raksmart   # 保持运行
# hosts 加 127.0.0.1 hs.haoqi90.top 后浏览器开 19443 端口
```

WORK-PC 已于同日迁移 ControlURL 至 `https://hs.haoqi90.top`
（NodeID/IP 不变）。qi 路由器侧同类出口组问题待修。
