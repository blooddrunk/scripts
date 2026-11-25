# 架构与原理

> 本文解释 openwrt-dns-daed 这套配置**为什么**这样设计。全部规则与地址均为脱敏示例。

## 两条彼此独立的 DNS 链路

这套配置最容易踩的坑，是以为配置了 https-dns-proxy 之后「所有 DNS 都加密了」。实际上有两条链路，必须分别处理：

### 链路 A：LAN / 路由器常规 DNS

```text
LAN 客户端
   ↓ TCP/UDP 53
https-dns-proxy force_dns（强制拉回路由器）
   ↓
dnsmasq :53
   ↓ 唯一上游 127.0.0.1:5053
https-dns-proxy
   ↓ HTTPS/443
AliDNS DoH
```

同时：

```text
LAN 客户端 TCP/UDP 853（DoT）→ REJECT
```

### 链路 B：daed 自己用于域名分流的 DNS

```text
中国域名 geosite:cn → AliDNS DoH
其他域名          → Google Public DNS DoH
默认 `ipversion_prefer: 4` → 有 A/AAAA 时仅响应 A，AAAA 返回空答案
```

**关键理解**：`https-dns-proxy` 只负责 dnsmasq/LAN 这条链路；它不会自动把 daed 自己的 DNS upstream 加密。所以 daed 的 DNS upstream 也必须单独改成 DoH，否则国外域名的解析仍走明文 53，继续受企业网关/运营商干扰。`ipversion_prefer: 4` 只影响 daed 处理的 DNS，不会改变链路 A 的返回记录。

## dnsmasq 层的三个决策

### 1. `noresolv=1`——WAN 下发的 DNS 不再是上游

`/tmp/resolv.conf.d/resolv.conf.auto` 里出现公司 DNS（如 DHCP 下发的 `172.18.x.x`）**并不代表 dnsmasq 在使用它**。只要 `no-resolv` 生效，dnsmasq 就只使用显式配置的 `server=`。排障时不要盯着 resolv.conf.auto。

### 2. 唯一上游 `127.0.0.1#5053`

历史遗留的多实例（5054/5055）端口已无进程监听，属于失效条目，会让排障时的链路判断混乱。目标状态：普通上游只剩 `server=127.0.0.1#5053`。

注意保留 `/域名/` 形式的域限定条目（如 `/mask.icloud.com/`、`/use-application-dns.net/`），它们是 https-dns-proxy 集成管理的 canary，不是异常。

### 3. `dns_redirect=0`——DNS 劫持只由一家负责

若 dnsmasq 的 DNSMASQ HIJACK 与 https-dns-proxy 的 force_dns 同时存在，nftables 里会出现两套 dport 53 规则。职责必须单一：**DNS 强制接管只由 https-dns-proxy 负责**（53 重定向回路由器 + 853 DoT 拒绝 + canary 约束）。

## daed Routing 层的关键规则

### 为什么 DNS 进程要 `must_direct` 而不是 `direct`

```text
pname(dnsmasq, https-dns-proxy) -> must_direct
```

`must_direct` 的目的不是普通分流，而是让这两个本机 DNS 进程的流量**不再被 dae 的透明接管/DNS 劫持捕获**。如果写成普通 `direct`，仍可能形成：

```text
DNS → daed → DNS → daed → ...（回环）
```

这也是「开 daed 后 DNS 失效」的头号原因。

同理，不要把桌面 Linux 的通用示例（NetworkManager、systemd-resolved）带进 OpenWrt——这台设备上真正参与 DNS 的只有 dnsmasq 和 https-dns-proxy。

### 为什么节点 endpoint 必须精确 `must_direct`

自建代理节点的入口流量，如果被 `fallback: proxy` 再次捕获，会形成「代理套代理」：

```text
客户端 / v2rayN
   ↓
代理节点 endpoint
   ↓
daed 再次代理
   ↓
另一个代理节点（甚至可能是自身）
```

症状：Reality / Hysteria2 节点测试异常、真连接延迟 `-1 ms`、连接超时。

正确做法是按「目标 IP + 传输协议 + 目标端口」精确放行：

```text
dip(203.0.113.10/32) && l4proto(tcp) && dport(33973) -> must_direct
dip(203.0.113.10/32) && l4proto(udp) && dport(50757) -> must_direct
```

**不要**写成整台 VPS 直连：

```text
dip(203.0.113.10/32) -> must_direct          # 错误示范
```

