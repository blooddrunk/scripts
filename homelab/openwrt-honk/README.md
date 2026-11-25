# openwrt-honk（router-home）

**在家庭主路由 router-home（iStoreOS 25.12.6）上以官方源安装 honk，替换 passwall2。**

- 上游核心：<https://github.com/daeuniverse/honk>（daeuniverse 出品的 dae 风格 Rust 重写，自带 doona Web UI）
- OpenWrt 打包：<https://github.com/kenzok8/openwrt-honk>（稳定通道：OpenWrt 25.12 / 内核 6.12+ / apk 包）
- 参考配置：公司路由器的 daed 方案 [../openwrt-dns-daed](../openwrt-dns-daed)（节点与基础规则同源，公司特有加固见下文「裁剪清单」）

## 配置架构（先读这一节）

> **状态**：本节描述的是 2026-11-25 定稿的方案。它已经是仓库里的候选配置，通过了离线校验和
> 隔离实验，**尚未部署**。线上 honk 现在跑的仍是 2026-11-20 的旧方案（见「分组与订阅设计」）。
> 部署步骤见「把候选配置部署上线」。

### 一句话概括

家里所有设备的流量都先经过路由器上的 honk。honk 按"你要访问的是谁"把流量分成几类，
每类交给一个出口组，出口组再从三台 VPS 里挑一台出去。

### 三台 VPS 的分工

| VPS | 线路 | 平时承担 | 月流量 |
|---|---|---|---|
| **lightlayer-zh** | 美国，优化线路，最稳 | 只走 AI 服务 | 1 TB，每月 18 日重置 |
| raksmart | 美国，优化线路 | 日常国外流量 | 1 TB，每月 16 日重置 |
| bitsflow | 日本，普通线路，延迟最低 | 日常国外流量 | 1 TB，每月 28 日重置 |

lightlayer-zh 的流量最金贵，所以日常流量不碰它，只留给 AI 服务。
旧的 lightlayer 退役之前暂且和 raksmart 一起算作"美国组"，退役时整块删掉即可。

### 流量怎么分类

```
家里的设备
   │
   ▼
路由器上的 honk：按"目标是谁"从上到下匹配规则
   │
   ├─ 局域网、国内网站 ─────────────────► 直接连，不经过 VPS
   ├─ AI 服务 ──────────────────────────► premium 组 ──► lightlayer-zh
   ├─ 其它国外网站 ─────────────────────► proxy 组 ────► raksmart 或 bitsflow
   └─ Tailscale、路由器上的 cloudflared ─► 直连（保命通道，不受代理影响）
```

- **国内还是国外**：按域名库（`geosite:cn`）和 IP 归属地（`geoip:cn`）判断。
- **算不算 AI 服务**：按域名库判断。目前是 OpenAI、Anthropic、Gemini、xAI、Perplexity 五类，
  外加 PayPal。
- **DNS**：国内域名问阿里 DNS，国外域名通过 VPS 问 Cloudflare DNS，都是加密的 DoH。
  运营商看不到你查了哪些国外域名，也不会被污染。
- **IPv6**：同样被 honk 接管，不会绕过代理（LAN 侧还需在家实测一次，步骤见
  [LAN-RUNBOOK.md](LAN-RUNBOOK.md)）。

### 出口组怎么选 VPS

每台 VPS 有两个节点（Reality 走 TCP、Hysteria2 走 UDP），同一台的两个节点里自动用
延迟低的那个活着的。

| 组 | 用途 | 怎么选 |
|---|---|---|
| `premium` | AI 服务 | **固定首选 lightlayer-zh**。它整台不通时依次顺延到 raksmart、bitsflow；恢复后自动切回 |
| `proxy` | 日常国外流量 | raksmart 与 bitsflow 之间按**实际使用效果**自动选：谁成功率高、速度快就用谁，并时不时试一下另一台 |
| `doh` | 国外 DNS 解析 | raksmart 与 bitsflow 里延迟低的那个；两台都不通才用 lightlayer-zh（DNS 流量极小） |

"顺延"靠的是每个 VPS 组末尾的一行 `final: <下一个组>`。
`premium` 也可以在面板里手动选。

### 出故障时会怎样

| 情况 | 结果 |
|---|---|
| lightlayer-zh 挂了 | AI 流量顺延到 raksmart，再不行到 bitsflow；恢复后自动切回 |
| raksmart 挂了 | 日常流量全走 bitsflow，AI 流量不受影响 |
| bitsflow 挂了 | 日常流量全走 raksmart，AI 流量不受影响 |
| 三台全挂 | 国外流量不通。**不会偷偷改成直连**，国内不受影响 |
| 某个订阅失效 | 只影响那一条，其余订阅照常（见「订阅失效会不会拖累整体」） |
| honk 进程停了 | 路由器退回普通行为：全部直连 + 运营商 DNS（按设计推断，未做停机实测） |

