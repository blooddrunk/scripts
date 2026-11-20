# 单 Tailnet 迁移执行记录：2026-11-20

**2026-11-21 凌晨：hs-raksmart / hs-bitsflow 旧控制域名已全部退役**
（客户端清点完毕后，两台 VPS 的 1Panel 站点+证书、Cloudflare 记录、
两台公司路由器 daed 规则一并移除；promote 工具链的 legacy 参数改为
可选，无 legacy 的 `--preflight-only` 已跑通）。详见
[旧域名退役](#旧域名退役2026-11-21-凌晨)。

**2026-11-20 深夜完成公司侧验收：两台公司 daed 独立恢复演练通过、
自建中继公网 STUN 回包确认、raksmart /32 MTU 与官方 DERP 直连两项调优
落地。** router-home 的测试与调优尚未进行。详见
[公司侧验收](#公司侧验收与调优2026-11-20-深夜)。

**2026-11-20 晚间（台北时间）已恢复家庭 WireGuard 和 SSH，接入
HOME-PC-2，并完成 Raksmart → Bitsflow → Raksmart 的生产演练。**
Raksmart 是唯一活动写入端。后文保留当天早些时候的排查证据。

节点接入步骤、Headplane 登录方式和 1Panel 实际配置归属见
[节点接入与管理入口](ONE_TAILNET_ACCESS.md)；日常操作速查见
[快速开始](ONE_TAILNET_QUICKSTART.md)。

## 旧域名退役（2026-11-21 凌晨）

前置清点：hs-raksmart vhost 当日仅剩 127.0.0.1 的 derp/latency-check
探测与运维 /health 检查，最后一次 `/ts2021` 控制面握手在昨晚提升演练
窗口内结束；hs-bitsflow vhost 仅剩 503 探测。全部在线客户端（三台
路由器 + WORK-PC + HOME-PC-2）的 DERP map 与 ControlURL 均已是稳定
域名，无功能性依赖。据此执行退役：

- **路由器 daed（两台，GraphQL 热加载）**：删除
  `hs-raksmart → tailscale-ctrl`、`hs-bitsflow → tailscale-derp` 两条
  规则，`routing-extra.dae` 同步（改前备份 `.bak-legacy-retire-*`）；
  `tailscale-derp` 分组已无规则引用，保留未删（无害，重放时可不建）。
- **1Panel**：raksmart 删 website id=5（hs-raksmart）+ 证书 id=7；
  bitsflow 删 website id=2（hs-bitsflow）+ 证书 id=3。API
  `websites/del`（forceDelete）+ `websites/ssl/del`（body 为
  `{"ids":[N]}`）。两机各自的稳定域名站点/证书（raksmart 8/10、
  bitsflow 5/6）不受影响；conf/proxy/日志完整备份在各自
  `/root/legacy-retire-20261121/<域名>/`。
- **Cloudflare**：删除 hs-raksmart、hs-bitsflow 两条 A 记录（其余记录
  未动；`hs-nosla`、`nosla`、`relay` 仍指向 82.139.215.223，属 nosla
  退役遗留，待用户决定）。
- **promote 工具链**：`--source-legacy/--target-legacy` 改为可选
  （缺省 `-` 表示无旧域名），dr-activate/dr-relay/dr-install-site 以
  `-` 占位跳过旧 vhost 操作；dr-install-site 无模板来源时使用内置
  8443 模板。无 legacy 参数的两台公司路由器 `--preflight-only` 实跑
  通过（隔离恢复 7 节点一致）。
- **残留观察**：仍有客户端经两台 VPS 的 xray 出口以 SNI hs-raksmart /
  hs-bitsflow 做 derp 探测（4-5s 一次）——特征指向家路由器持有的
  过期 DERP map（其流量恰好经两台 reality 出口；家↔WORK-PC 实际走
  P2P 直连不受影响）。DNS 退役后这些探测自然消亡；router-home 调优
  时重启其 tailscale-core 即可清掉陈旧地图。
- MagicDNS 基域仍为 `ts.hs-raksmart.haoqi90.top`（按计划保留，纯
  客户端内部名字，不依赖公网 DNS；未来迁移另行处理）。

## 公司侧验收与调优（2026-11-20 深夜）

### daed 独立恢复演练（qi、m-w-r 各一次）

方法：setsid 脱离 SSH 的演练脚本（停 daed → 只读探测 → 重启 daed →
复验），外加 300 秒兜底定时器；操作端已验证独立于被测 daed 的管理路径
（m-w-r 走 LAN SSH，qi 经 m-w-r tailnet 子网路由）。daed init 自带
procd respawn（3600 5 10），进程崩溃由 procd 自动拉起。两台结果一致：

- 停 daed 后 `BackendState=Running` 保持，真直连（无 eBPF）控制面 TLS
  全通过：稳定域名 200@~0.55s、旧 Raksmart 域名 200、`/derp/probe` 200
  ——**公司直连 raksmart 的 TCP:443 MTU 黑洞由 /32 mtu 1280 主机路由 +
  既有 MSS 1240 钳制覆盖，此前缺的 raksmart 路由已补上**（两台均
  `network.vpsmtu4`，网关分别为 172.17.48.1 / 172.17.56.1）。
- daed 重启后全部恢复：进程在、BackendState Running、控制域名与 LAN
  DNS 200。全程无人工介入。
- 已知代价：停 daed 瞬间，**经代理出口承载的官方 DERP 长连接死亡**，
  tailnet 数据面经 1–2 分钟 DERP 重连后自愈（演练脚本内的 TSMP 探测
  落在重连窗内超时，窗后实测 qi↔m-w-r、m-w-r↔home TSMP 均通）。
- m-w-r 首次演练后出现一条半死 DERP 连接（Send-Q 10KB 不消化，状态
  显示 active 但 TSMP 超时）。处置：确认 LAN SSH 独立可用后
  `/etc/init.d/tailscale-core restart`，连接全部重建，TSMP 恢复
  （qi 620ms、home 7ms 直连）。**教训：daed 停机窗口后若 relay 对端
  TSMP 持续超时，先 `netstat -tnp` 看 tailscaled 连接的 Send-Q，
  半死连接需重启 tailscale-core 清掉，等待 TCP 超时要 15 分钟以上。**

### 官方 DERP 直连规则（两台已生效）

演练暴露的耦合：routing 里没有 `tailscale.com` 直连规则，官方 DERP 与
log.tailscale.com 落入 `fallback: proxy`，daed 一停 tailnet 兜底跟着断。
修复：`domain(suffix: tailscale.com) -> direct` 插入 hs-bitsflow 规则
之后，经 daed GraphQL（:2023，JWT）`parsedRouting` 校验 →
`updateRouting` → `run(dry:false)` 热加载，未重启 daed；头部过期注释
（"官方 DERP 走 fallback: proxy"）同步改正。两台
`/etc/openwrt-dns-daed.d/routing-extra.dae` 片段与改前备份
（`.bak-tsrule-*`）已同步。此后官方 DERP 连接不再依赖代理出口，
daed 停机仅影响 raksmart 999/控制面长连接（官方区兜底仍在）。

### 自建中继公网 STUN 回包

- **headscale 内嵌 DERP 的 STUN 按设计只回应 Tailscale 客户端形态的
  请求**（SOFTWARE 属性必须为 `tailnode` 且带 RFC 5389 FINGERPRINT，
  参考 headscale#3379 与 tailscale net/stun 源码）。普通 RFC 5389 探测
  （stunclient、简易脚本）在本机回环也被拒——这不是故障，探测方法要
  对：用真客户端 `tailscale netcheck`，或带 `tailnode` SOFTWARE +
  FINGERPRINT 的定制探测。
- 服务端抓包（tcpdump udp 3478）：公司真实客户端请求到达，0.2ms 内
  回包已从网卡发出；客户端收不到是 **raksmart→该上行方向的回程 UDP
  丢包**（与既有运营商过滤结论一致）。
- 模拟 Tailscale 形态的探测：bitsflow（US）与 lightlayer-zh（CN 线路）
  均 `REPLY ok` ——公网可用性确认。公司/家庭路径收不到 999 STUN 时，
  客户端自动用官方 Tokyo 等区（netcheck UDP:true），不影响 P2P 打洞。

### hs.haoqi90.top 对 daed 配置影响的复核结论

两台现状已符合规划：`domain(suffix: hs.haoqi90.top) -> tailscale-ctrl`、
旧域名规则保留、`qname(suffix: haoqi90.top) -> alidns` 独立解析、
UDP/3478 直连、Reality TCP/443 must_direct 位于 SNI 域名规则之后、
ctrl 组为跨 VPS 多出口（qi 亦已修复：main-hy2/raksmart 正则 +
premium/lightlayer-hy2 + JP/bitsflow-hy2）。本次新增调优两项：
raksmart /32 MTU 主机路由、官方 DERP 直连（见上）。验收快照：两台
连续 3 次控制域名 200@~0.5s、PollNetMap 无异常、WSL google
generate_204 204@0.22s、经子网路由 SSH 对端可达。

### 当前权威状态

7 个节点（5 在线 + backey-laptop、xiaomi-15-pro 离线）、3 条 LAN 路由
Approved+Serving、单用户 home。剩余待办：WORK-PC 账号登录测试、离线
设备逐台核对、qi-work-pc 由用户手工接入、router-home 的测试与调优。

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
- 移除 901 前，家庭与两台公司路由器的 `tailscale debug derp` 实测 999、901 和
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

## 生产提升与切回

- 使用独立临时客户端禁用其直连 UDP，经官方 Tokyo 区与三台路由器
  分别验证实际 DERP 路径和 TSMP。临时节点到 WORK-PC 首次探测超时，
  随后家庭正式节点到 WORK-PC 的 TSMP 和 `relay "tok"` 活动会话通过。
  临时身份已清理，正式节点保持 6 个。
- 在 Raksmart 保存可恢复原属主、属组和权限的 901 配置，布置 300 秒
  systemd 回退，移除地图中的 901。确认三台路由器收到新地图并通过
  TSMP、家庭到两台 Windows 的 TSMP 都通过，再解除回退。随后停用
  Bitsflow 两个 901 恢复单元，释放 UDP/3478。恢复单元配置、旧代理
  配置和旧 DERP 密钥保留在主机上。需要恢复 901 时，先确认拓扑和
  端口无冲突，再恢复原地图与中继。
- 受限 Cloudflare 令牌验证有效。预检 `3f068b080ed2498c` 完成后，
  实际提升 `2987ba2177c1487a` 于 **21:24:38** 完成。Raksmart 持久
  `masked` 并停止运行后，从其停机快照
  `raksmart-20261120T132253Z.tar.gz.gpg` 恢复 Bitsflow。旧域名转发、
  Headplane 登录入口和三台路由器的 HTTPS、DERP 999 握手及 TSMP
  在 DNS 更新前通过。DNS Only A 切到 Bitsflow 后三台路由器复测通过，
  家庭两台 Windows 的 TSMP 与 WORK-PC 的 RDP TCP 亦通过。
- 实际切回 `68fe7ee2f9e84116` 于 **21:27:39** 完成。先持久封锁
  Bitsflow，再用其停机快照 `bitsflow-20261120T132601Z.tar.gz.gpg`
  恢复 Raksmart。两个方向均保留 6 个节点的身份、地址和批准路由，
  保持 Noise 指纹 `83f45cb98d28`。两份停机快照的异地副本校验通过。
- 最终 DNS Only A 指向 Raksmart，TTL 60。Raksmart Headscale 与
  Headplane 运行，Bitsflow Headscale `masked` 且 Headplane 停止。
  Bitsflow 上的稳定域名转发到 Raksmart，可服务切回前缓存的地址。
  旧 Raksmart 域名 `/health` 为 200，旧 Bitsflow 域名仍为 503。
  切回后实际登录 Headplane，确认显示 6 个节点，并立即撤销测试 API key。
  三台路由器的 DERP 和 TSMP 通过，两台公司路由器均再次经子网路由
  认证登录家庭 SSH，HOME-PC-2 与 WORK-PC 再次通过 RDP NLA 协商。
  两处 daed 持续运行，家庭 honk 持续运行，Passwall2 保持停用。
- 最新在线权威快照 `raksmart-20261120T132814Z.tar.gz.gpg` 已验证
  GPG、manifest、SQLite、双机 SHA-256 及 Bitsflow 隔离恢复。记录的恢复点为
  **2026-11-20 21:28:15 台北时间**。当前按需备份，非计划故障的 RPO
  随最后一次快照的时间增长。令牌、解密口令和操作日志留在运维端，
  解密口令需独立托管。

## 下次切换

先核对现写入端、DNS、独立 VPS SSH、三台路由器的管理路径、证书及 DERP
拓扑，再用相同参数加 `--preflight-only` 预检。示例在仓库根目录执行，
`HEADSCALE_DR_SSH_CONFIG` 指向已验证且固定主机密钥的私有 SSH 配置。

```sh
python3 homelab/headscale-openwrt-bootstrap/headscale-promote.py \
  --source raksmart --source-ip 107.148.154.129 \
  --target bitsflow --target-ip 216.23.86.169 \
  --domain hs.haoqi90.top --cf-zone haoqi90.top \
  --cf-token-file "$HOME/.config/headscale-dr/cloudflare-token" \
  --passphrase-file "$HOME/.config/headscale-dr/home-pc-2-backup.passphrase" \
  --backup-dir "$HOME/.local/share/headscale-dr/backups" \
  --router router-home --probe router-home=100.64.0.3 \
  --router router-my-workspace --probe router-my-workspace=100.64.0.1 \
  --router router-qi-workspace --probe router-qi-workspace=100.64.0.1 \
  --preflight-only
```

`--probe ROUTER=PEER_IP` 的语义是"从该路由器 TSMP 探测的对端 IP"，
不是路由器自己的地址。旧域名已于 2026-11-21 退役，
`--source-legacy`/`--target-legacy` 缺省即 `-`（无旧域名），无需再传；
若未来再出现需要兼容的旧域名，显式传入即可。

预检通过后移除 `--preflight-only` 执行。计划切回时对调源和目标的
主机与 IP。若提升中断，检查
`~/.local/share/headscale-dr/operations/` 对应事务。目标可能已写入时，
必须确认它已持久封锁，并以它的最新停机快照恢复。无法确认封锁时禁止
启动另一写入端。

公司 daed 独立恢复与自建中继公网 STUN 已于当晚验收（见上文）。
WORK-PC 账号登录与 `qi-work-pc` 接入已由用户完成（权威网络现为
7 节点）；其余离线设备由用户择机加入。仍待处理：router-home 的
测试与调优（含其陈旧 DERP map 的清理）。

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
