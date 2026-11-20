# 单 Tailnet 迁移执行记录：2026-11-20

**状态：未完成。** 权威控制面和两台公司路由器已恢复；家庭路由器的
控制连接在线，但 WireGuard 层和 SSH 未恢复。Raksmart → Bitsflow 的
实际提升在旧域名转发检查失败后回退，DNS 未切到 Bitsflow。不要将
隔离恢复通过或 `/health=200` 描述为主备切换验收通过。

## 现网与已验证结果

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
- 本机 Windows `WORK-PC` 当前仍使用 `https://hs-raksmart.haoqi90.top`，
  旧域名兼容入口必须保留；它并未完成稳定 URL 迁移。原 Bitsflow 独有
  的在线 `qi-work-pc` 按用户决定暂不迁移；另有 5 台仅在旧网络中的
  离线设备。它们不会自动合并到权威库，之后需逐台重新加入。
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