- **切走要多久**：有人在用时，失败的连接会触发加急检查，一般十几秒到几十秒内切走
  （这是按源码逻辑估的；隔离实验只能实测"没有流量反馈"的情况，是 33 秒）。
  完全没流量时只能等常规检查，最长几分钟。
- **恢复后多久切回**：自动，不用人工干预。honk 每隔一段时间探测挂掉的机器，连续两次成功才算恢复；
  挂得越久探测间隔拉得越长，切回最多要等 5 分钟左右。

### 流量配额守护

[quota/quota-switch.sh](quota/quota-switch.sh) 由 cron 每 30 分钟跑一次，看 komari 里
lightlayer-zh 当月还剩多少流量：

- 剩余 ≥ 20%：AI 流量走 lightlayer-zh。
- 剩余 < 10%：AI 流量改走 raksmart，直到重置后剩余回到 20% 以上。
- 10%–20% 之间：维持现状，避免来回跳。
- raksmart、bitsflow 快用完（< 10%）时只告警，不动作。
- 你在面板里手动选了 bitsflow 时，脚本不干预。手动选 raksmart 则会被当成脚本自己的备胎状态，
  lightlayer-zh 流量充足时会被切回去。

它只管得到**经过家里路由器**的流量。其它设备直接用 lightlayer-zh 节点（带在外面的电脑、
公司路由器的订阅）不归它管，那部分要靠你自己在客户端里选别的节点。

### 怎么看当前状态

- 面板：DOONA `http://192.168.100.1:9527/ui/`；zashboard `http://192.168.100.1:9090/ui/`（要密钥）。
  都能看到每个组当前选的是哪台。
- 路由器命令行：

  ```sh
  S=$(sed -n "s/^ *secret: *'\(.*\)' *$/\1/p" /etc/honk/config.dae)
  for g in premium proxy doh; do
      printf '%-8s ' "$g"
      curl -s -H "Authorization: Bearer $S" http://127.0.0.1:9090/proxies/$g | grep -o '"now":"[^"]*"'
  done
  logread | grep honk-quota | tail -5                  # 配额脚本的决策
  logread | grep -E 'marked dead|revived' | tail -5     # 节点故障与恢复
  ```

- 电脑上：`python3 tools/honk_subs.py router-home` 检查订阅、分组和直连规则是否一致。

### 改配置的流程

配置的唯一来源是仓库里的 [config.home.dae.example](config.home.dae.example)（脱敏模板），
带密钥的完整配置不进仓库。改完模板后一条命令生成并审计，增删 VPS 的具体步骤见
「VPS 增删清单」。

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

## 分组与订阅设计（2026-11-20 二次改造后）

> **这是线上现行的方案**，将被上文的候选方案取代。大白话版：
> - 没有独立的 lightlayer-zh。raksmart 和旧的 lightlayer 两个节点混在一个"美国池"（`us`）里，
>   自动用延迟低的那个，所以实际出口会在两台之间漂（11-24 审查时漂到了 lightlayer）。
> - AI 流量（`premium`）默认走这个美国池，不固定是 raksmart；配额脚本只看 raksmart 的流量，
>   在 raksmart 快用完时把 AI 流量切到 bitsflow。
> - 日常流量（`proxy`）在美国池和 bitsflow 之间按实际效果自动选。
> 候选方案把"每台 VPS 一个组"作为基本单位，AI 流量固定首选 lightlayer-zh。下表是旧方案的原样记录。

Sub-Store 统一入口、每 VPS 单订阅导出（`/download/<vps>`，全部 `route: direct`），
**订阅 tag 即池身份**（`subtag()` 过滤，节点改名/随机后缀不影响分组；首版 name(keyword) 方案已弃用）：

| 订阅 tag | 节点 | | 组 | 过滤 | 策略 | 用途 |
|---|---|---|---|---|---|---|
| raksmart | 2（reality-443 / hy2-51197） | | `us` | subtag(raksmart)+subtag(lightlayer) | min_moving_avg + 50ms 容差 | 美国池，premium 默认承载 |
| lightlayer | 2（hy2-36143 / reality-443） | | `jp` | subtag(bitsflow) | min_moving_avg + 50ms 容差 | 日本池，配额不足备选 |
| bitsflow | 2（hy2-47625 / reality-443） | | `proxy` | group(us)+group(jp) | **score** | 日常代理（不稳回退 min_moving_avg 一行） |
| | | | `premium` | group(us)+group(jp) | fixed selector，`default: us` | openai/anthropic/paypal，quota-switch 按月配额切 us\|jp |
| | | | `doh` | group(us)+group(jp) | min_moving_avg | fallback DoH 承载：三 VPS 双协议 |

