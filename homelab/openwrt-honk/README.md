# openwrt-honk（router-home）

**在家庭主路由 router-home（iStoreOS 25.12.6）上以官方源安装 honk，替换 passwall2。**

- 上游核心：<https://github.com/daeuniverse/honk>（daeuniverse 出品的 dae 风格 Rust 重写，自带 doona Web UI）
- OpenWrt 打包：<https://github.com/kenzok8/openwrt-honk>（稳定通道：OpenWrt 25.12 / 内核 6.12+ / apk 包）
- 参考配置：公司路由器的 daed 方案 [../openwrt-dns-daed](../openwrt-dns-daed)（节点与基础规则同源，公司特有加固见下文「裁剪清单」）

## 目标机器现状（2026-11-20 勘察）

| 项目 | 值 |
|---|---|
| 系统 | iStoreOS 25.12.6（2026110611），x86_64，QEMU Q35 虚拟机 |
| 内核 | 6.12.112，`/sys/kernel/btf/vmlinux` 存在（BTF 免装） |
| 包管理 | apk（`/etc/apk/repositories.d/*.list`，distfeeds 走 cernet 镜像，kmods feed 完整） |
| WAN | `eth0` 上 PPPoE（`pppoe-wan`，移动宽带）+ `Modem` 静态 192.168.1.254（猫桥管理） |
| LAN | `br-lan`（eth1）192.168.100.1/24，IPv6 PD 正常（2409:8a28:.../60 + ULA） |
| 现役代理 | passwall2（enabled，xray socks×2 + chinadns-ng，`node=rulenode` 分流，主节点 raksmart-reality-443） |
| 容器 | watchtower、**cloudflared tunnel（路由器本机跑着，必须保直连）**、iptv-api |
| tailnet | tailscaled（UDP 41641）+ 自定义 `/usr/sbin/tailscale-failover`；本机 100.64.0.1，子网路由 192.168.100.0/24 Approved+Serving |
| 订阅源 | Sub-Store `MySub` collection（raksmart / ll / nosla 各 reality-443 + hy2 固定口；hy2 跳跃段 48000-50000） |

**访问通道警示**：运维 SSH 经 tailnet 子网路由进入（WSL → 宿主 tailscale → 192.168.100.0/24）。
任何会掐断 tailscale 流径的操作都会立即失联，方案中的保命规则与 watchdog 均围绕这一点设计。

## 与公司侧 daed 配置的取舍

公司侧方案是为「DNS 劫持 / 错误解析 / MTU 黑洞 / UDP 受限」的企业上行定制的，家庭宽带（PPPoE 直连、
UDP 可用、IPv6 正常）不需要这些。逐项裁决：

### 保留（基础规则，与公司侧同源）

| 项 | 内容 |
|---|---|
| DNS 分流 | geosite:cn → AliDNS DoH（223.5.5.5），其余 → Cloudflare DoH（1.1.1.1，钉到 doh 组）；`qname(suffix: 自建域名) -> alidns`；`ipversion_prefer: 4`（节点 IPv4-only，防国外 AAAA 走代理失败） |
| 节点入口精确直连 | hy2 固定口 dip+回程 sip、跳跃段 48000-50000 双向、reality TCP 443 裸 IP 直连（放在域名规则之后） |
| doh 钉住组 | 跨 VPS + 协议多样（raksmart + ll，reality + hysteria2），dip(1.1.1.1/1.0.0.1/104.16.248-249.x) + domain(cloudflare-dns.com) 双写，DNS 上游再用 `-> doh` 箭头钉死 |
| STUN 3478 直连 | LAN 客户端与路由器自身 NAT 穿越都要 |
| cloudflared 直连 | 路由器本机有 cloudflared tunnel 容器：argotunnel/cftunnel/cloudflareaccess 域名 + 198.41.192/200.0/24 :7844 TCP/UDP 必须 must_direct |
| Google 系显式代理 | geosite:google、google-deepmind → proxy（先于 CN 直连） |
| premium 组 | raksmart 独占（anthropic/paypal 等出口质量敏感规则） |
| 检测参数 | tcp_check_url 用 `http://cp.cloudflare.com`（不用 gstatic，防 geoip:cn 侧误杀）、udp_check_dns 用 `223.5.5.5:53`（VPS 侧 Xray 有对应的 AliDNS UDP53 窄例外，见 ../vps-proxy-rollout/ROUTING.md）、check_tolerance 50ms |
| 本机 DNS 防回环 | `pname(dnsmasq) && udp && dport(53) -> direct(must)` |

### 裁剪（公司特有，家庭不需要）