同一台 VPS 往往还承载 1Panel、面板、Web/HTTPS 等普通服务。整 IP `must_direct` 会让这些流量也被强制直连，失去通过代理改善访问速度的机会。精确 endpoint 规则下：

```text
203.0.113.10:33973/tcp  → must_direct        # 节点入口
203.0.113.10:443        → 不命中，继续向下匹配（独立端口的情形）
1Panel / 其他端口       → 不命中，继续向下匹配 → fallback: proxy
```

**这些规则约束的是谁**（2026-11-25 在公司路由器上实测确认）：LAN 客户端（例如自己跑 v2rayN 的电脑）和路由器上的其它进程。daed 自己连节点的流量带内部 socket mark，不经过它自己的转发面——所以新增一台 VPS 时忘了补入口规则，daed 的节点照样可用，受影响的只是 LAN 上直连该节点的客户端。不要因为"节点能用"就认为规则齐全。

### Reality 与网站共用 443 时，endpoint 规则要放在域名规则之后

当 Reality 绑定公网 IPv4 TCP 443、与同机 Web/Headscale 共用端口时（见 `vps-proxy-rollout`），443 入口规则**不能**放在文件顶部的 endpoint 区，而要放在所有针对同 IP 域名的规则（Headscale 控制面、Cloudflare Access 等）之后：

```text
domain(suffix: hs.example.top) -> tailscale-ctrl   # 先命中：带 SNI 的 443 流量
...
dip(203.0.113.10/32) && l4proto(tcp) && dport(443) -> must_direct   # 兜底：裸 IP 直连的才是节点入口
```

若把 443 规则放在域名规则之前，同机 Headscale/Web 的 443 流量会被抢先 `must_direct`，绕开分组出口。所以这类 443 endpoint 不进配置文件的 `NODE_ENDPOINTS`（那会生成在文件顶部），统一放 `routing-extra.dae` 末尾维护。

### Hysteria2 端口跳跃段要双向放行

Hy2 启用 `mport` 跳跃后，客户端出向 dport 在跳跃段内变化，服务端回程 sport 也在该段内。只放行固定口会让跳跃流量落入 `fallback: proxy`，形成代理回环。固定口 dip 之外还要补：

```text
dip(203.0.113.10/32) && l4proto(udp) && dport(40000-41000) -> must_direct
sip(203.0.113.10/32) && l4proto(udp) && sport(40000-41000) -> must_direct
```

### 规则顺序不可乱

```text
1. DNS/本机保护（pname must_direct）
2. 代理节点 endpoint 精确 must_direct（IP+协议+端口，勿整台 VPS /32；
   与同机服务共用 443 的 Reality 入口例外——放域名规则之后）
3. 私网 / 组播 / 内部域名直连
4. ai 特例（AI 服务与支付的 geosite 分类）
5. Google 系显式代理（须排在 ai 特例之后，否则 Gemini 会被它先接走）
6. proxy 特例（需要走代理的国内域名）
7. 中国大陆 direct（geosite:cn / geoip:cn）
8. fallback DoH 上游钉到 proxy
9. fallback: proxy
```

例如 `xiaohongshu.com` 虽然属于中国域名，但它的 `-> proxy` 规则写在 `geosite:cn -> direct` 之前，所以仍走代理。同理 AI 服务与 PayPal 优先命中 `ai`。

规则是顺序匹配、命中即止，所以"兜底"只能体现在 `fallback` 上：没有规则可以表达"这个组不可用就换那个组"，组内成员挂了自动换人全靠分组策略。

### 组里不要留不可达的节点

grouping 策略只在"存活节点"里选，看起来不可达的节点放着也无妨，但每次 reload 之后的二三十秒里 daed 会先把组内全部节点当作可用，死节点要等第一次检测才会被判掉。这段时间 DoH 上游可能被分到死节点上，国外域名解析超时（2026-11-25 在 qi 上实测一次 reload 后 6 次 DoH 上游超时，排除该节点后为 0 次）。所以从本地不可达的节点要从组里排除，不要指望"反正不会被选"。

### 关于 OpenAI 特例

`geosite:openai` 的特例以前默认不启用，因为那时 `premium` 与 `proxy` 成员相同、区分没有意义。2026-11-25 起公司两台的高级组改名 `ai` 并明确为"只用美国线路"，OpenAI / Anthropic / Gemini / xAI / Perplexity 都归入其中（`PREMIUM_GEOSITES`）。扩展 AI 清单时用具体分类，不要用 `category-ai-!cn`——它混着模型下载类的大流量域名。