nosla 不订阅自动绝迹。bitsflow 入口直连规则：hy2 固定口 47625 dip/sip、跳跃段 48000-50000 双向（三 IP 共用行）、reality 443 IP 列表加 bitsflow 公网 IP（真实值见路由器 config）。

## 实施记录二：流量感知节点策略（2026-11-20 晚完成）

按 [quota-aware-policy-plan.md](quota-aware-policy-plan.md) 执行，§6 全序走完，要点与偏差：

1. **komari 月度用量决策树**（§3）：agent 带 `--month-rotate 16`（TZ=UTC，序列持久化于 bind mount），
   `/api/recent` 的 totalUp+totalDown 即月度真值，**计费周期 = 每月 16 日 00:00 UTC**；面板卡片/告警同源，
   admin API 不需要。冷启动缺口（序列 11-19 02:26 UTC 才开始，本周期缺 ~36G）用脚本内**自过期 offset** 补偿（12-16 失效）。
2. **validate 的坑**：`honk-core config validate` 不检查 filter 表达式语义（写错函数名照样通过），
   filter 解析失败 = 静默忽略 + 组空。本次对 v0.1.0-beta.6 源码核实 `subtag()`/`group()`/`score`/`fixed`+`default`/`tolerance` 后才 reload。
   （2026-11-24 补充：它同样不检查 `subtag(...)` / `group(...)` 指向的名字是否存在，见下文「操作铁律」。）
3. **CLI 的坑**：`honk-core proxy premium jp` 不接受嵌套组成员（"Node not found"），脚本用 clash API `PUT /proxies/premium` 替代。
4. watchdog 420s 布防→reload→全绿解除；备份 `/root/honk-quota/config.dae.bak-1795183476`。
5. quota-switch.sh + config.sh + cron `*/30`（passwall2 已注释行未动）；滞回/告警/cron 触发均实证（详见 plan §10）。
6. **score 保留**：proxy 组以 score 上线，首晚正常；§7 观察判据留待日常，回退 = 改一行 + reload。

## 2026-11-24 审查：订阅韧性、DNS 泄漏面与 VPS 增删

对象是线上 `/etc/honk/config.dae`（2026-11-20 23:42 版，此后未改）加运行态实测。
对应的修订版是 [config.home.dae.example](config.home.dae.example)，**候选，尚未部署**。

### 某个订阅失效会不会拖累整体：不会

- 现网日志：11-24 10:25 `lightlayer` 刷新 `download_timeout`，honk 记
  `Subscription refresh failed; keeping active nodes`，raksmart / bitsflow 的池成员不受影响。
- v0.1.0-beta.6 文档：拉取失败、HTTP 错误、解析失败、零可用节点，一律不发布替换节点，
  "空结果绝不会清空上一代节点"；失败后 30 秒起指数退避重试。冷启动先从 `state/honk.db`
  恢复各订阅正文（`store_subscribe: true`），没恢复的直连订阅共用 5 秒首拉等待。
- 源码 `subscription/supervisor.rs`：每个订阅是独立的拉取任务，失败只结束自己那个 flight。

失效订阅留下的痕迹：条目还在配置里时，它的旧节点会一直留在池里（被健康检查判死），
不影响别的订阅；删掉订阅条目后节点立即消失，若它当时正被选中，池自动改选。
Sub-Store 里已删的订阅返回 404 + JSON（`nosla` 现状），同属"拉取失败"。

所有订阅都出自同一个 Sub-Store 域名（现为 Cloudflare 橙云，不是灰云），这是共用的单点：
它挂了只影响刷新，运行期节点照常，重启时靠 `honk.db` 里存的正文恢复。

### DNS 泄漏面（逐条实测）