| 项 | 裁剪理由 |
|---|---|
| 链路 A（dnsmasq no-resolv + https-dns-proxy DoH + 强 53/拒 853） | 防 DNS 劫持是企业上行问题；家庭 PPPoE 无此困扰，honk 自带 53 透明接管 |
| `DIRECT_INTERNAL_DOMAINS`（komect.com/komect.net/hq.cmcc） | 公司内网域名 |
| 上游 MTU 黑洞加固（/32 mtu 1280 主机路由 + fw4 MSS 钳制） | 企业网关隧道特有；PPPoE 标准 1492 无此问题 |
| tailscale-ctrl / tailscale-derp 锁出口组 + hs-* 域名规则 | 公司上行 UDP 全灭（UDP:false）才需要锁 DERP 出口优化路径；家庭 UDP 直连可用，改用 `pname(tailscaled) -> direct(must)` 全量直连（与现役 passwall2 的 PriorDirect 语义一致） |
| PROXY_CN_DOMAINS（小红书/NGA/雪球/富途等强制代理） | 这些是公司出口 IP 受限才需要的；家庭直连即可，如后续有需要再加 |
| DISABLE_IPV6 / ULA 清理 | 家庭 IPv6 正常，不做整机禁用（DNS 层 ipversion_prefer:4 已足够） |
| Cloudflare Access 自建应用 → premium | 公司侧用法；家庭侧访问该应用走 fallback: proxy 已够用 |

### 家庭侧新增（公司侧没有的）

| 项 | 内容与理由 |
|---|---|
| **tailnet 保命规则** | `pname(tailscaled) -> direct(must)`；`dip(100.64.0.0/10, fd7a:115c:a1e0::/48) -> direct(must)`；`l4proto(udp) && sport(41641) -> direct(must)`（若 honk 不支持 sport 函数则删，pname 已兜底）。运维通道就是这条路径，宁多勿少 |
| watchdog 换机流程 | honk 首启前挂 5 分钟自杀定时（nohup sleep 后 stop honk + start passwall2），验证通过再解除。防止规则失误导致 SSH 永久失联 |
| passwall2 cron 中和 | crontab 有 `4:00 restart passwall2`、`5:00 订阅更新`——init.d 的 restart 会把已停用的服务**重新拉起**，不注释掉会在凌晨与 honk 冲突 |
| `.lan` 本地域名放行 | `qname(suffix: lan) -> asis`，让 dnsmasq 的本地/DHCP 主机名解析不受 honk DNS 接管影响（实施时验证，行为不符则删） |

## 分组与订阅设计

Sub-Store `MySub` 当前节点构成（2026-11-20 实测）：nosla×2（退役中，仍被派发）、raksmart×2、ll×2，
bitsflow 不在集合内。home 只订一个订阅，nosla 用组过滤排除（不在 Sub-Store 侧动刀，避免影响公司侧）：

| 组 | 过滤 | 策略 | 用途 |
|---|---|---|---|
| `proxy` | `name('^(raksmart|ll)-')`（4 节点） | min_moving_avg + 50ms 容差 | 日常代理 |
| `premium` | `name('^raksmart-')`（2 节点） | min_moving_avg | anthropic/paypal 等出口敏感规则 |
| `doh` | `name('^(raksmart|ll)-') && name('-(hysteria2\|reality)')`（4 节点） | min_moving_avg | fallback DoH 承载：双 VPS 双协议 |

注：dae 的 `min` 策略在 honk 里不存在，用 `min_moving_avg` 等价替代。

## 实施记录（2026-11-20 完成）

按计划执行，与计划的偏差与补充经验：

1. **备份** ✓ → `/root/honk-migration-20261120/`（passwall2 双份、crontab、network/dhcp/firewall UCI、nft 全量快照、apk keys、包默认 config.dae、面板凭据）
2. **安装** ✓，但 dllkids feed 域名（down.dllkids.xyz）家宽直连被墙：
   - honk/luci-app-honk 的 .apk 从 GitHub release（`v2026.11.18.1-r3`，core v0.1.0-beta.6）经 WSL 下载 scp 上去，`apk add --allow-untrusted` 本地安装；kmod-nfnetlink-queue/kmod-nft-queue 从官方 cernet kmods feed 按内核 6.12.112-r1 精确匹配自动拉取
   - feed 行 + ECDSA key 手动落地（脚本经 socks 拉取）；**honk 起来后 `apk update` 经代理可达**，`apk search` 能搜到 honk 包——升级链路端到端验证通过
