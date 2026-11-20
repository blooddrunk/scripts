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

## 1Panel、证书和现存限制

该站点是直接写入 1Panel OpenResty 使用的文件，而非通过 1Panel 网站或
证书功能创建。现场只读检查时，1Panel `agent.db` 的 `websites`、
`website_domains`、`website_ssls` 均无此域名记录，因此面板列表不会
显示对应网站或证书。OpenResty 容器采用 host 网络，主机上的
`/opt/1panel/www` 挂载到容器内 `/www`；主要文件为：

- `/opt/1panel/www/conf.d/hs.haoqi90.top.conf`
- `/opt/1panel/www/sites/hs.haoqi90.top/proxy/root.conf`
- `/opt/1panel/www/sites/hs.haoqi90.top/proxy/headplane.conf`
- `/opt/1panel/www/sites/hs.haoqi90.top/ssl/fullchain.pem`
- `/opt/1panel/www/sites/hs.haoqi90.top/ssl/privkey.pem`

现有公网 IPv4 443 经 Xray 分流到 OpenResty 的回环 8443；OpenResty
按路径分别转发 Headscale 和 Headplane。证书使用运维端 `acme.sh`、
Cloudflare DNS-01 签发，再安全复制到两台服务器对应目录；不是由
1Panel 申请或托管。**尚无已验证的自动续期和双机部署**，现有证书于
2027-02-18 10:19:55（台北时间）到期。ACME 和 DNS 凭据仅保存在运维端
受限目录，不要将证书私钥或凭据放入 Git。

该做法未满足面板可见、可管理的目标。后续应先设计保留既有 443/Xray
入口和回退的迁移，再将网站与证书安全纳入 1Panel 管理；不要直接在
面板重复创建同域名站点，以免与现有 OpenResty 配置冲突。