| 路径 | 状态 | 依据 |
|---|---|---|
| LAN 客户端发往任意服务器的 53 端口查询 | 被 honk 接管，干净 | 现网连接表里三台 LAN 设备连 `*.google.com` 的目标 IP 都是真实段；dnsmasq 路径给的是假地址 |
| IPv6 流量 | 在拦截面内 | 入口 eBPF 同时解析 `ETH_P_IP` / `ETH_P_IPV6`；路由器自身走 IPv6 访问 cloudflare.com，出口是节点 IP |
| 路由器自身发往外部解析器的 53 端口 | 被 honk 接管，干净 | 对 1.1.1.1 / 8.8.8.8 / 9.9.9.9 / 223.5.5.5 查同一域名，答案一致且等于 honk `/dns/query` |
| 路由器自身经 `127.0.0.1`（dnsmasq）→ 运营商 | **明文，国外域名被污染** | `pname(dnsmasq)` 规则 `direct(must)` 豁免 |
| Docker 容器的 DNS | 明文 `8.8.8.8` 直连，不经 honk | 三个容器的 resolv.conf 都是 8.8.8.8 / 8.8.4.4 |
| bootstrap（节点入口域名、直连订阅下载） | 明文 `1.1.1.1:53` | 四台 hy2 节点的 server 都是域名 |
| 浏览器 / 系统自带 DoH、DoT | 按路由规则走代理或钉到 doh 组 | 按规则推断，未实测 |
| honk 进程停了 | 全屋退化为直连 + dnsmasq → 运营商 | 按设计推断，未做停机实测 |

`pname(dnsmasq)` 这条豁免不建议动：去掉后路由器自身的解析（含 tailscaled 用的系统解析）
就要依赖 honk 的 DNS 路径，与「透明代理停摆时 tailnet 兜底不受连累」的原则相悖。
受影响的只有路由器自己发起的流量，LAN 客户端无关。

### 风险清单

| 级别 | 项 | 说明 | 处置 |
|---|---|---|---|
| 高 | lightlayer-zh 未接入 | Sub-Store 已有该订阅，honk 未订阅、无 filter、无入口规则 | 候选配置 |
| 高 | 配置文件权限 | `/etc/honk/config.dae` 与两份 `.bak` 是 0644，内含订阅 token 与 clash secret；机器上有 dnsmasq、avahi、rpcbind 等降权账户 | `chmod 600 /etc/honk/config.dae /etc/honk/config.dae.bak*` |
| 高 | 操作流程 | 见下文「操作铁律」，validate 有盲区 | 流程约束 |
| 中 | premium 出口漂移 + 配额盲区（线上旧方案） | `us` 是 min_moving_avg 池，当前实际出口是 `lightlayer-hysteria2` 而非 raksmart；`quota-switch.sh` 只盯 raksmart 的 komari 数据。komari 现有 Raksmart、Bitsflow、Lightlayer-zh（11-24 接入，每月 18 日重置，与 raksmart 的 16 日不同步），lightlayer 没有 agent | 候选配置已解决，见「配置架构」 |
| 中 | bootstrap 明文 | 见上表 | 候选已改 `223.5.5.5:53` |
| 低 | 崩溃后无自愈 | procd `respawn 3600 5 5`：一小时内崩 5 次就不再拉起，也没有告警 | 可选：cron 健康检查 |
| 低 | 旁路钩子 | `dscp(4) -> direct(must)` 是包默认规则，LAN 客户端给包打上 DSCP 4 即可绕过分流；STUN 3478 直连会让 WebRTC 向对端暴露家宽 IP，这是为 NAT 穿越做的取舍 | 知悉即可 |

### 方案取舍

诉求：lightlayer-zh 最稳，留给 AI 服务，不要被日常流量早早用完；它挂了要有备胎。
raksmart 和 bitsflow 继续动态平衡。据此定稿：

- **lightlayer-zh 不参与日常流量。** honk 没有"按剩余流量动态让一台 VPS 进出池子"的干净做法，
  所以不硬让日常流量用它。它只承担 AI 服务，外加 DNS 通道的最后兜底。
- **AI 流量首选 lightlayer-zh，逐级顺延**（`lightlayer-zh → us → jp`），靠各 VPS 组上的 `final`。
  没用 honk 的 `fallback` 策略：它一旦顺延就粘在备胎上，首选恢复了也不切回（实验已复现）。
- **Gemini 也归入 AI 服务。** Gemini 的域名全在 `geosite:google` 里，所以 AI 规则必须排在
  Google 规则前面。AI 清单用精选的五类，没用全量的 `category-ai-!cn`：后者混着 HuggingFace、
  Cursor 更新等下载类域名，会消耗 lightlayer-zh 的流量。这五类与 `geosite:cn` 没有重叠。
- **配额守护改为盯 lightlayer-zh。** [quota/quota-switch.sh](quota/quota-switch.sh) 重写，
  逻辑见「流量配额守护」。故障切换不再靠脚本（`final` 在配置里直接生效），脚本只管配额。

### 候选方案的验证

在本机的隔离环境里实测，不碰路由器。工具是 [tools/honk_group_lab.py](tools/honk_group_lab.py)：
用上游发布的同版本 honk-core（v0.1.0-beta.6 静态二进制，SHA256 已核对），在一个私有的
用户/网络命名空间里以 mock 模式启动，三个本地假 SOCKS5 节点模拟 VPS。出口以 honk
自己的 debug 日志为准，"成功"要求查询真的返回 NOERROR。

