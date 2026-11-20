# 单 Tailnet 迁移执行记录：2026-11-20

**状态（2026-11-20 晚间，台北时间）：家庭 WireGuard 和 SSH 已恢复，
HOME-PC-2 已入网，生产主备演练待完成。** 下节记录本次复核，后续各节
保留白天暂停前的历史证据。

节点接入步骤、Headplane 登录方式和 1Panel 实际配置归属见
[节点接入与管理入口](ONE_TAILNET_ACCESS.md)。

## 晚间恢复

- 从 HOME-PC-2 通过直连家庭 LAN `192.168.100.1`，用已知主机密钥登录。
  重启前生成并验证包含原身份和 honk SQLite Online Backup 的加密快照，
  路由器另留权限受限的身份副本。仅重启 `tailscale-core`，家庭
  `tailscaled.state` 的 SHA-256 重启前后一致，原节点地址仍为 `100.64.0.1`。
- Passwall2 自启此前已关闭，但现场发现主进程、透明转发规则和 UCI
  `enabled=1` 仍在。保全 UCI 与 nftables 配置后停止主服务，将开关设为
  `0`，关闭未使用的 `passwall2_server` 自启。复核主进程和拦截规则已清除，
  活动 cron 中无 Passwall2 任务。honk 全程运行。
- 家庭与两台公司路由器双向 TSMP 通过，两台公司路由器经
  `192.168.100.1:22` 的回程 SSH 均实际通过主机密钥验证和认证。
  家庭端可经批准路由登录 `192.168.6.1` 与 `192.168.10.1`。
  HOME-PC-2 到 WORK-PC 的 RDP TCP 建连通过，家庭路由器作为来源的 RDP
  协商也返回 NLA。账号登录仍待用户操作。
- HOME-PC-2 以单次短期密钥重新认证为原节点 ID `5`，IPv4 `100.64.0.5`
  和机器公钥保持一致，客户端节点密钥更新。实测 ControlURL 为
  `https://hs.haoqi90.top`，到家庭路由器和 WORK-PC 的 TSMP 通过。
  权威节点仍为 6 个。WORK-PC 继续使用旧 Raksmart 域名，
  qi-work-pc 留待用户手工接入，离线 Android 设备的 ControlURL 未验证。
- 两台公司 daed 保持运行。两处 AliDNS 返回当前权威 IP，UDP/3478
  直连规则和 WAN MSS 钳制存在，Bitsflow 有 `/32 mtu 1280` 主机路由，
  Raksmart 在两处均无对应 `/32`。`192.168.10.1` 曾对两个控制域名
  返回 TLS EOF，随后两台路由器分别对每个域名连续 3 次返回 200。
  后续 25 秒采样见两台路由器各自同一条到 Raksmart 443 的
  tailscaled TCP 连接持续建立。长期稳定性、Raksmart 路径 MTU、
  HY2 候选和无 daed 恢复仍待验收。
- 家庭与两台公司路由器的 `tailscale debug derp` 实测 999、901 和
  官方 Tokyo 区均完成 DERP 握手。三处的官方 Tokyo 探测均收到 IPv4
  STUN 回包，两台自建中继的探测均未收到 IPv4 STUN 回包。
  家庭已建立官方 Tokyo 连接。
  Bitsflow 的 901 仍由临时恢复实例提供并占用 UDP/3478。
- 重新入网后的权威快照 `raksmart-20261120T130600Z.tar.gz.gpg` 已通过
  GPG、manifest 和 SQLite 校验，两台 VPS 的异地副本与本机 SHA-256
  一致。按该快照在 Bitsflow 的仅回环命名空间恢复，6 个节点、身份、
  地址和批准路由与权威一致。恢复后的家庭快照
  `router-home-20261120T130903Z.tar.gz.gpg` 也已校验并复制到双机。
  此前快照保留。新备份口令写入 HOME-PC-2
  的 `~/.config/headscale-dr/home-pc-2-backup.passphrase`，
  需另行托管在独立安全位置。两台 VPS 仅接收加密归档。