### 本地网络有 IPv6，但节点 IPv6 不一定可靠

如果宽带具备 IPv6 出口，而 VPS 节点没有 IPv6、IPv6 线路质量不稳定或没有优化，默认采用 IPv4-only 的 daed DNS 配置更稳妥：

```text
DNS:   ipversion_prefer: 4
TCP:   http://cp.cloudflare.com + IPv4 1.1.1.1
UDP:   IPv4 DNS 223.5.5.5:53
IPv6:  不配置 TCP/UDP 节点检测地址
```

这里的 `ipversion_prefer: 4` 会让同时有 A/AAAA 记录的域名仅响应 A、对 AAAA 返回空答案，但不是把所有路由器 IPv6 都关闭；节点 endpoint 仍按实际地址和端口生成 `must_direct`。节点检测也应使用 IPv4 目标，避免检测结果被一条不可用的 VPS IPv6 路径拖成超时或误判。

dae 核心仍会按 tcp4 / tcp6 / udp4 / udp6 分别维护每个节点的存活状态，日志里大量的 tcp6 翻动来自这些检测。LAN 关闭 IPv6 后实际流量只有 v4，看日志时只需关注 tcp4 / udp4。

全局页面中的“引导解析器”留空、“备用解析器”使用 `8.8.8.8:53`，是启动/故障回退设置，与 DNS 片段中的 DoH 上游和节点检测 DNS 不是同一个用途。脚本会生成 `daed-global-settings.txt` 作为 GUI 填写清单，但不会直接写入 `wing.db`。

注意：这项策略只约束 daed 的 DNS。LAN 链路仍是 `dnsmasq → https-dns-proxy`，若要让 LAN 客户端也不拿到 AAAA，需要另外配置 HDP/解析过滤或在 LAN 防火墙层处理 IPv6。

## bootstrap DNS 的作用

```text
bootstrap_dns = 223.5.5.5,223.6.6.6
```

它只在 https-dns-proxy 启动阶段用于解析 DoH 域名本身；之后的实际查询全部走 HTTPS/443。只保留 IPv4 引导即可。注意：v1.2.2 起 `HDP_RESOLVER_URL` 默认为 IP 形式（`https://223.5.5.5/dns-query`），此时无需解析 DoH 域名、bootstrap 不被使用；仅当你改回域名形式时它才生效。

不要把它与 daed 全局页面的“引导解析器 / 备用解析器”混为一谈：前者是链路 A 的 `HDP_BOOTSTRAP_DNS`，后者只是 daed 自身启动或系统解析不可用时的辅助解析器。

## 这套方案的三个限制

1. **强 53 / 拒 853 ≠ 能阻止所有 DoH**。客户端仍可能自行通过 HTTPS/443 访问第三方 DoH 服务。`canary_domains_icloud` / `canary_domains_mozilla` 能约束一部分系统/浏览器行为，但不能等价于「封锁互联网上所有 DoH」。
2. **daed 配置不要直接编辑数据库**。daed 的配置底层在 `/etc/daed/wing.db`（SQLite），但应通过 GUI 操作（GUI 支持导入/导出）。重装流程：导入 daed 配置 → 核对 DNS → 核对 Routing → 重启验证。
3. **`ipversion_prefer: 4` 只影响 daed DNS**，不会自动过滤 `https-dns-proxy` 链路的 AAAA；LAN 的 IPv6 策略仍需单独设计。

## 推荐的部署/排障顺序

```text
1. 安装并启动 dnsmasq / https-dns-proxy
2. 整理 dnsmasq：no-resolv + 只留 5053
3. 配置 https-dns-proxy：AliDNS DoH + force DNS
4. 验证 5053 / nftables / dnsmasq          ← openwrt-dns-daed.sh install 自动完成 1-4
5. 导入/粘贴 daed DNS 与 Routing 配置      ← 片段由脚本生成，GUI 手动粘贴
6. 重启 daed
7. 完整连通性测试                           ← openwrt-dns-daed.sh check
```

**原则：每完成一层先验证，再进入下一层。** 这样一旦出问题，可以明确故障发生在 `客户端 → dnsmasq → https-dns-proxy → DoH` 还是 `daed DNS → daed Routing → 代理组`，而不是把 DNS、透明代理和防火墙一起改掉之后再猜。