| 场景 | 结果 |
|---|---|
| 正常：AI 流量经 lightlayer-zh；日常流量与 DNS 通道在 raksmart / bitsflow 之间 | 通过 |
| lightlayer-zh 下线 | AI 流量 33 秒后改走 raksmart；日常流量不受影响 |
| raksmart 也下线 | AI 流量 36 秒后到 bitsflow；日常流量、DNS 通道都落到 bitsflow |
| lightlayer-zh 恢复 | honk 判定复活后，AI 流量自动回到 lightlayer-zh |
| 配额脚本把 premium 切到 us | AI 流量经 raksmart；此时 raksmart 下线仍兜底到 bitsflow |
| 候选配置里原样的 group 段（只把订阅过滤换成假节点） | 以上 16 项全部通过 |
| 对照：不配 `final` | 节点被判死后 60 秒内 23 次查询全部失败，没有兜底，也没有偷偷直连 |
| 对照：premium 用 `fallback` 策略 | 顺延到 raksmart 后，lightlayer-zh 恢复了也不切回 |

读源码时还确认了几条线上同样适用的健康判定规则：

- 节点注册后有 60 秒宽限期，期间检查失败和流量失败都不计数，防止重启瞬间把所有节点一起判死。
- 判死要连续 3 次检查失败；失败后两次检查之间按 10 秒、20 秒退避，之后 40 秒、80 秒……
  上限 5 分钟。所以实验里的 33 秒是"纯靠常规检查"的节奏。
- 真实的 TCP 连接失败会立即触发一次加急检查（每台最快 2 秒一次）并绕过退避，
  所以有人在用时切换更快。**这一条只读了源码，没有实测**：实验走的 DNS 路径不触发它。
- 恢复要连续 2 次检查成功。挂得越久，探测间隔越长，所以切回最多要等 5 分钟左右。

以后改了 group 结构或升级 honk，按脚本开头的说明重跑一遍即可。

### 操作铁律

- 改完一律 `honk-core reload`：热重载，做完整校验；坏配置被拒时旧配置继续运行。
- `/etc/init.d/honk reload` 实为 restart，会断连接。没热重载过的配置不要 restart。
- `honk-core config validate` 只查语法和部分语义：`subtag(写错)` 与 `group(写错)` 实测都通过。
  前者运行时是静默空过滤；后者热重载会被拒，但直接 restart 会让核心起不来。
- 组里一条 filter 都不剩等于接收全部节点。删订阅时别把某组最后一行 filter 删掉。
- 订阅默认 24 小时刷新一次。Sub-Store 侧改了节点想立刻生效，`honk-core reload` 或在 DOONA 里手动刷新。

### VPS 增删清单

推荐改仓库里的脱敏模板，带密钥的完整配置不进仓库。工具是
[tools/honk_subs.py](tools/honk_subs.py)，对路由器只读。

加一台 VPS：

1. 模板 `subscription {}` 加一行：tag 用 Sub-Store 的订阅名，URL 沿用
   `substore.example.top/TOKEN/download/<tag>`，带 `route: direct`。
2. `group {}` 里给它加一个"单 VPS 组"：复制一个现有的（如 `jp`），改组名和
   `filter: subtag(<tag>)`，需要兜底的话加一行 `final: <下一个组>`。
3. 把这个组挂到需要的用途组里：给 `proxy` / `premium` / `doh` 各加一行
   `filter: group(<组名>)`。
4. 节点入口规则段（两个 `# honk:nodes-` 标记之间）复制一个现有块，
   首行改成 `# <tag> <新的占位 IP>`。
5. `python3 tools/honk_subs.py --candidate /tmp/config.dae`：代入路由器上的真实值生成
   完整配置（0600），并对照订阅实际内容审计。漏规则、漏 filter、URL 写错、
   订阅名在 Sub-Store 里不存在，都会报出来且不落盘。
6. 按「把候选配置部署上线」部署。

退役一台：删订阅行、删它的单 VPS 组、删用途组里引用它的那一行、删它的规则块，
同样走第 5、6 步。`group(...)` 的引用漏删不会静默：热重载会被拒绝，旧配置继续运行。

以退役 lightlayer 为例，就是删四处：`subscription` 里的 `lightlayer` 行、`us` 组里的
`filter: subtag(lightlayer)`、规则段里 `# lightlayer …` 那一块（5 行），以及注释里的说明。

直接改路由器上的配置也可以，改完 `honk_subs.py router-home` 审计线上文件。
手里有完整配置的本地副本时，`--patch FILE` 会按订阅实际内容重写规则段并审计；
它拒绝在脱敏模板上运行，免得把真实 IP 写进仓库。