- Raksmart 保持唯一权威，稳定和旧 Raksmart 域名的 TLS、健康检查及
  稳定域名的 Headplane 登录页通过。双机证书已登记 1Panel 自动续期，
  见[节点接入与管理入口](ONE_TAILNET_ACCESS.md)。
  本次尚未进行生产 fencing、提升、DNS 变更或切回演练。

## 生产演练的恢复顺序

1. 在独立管理通道复核两台公司路由器的控制长连接、实际 HY2 出口、
   Raksmart 主机路由 MTU/MSS 和管理恢复能力，再判断是否调整 daed。
2. 在各站点确认官方和自建中继的实际数据面，分离 901 与目标 999。
   如果迁移 901，先让客户端实际使用独立中继。若退役 901，先验证目标
   客户端的替代中继以及无法直连时的 TSMP。确认客户端地图更新后再释放
   备机的 UDP/3478 并处理恢复单元。保留 901 的配置、服务与证书回退。
3. 将上次运维端的受限 DNS token 安全放回当前 WSL 的
   `~/.config/headscale-dr/cloudflare-token`，权限 0600。HOME-PC-2
   当前缺少该文件。更新并异地校验一致性快照，运行 `--preflight-only`。
4. 执行受控提升。检查持久封锁和节点摘要，验证旧域名兼容、Headplane
   与各路由器的 HTTPS、目标 DERP 握手和 TSMP 后才更新 DNS，再复测。
   如需切回，先持久封锁现写入端，再用其最新停机快照恢复旧主。
   保留最后一次已验证快照时间作为非计划故障的恢复点，备份目前按需执行。

## 白天暂停前的现网记录

- DNS Only `hs.haoqi90.top` A 指向 Raksmart，TTL 60，
  无额外 AAAA。Raksmart Headscale v0.29.3 和 Headplane 运行，稳定域名
  与旧 `hs-raksmart.haoqi90.top` `/health` 返回 200。权威 Noise 公钥
  对应私钥的 SHA-256 前缀为 `83f45cb98d28`；节点数 6，三条已批准
  LAN 子网路由保留，MagicDNS 仍为 `ts.hs-raksmart.haoqi90.top`。
- 三台路由器已切到 `https://hs.haoqi90.top`，完成加密快照后移除非活动
  profile、关闭旧跨网络 watchdog；未删除 `tailscaled.state`。公司两台
  daed 的控制组包含跨 VPS 的两个 HY2 候选，保留自建域名 AliDNS、
  UDP/3478 和实际出口的 MTU/MSS 配置。旧域名规则仍保留。
- 一次公司路由器 daed 修改期间曾失去运维端联网，用户手动启动后恢复；
  **没有证据证明该次故障可无人恢复**。此后未再停止或重启公司 daed。
  后续修改必须先验证不依赖该 daed 的管理和恢复通道。后续检查时本机
  所连路由器 `tailscale-ctrl` 还曾报告两个候选均不可用，控制域名
  HTTPS 间歇返回 000；另一台公司路由器同期返回 200。现有两台公司
  路由器间的 WireGuard TSMP 探测成功，但这不代表控制出口持续健康。
- 家庭路由器使用 honk，Passwall2 按用户确认保持停用；没有修改或启动
  Passwall2。家庭节点的控制连接报告在线，历史备份中的节点私钥与当前
  权威公钥匹配，迁移前后数据库中的节点身份、地址和批准路由未变。
  然而来自公司路由器的家庭节点 TSMP 和家庭 LAN SSH 超时；普通
  disco `tailscale ping` 曾成功，
  它不证明 WireGuard 或 SSH 可用。需要恢复前禁止继续主备切换。