3. **初始化**：`control.sh initialize` 要求 config.dae 为包默认 + stdin 传 `{"username","password"}` JSON → 先恢复默认→初始化（mock-ebpf 试跑、设 uci `initialized=1`）→换回自定义配置→`control.sh local-direct` 重渲染本机地址块
4. **配置语法坑**：`name('正则')` 是**全匹配**语义（子串要用 `name(keyword: '...')`）；首次用 `^(raksmart|ll)-` 前缀式导致组全空（节点活着但 fail-closed），改为多行 `filter: name(keyword:...)` OR 后恢复
5. **订阅冷启动死锁**：订阅默认经 `proxy` 组下载，而组内节点要等订阅——鸡生蛋。解法：订阅条目加 `route: direct`（substore.haoqi90.top 灰云直连实测 200/0.44s）
6. **切换** ✓：cron 中和（passwall2 的 4:00 restart 会复活已停用服务）→ watchdog 420s 布防 → passwall2 stop+disable → honk enable+start。**SSH（经 tailnet 子网路由）全程存活**，watchdog 未触发、验证后解除
7. **clash API 加固**：默认 `0.0.0.0:9090` 无鉴权；设随机 secret（热 reload 不生效，需 lifecycle restart）后 401/200 正常

## 验证清单（2026-11-20 实测）

- [x] honk 启动：eBPF 加载、TPROXY :12345、NFQUEUE 绑定（一条已知降级：UDP raw tracepoint attach busy，QUIC 实测 200 不受影响）
- [x] 代理连通：路由器本机 `curl https://www.google.com/generate_204` → 204@0.65s，出口 IP=raksmart（SJC），QUIC(http3) 200
- [x] DNS：cn 域 alidns 秒回；国外域（google.com）正常返回真实 IP（doh 组承载 cloudflaredns）
- [x] 直连：baidu 200@0.07s、cernet 镜像 apk update 全通
- [x] tailscale：status 正常、kwrt-1 会话持续在线、本 SSH 存活（含一次 honk lifecycle restart）
- [x] cloudflared：隧道连接保持，**passwall2 时代每 5 分钟的 argotunnel DNS 刷新超时在切换后消失**
- [x] LuCI/面板：doona(127.0.0.1:9527)、zashboard(:9090 已设密钥) 均可用；凭据在 `/root/honk-migration-20261120/panel-credentials.txt`
- [x] 官方源升级：`apk update` + `apk search honk` 通过
- [ ] 待抽查：真实 LAN 设备（手机/PC）上的 DNS 行为——ipversion_prefer:4 的 AAAA 压制、`.lan` 本地域名 asis 放行（路由器自身与 tailnet 远程路径查询走 dnsmasq，测不到 br-lan 拦截面）
- [ ] 次日观察：无 passwall2 凌晨复活（cron 已中和）

## 已知观察（2026-11-20）

- **raksmart-reality 节点 UDP 健康检查标记 dead**：与既有结论一致（raksmart 出站 CN 方向 UDP 运营商过滤）；DoH 承载是 TCP 不受影响，TCP 代理正常（当前出口即该节点）
- **ll-reality-tcp-443 从家宽直连超时**：与公司侧 lightlayer 443 病理一致（路径级丢包），节点保持 dead 备用；ll-hysteria2 正常（165ms）
- **kwrt-1（远程站）查询 192.168.100.1:53 超时**：非 honk 回归——同路径从 WSL 正常，属 kwrt-1 站点/ACL 既有问题
- honk 首启日志 `LAN self-protection coverage could not be confirmed` 为提示性 WARN（local-direct 块实际已渲染）

## 升级路径（官方源能力）

```sh
apk update && apk upgrade honk luci-app-honk   # 或 LuCI → 服务 → Honk → 维护 → 升级
```

- dllkids feed 由 kenzok8 官方维护，新 release 同步后 apk 可见；配置在升级时保留（pre-upgrade 备份钩子到 `/etc/.honk-preupgrade/`）
- feed 域名直连被墙，但路由器自身流量经 honk `fallback: proxy` 出网，apk 可正常访问（已实测）
- kmod 类依赖随官方 kmods feed（内核升级后 kmods hash 变化时需要 `apk upgrade` 全量对齐）

## 回滚手册

```sh
# 1. 停 honk 并禁用自启
/etc/init.d/honk stop; /etc/init.d/honk disable
# 2. 恢复 passwall2
/etc/init.d/passwall2 enable; /etc/init.d/passwall2 start
# 3. 恢复 crontab（取消注释两行 passwall2 任务）
# 4. 如需彻底卸载：apk del honk luci-app-honk（保留 /etc/honk/config.dae 备查）
# 完整配置快照在 /root/honk-migration-<date>/
```

## 安全注意

本目录文件保持脱敏：节点 IP 用 RFC 5737 占位、域名用 example.top、订阅 URL 去除 token。
真实值只存在于路由器 `/etc/honk/config.dae` 与本地记忆，勿提交。

## 已知关联问题（非本次范围，记录备查）

- 公司侧 daed 有两个订阅使用已失效的过滤参数（`googleStatus=non-cn10/non-cn20`，Sub-Store 现仅接受 all/clean/ok/non-cn/cn/china/unknown），拉取返回错误 JSON
- Sub-Store MySub 仍派发 nosla 节点（nosla 退役待办中的「最急清理项」）；home 侧用组过滤排除，未动 Sub-Store