工具只为**配置里已订阅的**订阅生成规则：Sub-Store 里另有两个第三方机场订阅
（`zh-vless`、`zh-hysteria`），它们的节点不会被列出，也不会生成任何直连规则。

### 候选配置相对线上的改动

1. **组结构重组**：每台 VPS 一个组，组末用 `final` 逐级兜底。`premium` 的首选从"美国池"
   改为 `lightlayer-zh`；`doh` 的兜底是 `lightlayer-zh`；日常流量不再有"美国池"。
2. **新增 `lightlayer-zh` 订阅**（hy2 固定口 15672，Reality 走该机公网 IP 的 443）。
3. **AI 规则前移到 Google 规则之前**，并加入 Gemini、xAI、Perplexity。PayPal 规则位置不变。
4. 节点入口直连规则改为每台 VPS 一个 5 行块。规则语义不变，只是 Reality 443 那条从后面
   并进了各 VPS 的块，它与中间那几条规则没有交集。
5. `bootstrap_resolver` 改 `223.5.5.5:53`。AliDNS 对四个节点域名的解析已核对无误。
6. **配额脚本重写**为守护 lightlayer-zh：[quota/quota-switch.sh](quota/quota-switch.sh)、
   [quota/config.sh.example](quota/config.sh.example)。
7. 其余只有注释更正（`client_subnet`、`bootstrap` 的用途、dnsmasq 豁免的副作用）。

候选配置已通过工具审计与路由器上的离线校验（`honk-core config validate`），
行为验证见「候选方案的验证」。配额脚本在本地假接口上跑过 9 个场景（滞回各区间的切换与保持、
手动选择不干预、取数失败不改选择、日常机告警、首周期补偿），并在路由器上对真实接口
做过一次只打印不切换的 DRY_RUN。

### 把候选配置部署上线

**需要你确认后再做。** 配置和配额脚本必须一起换：旧脚本按 raksmart 的配额在 `us` / `jp`
之间切 premium，而新配置里 premium 的首选是 `lightlayer-zh`，旧脚本会在半小时内把它切走。

1. 电脑上生成并审计候选配置：
   `python3 tools/honk_subs.py --candidate /tmp/config.dae`，审计结论必须是"无问题"。
2. 上传到路由器：`/tmp/config.dae` → `/tmp/config.dae.new`；
   `quota/quota-switch.sh` → `/root/honk-quota/quota-switch.sh.new`。
3. 路由器上按 [quota/config.sh.example](quota/config.sh.example) 准备新的 `config.sh`
   （0600）：保留原来的 `KOMARI_BASE`、`CLASH_SECRET`，补上 lightlayer-zh 的 uuid、组名和补偿量。
4. 路由器上先空跑脚本：
   `DRY_RUN=1 QUOTA_CONF=<新 config.sh> sh /root/honk-quota/quota-switch.sh.new`，
   应打印 `premium us -> lightlayer-zh` 的 DRY_RUN 决策。
5. 备份 `/etc/honk/config.dae`，挂一个 420 秒后把备份拷回去并 `honk-core reload` 的 watchdog。
6. 换脚本：`quota-switch.sh.new` 覆盖 `quota-switch.sh`（0700），新的 `config.sh` 就位。
   先换脚本再换 honk 配置是安全的：新脚本面对旧配置只会报一次 `premium_set failed`，不改任何东西。
7. 换配置：`install -m 600 /tmp/config.dae.new /etc/honk/config.dae`，然后 `honk-core reload`。
8. 验证，全部通过才撤 watchdog：
   - `python3 tools/honk_subs.py router-home` 无问题；
   - premium 的 `now` 是 `lightlayer-zh`（脚本半小时内会自动切过去，也可以手动跑一次脚本）；
   - 经 tailnet 的 SSH 仍在，`tailscale status` 正常；
   - `logread | grep honk` 没有 parse 告警，没有 "marked dead" 之外的异常。
9. 顺手 `chmod 600 /etc/honk/config.dae /etc/honk/config.dae.bak*`。

### 范围外观察（同机，与 honk 无关）

- WAN 入站有两个例外对双栈放行：`8897/tcp`（iStoreOS 的 linkease）和 `51819/udp`（WireGuard）。
  本机有公网 IPv6，按规则这两个端口 IPv6 全网可达；本次未能从外部实测。
- docker 区 `input ACCEPT` 全端口，任一容器都能访问路由器的管理面（LuCI、dropbear、honk API）。
- honk 的两个 API 端口（9090、9527）只监听 IPv4，WAN 不可达；LAN、tailnet、docker 三区可达。