- 本机 Windows `WORK-PC` 已是权威网络的节点 4，仍使用
  `https://hs-raksmart.haoqi90.top`；旧域名兼容入口必须保留，稳定
  URL 尚未迁移。用户随后确认希望从家中远程访问这台留在公司的电脑：
  已启用 Windows Tailscale 无人值守模式，并核对节点公钥、IP、控制
  地址及其他网络设置不变。来自两台公司 Tailnet 路由器的 WireGuard
  探测成功，另一台公司路由器到 Windows 远程桌面 TCP 3389 建连及
  网络级身份验证协议协商成功；未发送凭据或进行账号登录测试。
  Windows 远程桌面已启用并要求网络级
  身份验证，交流供电时的自动睡眠为关闭。不要为重复加入而重新注册。
  后续从该 Windows 当前网络新建稳定及旧控制域名 TLS 连接均曾失败，
  而服务器本机检查为 200，控制组也曾报告无可用出口；因此不能保证
  断线或重启后立即重连，更不能推断家庭侧已可远程访问。
  原 Bitsflow 独有的在线 `qi-work-pc` 按用户决定暂不迁移；另有 5 台
  仅在旧网络中的离线设备。它们不会自动合并到权威库，之后需逐台
  重新加入。
- Headplane v0.8.2 Native 固定镜像摘要，监听回环地址；`/admin/` 无
  身份时跳转登录。曾用短期 API key 实际登录并查看 6 个节点，也验证
  暂停 Headplane 不影响 Headscale `/health`。不授予 Headplane 配置
  写入或进程重启权限。Bitsflow 的 Headplane 当前未运行。使用者可在
  权威主机自己的终端执行 `headscale apikeys create --expiration 2h`
  取得短期登录密钥；密钥不要写入聊天或仓库。

## 提升与恢复演练

1. 在 Bitsflow 的仅回环网络命名空间中，恢复副本的 `/health` 和
   `/derp/probe` 均为 200，6 个节点、密钥、地址和批准路由的摘要与
   权威机一致。另在私有挂载及网络命名空间使用实际 `headscale` 用户
   和二进制验证恢复文件、启动及故障时封锁；这些均不等于真实切换。
2. 约 08:48–08:51 UTC 实际封锁 Raksmart、对停机后数据库生成并验证
   加密快照，在 Bitsflow 启动同一权威身份并验证节点摘要。旧域名转发
   健康检查失败，故没有更新 DNS。程序封锁 Bitsflow，从**其最新停机
   快照**恢复 Raksmart，然后恢复 Headplane；唯一活动的权威身份仍在
   Raksmart。事务日志在运维端受限目录
   `~/.local/share/headscale-dr/operations/e87dee68d00d4bfb.json`，
   回退快照为 `bitsflow-20261120T085034Z.tar.gz.gpg`。
3. 失败原因定位为 OpenResty 上游证书链验证深度不足；代码已设置
   `proxy_ssl_verify_depth 4` 并增加重载后的重试，**尚未重新执行成功的
   生产提升**。原 Bitsflow Headscale 主服务现在 `masked`，其旧数据库
   从未与权威数据库合并；原服务的旧数据及加密快照仍保留。
4. 回退后发现家庭节点的 WireGuard 不通。为恢复其曾使用的 DERP 901，
   Bitsflow 暂时运行 `hs-dr-derp-recovery`：它使用原 DERP 私钥、独立
   的空数据库和仅回环控制监听；外部仅开放 `/derp`，旧 `/health`
   为 503，主 Headscale 服务仍 `masked`。公司路由器对 901 的 DERP
   连接探测通过，但家庭 WireGuard 仍失败。另已启用持久的
   `headscale-derp-recovery` 单元，只有主 Headscale 保持 `masked` 时
   才允许启动；当前仍由临时单元运行，**尚未进行重启实测**。它占用
   UDP/3478，不能把此状态视为可直接提升的备用机，也不能把它当作
   第二个生产控制面。
5. 排查期间一次尝试移除失效自建 DERP 后回退配置时，回退副本属组未
   保留，导致 Raksmart Headscale 重启失败。已将
   `/etc/headscale/derp-extra.yaml` 恢复为 `root:headscale`、模式 0640，
   重启后本机和 HTTPS 健康检查均恢复 200。该尝试未作为已验证工具
   交付。官方 DERP 地图保留；自建 901 的恢复中继与 999 仍需真实
   数据面及重启验证，不能仅凭 HTTP 200 推断完整可用性。