## 实施记录（2026-11-20 完成）

按计划执行，与计划的偏差与补充经验：

1. **备份** ✓ → `/root/honk-migration-20261120/`（passwall2 双份、crontab、network/dhcp/firewall UCI、nft 全量快照、apk keys、包默认 config.dae、面板凭据）
2. **安装** ✓，但 dllkids feed 域名（down.dllkids.xyz）家宽直连被墙：
   - honk/luci-app-honk 的 .apk 从 GitHub release（`v2026.11.18.1-r3`，core v0.1.0-beta.6）经 WSL 下载 scp 上去，`apk add --allow-untrusted` 本地安装；kmod-nfnetlink-queue/kmod-nft-queue 从官方 cernet kmods feed 按内核 6.12.112-r1 精确匹配自动拉取
   - feed 行 + ECDSA key 手动落地（脚本经 socks 拉取）；**honk 起来后 `apk update` 经代理可达**，`apk search` 能搜到 honk 包——升级链路端到端验证通过
3. **初始化**：`control.sh initialize` 要求 config.dae 为包默认 + stdin 传 `{"username","password"}` JSON → 先恢复默认→初始化（mock-ebpf 试跑、设 uci `initialized=1`）→换回自定义配置→`control.sh local-direct` 重渲染本机地址块
4. **配置语法坑**：`name('正则')` 是**全匹配**语义（子串要用 `name(keyword: '...')`）；首次用 `^(raksmart|ll)-` 前缀式导致组全空（节点活着但 fail-closed），改为多行 `filter: name(keyword:...)` OR 后恢复
5. **订阅冷启动死锁**：订阅默认经 `proxy` 组下载，而组内节点要等订阅——鸡生蛋。解法：订阅条目加 `route: direct`（substore.haoqi90.top 灰云直连实测 200/0.44s）
   （2026-11-24 复核：该域名现解析到 Cloudflare 橙云地址 104.21.x / 172.67.x，家宽直连偶发 `download_timeout`，honk 会退避重试并保留旧节点。）
6. **切换** ✓：cron 中和（passwall2 的 4:00 restart 会复活已停用服务）→ watchdog 420s 布防 → passwall2 stop+disable → honk enable+start。**SSH（经 tailnet 子网路由）全程存活**，watchdog 未触发、验证后解除
7. **clash API 加固**：默认 `0.0.0.0:9090` 无鉴权；设随机 secret（热 reload 不生效，需 lifecycle restart）后 401/200 正常

## 验证清单（2026-11-20 实测）

- [x] honk 启动：eBPF 加载、TPROXY :12345、NFQUEUE 绑定（一条已知降级：UDP raw tracepoint attach busy，QUIC 实测 200 不受影响）
- [x] 代理连通：路由器本机 `curl https://www.google.com/generate_204` → 204@0.65s，出口 IP=raksmart（SJC），QUIC(http3) 200
- [x] DNS：cn 域 alidns 秒回；国外域（google.com）正常返回真实 IP（doh 组承载 cloudflaredns）
- [x] 直连：baidu 200@0.07s、cernet 镜像 apk update 全通
- [x] tailscale：status 正常、kwrt-1 会话持续在线、本 SSH 存活（含一次 honk lifecycle restart）
- [x] cloudflared：隧道连接保持，**passwall2 时代每 5 分钟的 argotunnel DNS 刷新超时在切换后消失**
- [x] LuCI/面板：doona(:9527)、zashboard(:9090) 均可用；凭据在 `/root/honk-migration-20261120/panel-credentials.txt`
- [x] 2026-11-20 追加：DOONA native_api 改监听 `0.0.0.0:9527`（用户偏好其布局，远程 tailnet 直达 `http://192.168.100.1:9527/ui/`，password_auth 兜底）。附：自定义 config.dae 属 file-owned，`control.sh settings` 会以 `runtime_settings_owned_by_file` 拒改 runtime 字段——监听地址/密钥这类 experimental 字段直接改 config.dae + lifecycle restart 生效（热 reload 不重绑监听）
- [x] 官方源升级：`apk update` + `apk search honk` 通过
- [x] 2026-11-21 凌晨单 Tailnet 验收（经 tailnet 远程）：ControlURL=
  hs.haoqi90.top、单 profile、BackendState Running；TSMP 到公司两路由
  与 WORK-PC 全通（m-w-r 5ms P2P 直连 112.17.79.157:41641）；
  **DERP 999 握手成功**（netcheck 中 raksmart 区无 STUN 延迟=已知
  回程丢包，官方 Tokyo 兜底）；tailscale-core init 无 passwall2 遗留
  env；watchdog UCI 残段已删（enabled 本就为 0）