`headscale-promote.py` 是仍需现场验收的人工触发工具，不是自动选主。
它现在要求每台路由器有实际 WireGuard TSMP 对端探测，并在目标还有
临时 DERP 服务或其它自建 DERP 区域与目标 IP 冲突时，在封锁旧主之前
拒绝运行。若目标可能已写入，回退需先封锁目标并从目标最新快照恢复；
若无法确认目标已封锁，不会重新启动旧主。该工具**尚未完成成功切换
及独立回退的全部验收**。

## 恢复家庭管理通道

需要家庭侧不依赖 Tailnet 的管理入口（家庭 LAN SSH、控制台或其它已
配置的带外通道）。从本机到家庭 LAN 地址和家庭 Tailnet 地址的 SSH
仍超时；不要重置节点身份、删除 `tailscaled.state` 或启动
Passwall2。若能在家庭 LAN 登录，先检查：

```sh
/etc/init.d/tailscale-core status
tailscale status
logread | grep -iE 'tailscale|wireguard|derp' | tail -80
```

确认本地管理入口独立于 Tailnet 后，可由家庭侧重启
`/etc/init.d/tailscale-core restart` 并重新检查状态；honk 保持运行。
随后从公司路由器验证 WireGuard 层，并验证家庭 LAN SSH：

```sh
tailscale ping --tsmp --c 2 --until-direct=false <HOME_TAILNET_IP>
```

日志可能包含敏感信息，只分享脱敏错误，不要提交状态文件或密钥。
如果重启无效，保留现状并进一步检查家庭侧 tailscaled 与防火墙日志。

## 备份、证书与人工维护

- 已使用 SQLite Online Backup API、SQLite `quick_check`、逐文件 manifest
  和 GPG 完整性校验；最新已验证权威快照
  `raksmart-via-nosla-20261120T091831Z.tar.gz.gpg` 位于运维端
  `~/.local/share/headscale-dr/backups/`，并复制到两台 VPS 的
  `/var/backups/headscale-dr/`，复制后 SHA-256 一致。Bitsflow 原旧网络
  与三台路由器均有独立加密快照；不要将任何快照提交 Git。
- 加密口令位于运维端 `~/.config/headscale-dr/backup.passphrase`，
  Cloudflare 受限令牌位于同目录 `cloudflare-token`，权限为 0600。
  **必须将备份解密口令另存到受保护、独立于本机和备份的地方**；
  丢失口令无法恢复加密归档。不要把口令或令牌贴进聊天或仓库。
- 尚未安装可靠的定时快照；最近一次快照不等于实时复制，RPO 随时间
  增长。重大节点或路由变更后应重新生成并异地校验一致性快照。
- `hs.haoqi90.top` 证书于 2027-02-18 02:19:55 UTC 到期；本次 DNS-01
  ACME 凭据保留在运维端，**尚未配置可靠的自动续期和双机部署**。
  到期前须在运维端续签并安全部署到两台 VPS；不要依赖 WSL 持续在线。
- Raksmart 直连 SSH 曾被重置，运维端私有 SSH 配置
  `~/.config/headscale-dr/ssh-config` 中的 `raksmart-via-nosla` 经过
  独立跳板；设置 `HEADSCALE_DR_SSH_CONFIG` 可供 DR 工具使用。跳板仅
  用于连接，不是控制平面或复制节点。

## 精简验证

已运行 VPS 安装 fixture、路由器迁移失败/回退测试、提升顺序及故障分支
测试、Python/Shell 语法检查；真实服务器隔离恢复、证书/SNI、加密备份
和 Headplane 登录验证如上。没有成功完成 Raksmart → Bitsflow DNS
切换，也没有完成家庭 SSH/WireGuard 验收。