- [x] 2026-11-21 调优：新增 **LAN 客户端 Tailscale 直连规则**
  （`domain(suffix: tailscale.com) -> direct` +
  `domain(full: hs.haoqi90.top) -> direct`，validate+reload 实证，
  路由器自身仍由 pname 规则覆盖）——对齐公司侧"透明代理停摆时
  tailnet 兜底不受连累"的教训
- [ ] 待抽查（需人在家）：真实 LAN 设备（手机/PC）上的 DNS 行为——
  ipversion_prefer:4 的 AAAA 压制、`.lan` 本地域名 asis 放行、以及
  LAN 客户端 Tailscale 的 direct 表现与 honk 停机容忍（步骤见
  [LAN-RUNBOOK.md](LAN-RUNBOOK.md)）
- [ ] 次日观察：无 passwall2 凌晨复活（cron 已中和）

## 已知观察（2026-11-20）

- **raksmart-reality 节点 UDP 健康检查标记 dead**：与既有结论一致（raksmart 出站 CN 方向 UDP 运营商过滤）；DoH 承载是 TCP 不受影响，TCP 代理正常（当前出口即该节点）
- **ll-reality-tcp-443 从家宽直连超时**：与公司侧 lightlayer 443 病理一致（路径级丢包），节点保持 dead 备用；ll-hysteria2 正常（165ms）
- **bitsflow-hysteria2 TCP/DnsUdp 探测标记 dead**（2026-11-20 晚）：bitsflow-reality-tcp-443 活跃（chatgpt trace ~50ms）；jp 池实际由 reality 节点承载，hy2 待观察
- **router 本机 DNS 污染路径（既有，与 honk 配置无关）**：本机 `127.0.0.1:53` 走 dnsmasq→ISP 明文上游（pname 豁免规则放行），openai/google 等被污染域拿到假 IP；假 **IPv6** 目标会使本机 curl 直连假 IP 挂起（本机 v6 出流未入 honk 拦截面），v4 无碍（SNI 嗅探 + dial_mode:domain 按域重拨）。LAN/tailnet 客户端路径（honk 劫持→doh 组）完全干净（openai=Cloudflare 真段、google=142.251 真段实测）。根治思路：dnsmasq noresolv + 上游钉 honk / 本机压 AAAA，另立任务
  （2026-11-24 更正：本机 v6 出流**在** honk 拦截面内——路由器走 IPv6 访问 cloudflare.com 出口是节点 IP。当时挂起的是 v6 裸 IP 字面量，节点无 IPv6 所以超时，属 fail-closed。另外路由器自身发往外部解析器的 53 端口查询是被 honk 接管的，本机唯一的明文路径就是这条 dnsmasq → 运营商。dnsmasq 豁免为何不建议动见「2026-11-24 审查」。）
- **kwrt-1（远程站）查询 192.168.100.1:53 超时**：非 honk 回归——同路径从 WSL 正常，属 kwrt-1 站点/ACL 既有问题
- honk 首启日志 `LAN self-protection coverage could not be confirmed` 为提示性 WARN（local-direct 块实际已渲染）
- **selector 切换不复位既有连接**：premium us↔jp 切换后，已建立的 chatgpt 等长连接保持原出口直到自然结束，新连接走新选择（验证出口时勿用已有连接池的域名直读 IP，chatgpt.com:443 有连接复用干扰，用组延迟/连接链路判断）

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

## 后续计划

- [quota-aware-policy-plan.md](quota-aware-policy-plan.md) —— **已于 2026-11-20 晚执行完毕**（结论与偏差见 plan §10 及上文实施记录二）。遗留观察：§7 score 稳定性判据（3–7 天）、bitsflow-hysteria2 dead 状态、router 本机 DNS 污染路径根治。它描述的是线上现行旧方案的配额策略。
- 2026-11-25 定稿的候选配置（lightlayer-zh 首选 + `final` 逐级兜底 + 新的配额守护）**待部署**，步骤见「把候选配置部署上线」。
- 部署后待办：在家实测 LAN 侧的 DNS 与 IPv6（[LAN-RUNBOOK.md](LAN-RUNBOOK.md)）；旧 lightlayer 退役时按「VPS 增删清单」删四处。

## 已知关联问题（非本次范围，记录备查）

两条均已于 2026-11-23 在 Sub-Store 与公司侧处理完毕，此处仅留记录：

- 公司侧 daed 曾有订阅使用已失效的过滤参数（`googleStatus=non-cn10/non-cn20`），拉取返回错误 JSON；两台路由器的 URL 已清理
- Sub-Store MySub 曾派发 nosla 节点；nosla 订阅与成员已删除，`/download/nosla` 现返回 404
