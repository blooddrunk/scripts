# 新路由器 Reapply Runbook（2026-10-30 实战版）

> 目标：把本仓库方案（`routing-extra.dae` 附加规则、`check_tolerance` 选点粘性、公司路由器现行分组模型）完整重放到另一台 OpenWrt 路由器。**分组模型以 README「公司路由器现行方案（2026-11-25 起）」为准**；本文里与它冲突的旧说法（独立 `doh` 组、`premium` 组等）属于 2026-10-30 版本，不再适用。
>
> 产物分三层，职责不同：
>
> | 层 | 载体 | 谁来写 |
> |---|---|---|
> | UCI / 服务层（dnsmasq、https-dns-proxy、防火墙、IPv6） | `/etc/openwrt-dns-daed.conf` + 脚本 | 脚本自动（幂等） |
> | 脚本片段层（DNS / Routing 文本） | `/root/openwrt-dns-daed/daed/*.dae` | 脚本生成（含 extras 合并），粘贴到 GUI |
> | daed 分组 / 全局层 | `/etc/daed/wing.db` | **GUI 手工**（脚本绝不写库） |
>
> 脚本无法代劳的第三层是重放时最容易漏的部分，本文逐步列出。现网实际取值（真实 IP/端口/域名）以在用路由器的 `/etc/openwrt-dns-daed.conf` 与 `/etc/openwrt-dns-daed.d/routing-extra.dae` 为准，本仓库全部脱敏。

## 0. 前提

- OpenWrt 24.10+（fw4/nftables），SSH 可用；`daed` 已安装（kenzok8/openwrt-daede），`/dev/net/tun` 就绪；
- 自建 VPS 若干（示例：三台美国线路 + 一台备机 `bitsflow`），每台 **Reality(tcp) + Hysteria2(udp) 双入口**；
- 占位符约定与仓库一致：IP 用 `203.0.113.10/20/30`，域名用 `*.example.top`。

## 1. 安装脚本（生成 conf 模板）

```sh
curl -fsSL https://raw.githubusercontent.com/blooddrunk/scripts/main/homelab/openwrt-dns-daed/openwrt-dns-daed.sh | sh -s -- install
```

## 2. 填写 `/etc/openwrt-dns-daed.conf`

生成后**永不被覆盖**，重点填：

```sh
# 全部节点入口（IP:协议:端口，空格分隔；hy2 的 udp 入口也要列）
NODE_ENDPOINTS="203.0.113.10:tcp:39654 203.0.113.10:udp:51237 \
                203.0.113.20:tcp:20185 203.0.113.20:udp:47777 \
                203.0.113.30:tcp:56917 203.0.113.30:udp:36143"
DIRECT_INTERNAL_DOMAINS="internal.example.corp"    # 内网域名，geosite:private 自动追加
PROXY_CN_DOMAINS="xiaohongshu.com nga.cn xueqiu.com"  # 需强制走代理的国内域名
# DAED_DNS_CN_URL / DAED_DNS_FALLBACK_URL 保持 v1.2.2 默认（IP 形式 DoH）即可
```

注意：**hy2 回程 `sip(sport)` 规则脚本不生成**，进第 3 步的 extras 文件维护。

## 3. 放置 `routing-extra.dae`

```sh
mkdir -p /etc/openwrt-dns-daed.d
# 把仓库 examples/routing-extra.dae.example 拷过去，替换占位符
vi /etc/openwrt-dns-daed.d/routing-extra.dae
```

内容模板见 [examples/routing-extra.dae.example](../examples/routing-extra.dae.example)，承载 install 不生成的全部附加规则：hy2 回程 `sip` 与跳跃段、STUN 3478、headscale 控制面、自建 CF Access、Google 显式代理、**fallback DoH 钉到 `proxy` 组**、Cloudflare Tunnel 直连、Reality 443 入口。插入位置由脚本保证（endpoint 规则之后），规则与 CN/私网规则目标无重叠。

## 4. 重跑 install 生成片段

```sh
sh /root/openwrt-dns-daed/openwrt-dns-daed.sh install
```

产物：`daed-dns.dae`（IP 形式 DoH + `ipversion_prefer: 4`）、`daed-routing.dae`（含 extras 自动合并）、`daed-global-settings.txt`。

## 5. daed GUI 手工项（脚本不做、重放最易漏）

### 5.1 订阅与节点命名

两个订阅：`main`（美国线路）与 `backup`（bitsflow）。不要再建"同一集合再加区域筛选"的订阅——美国档里只剩美国节点时它和 `main` 返回同样的结果。**节点命名要带协议**（`xx-reality-tcp-…`、`xx-hysteria2`），第 5.2 步的 `reality` 过滤依赖它。

### 5.2 分组（配置 → 分组）

| 分组 | 订阅 | 过滤正则 | 策略 | 用途 |
|---|---|---|---|---|
| `proxy` | `main` + `backup` | `main` 用 `raksmart-reality\|lightlayer-zh-reality`；`backup` 用 `reality` | `min_moving_avg` | 日常代理；承载 fallback DoH |
| `ai`（即 `DAED_GROUP_PREMIUM`） | `main` | 同上 | `min_moving_avg` | AI 服务、支付、自建 Access |
| `tailscale-ctrl` | `main` + `backup` | `main` 同上；`backup` 用 `reality` | `min_moving_avg` | headscale 控制面（可选） |

公司网络下组里不要收 hy2 节点（约每小时翻动一次），bitsflow 的 Reality 节点延迟最低，放进 `proxy` 就是主力（公司两台接受这一点，AI 仍只走美国两台）；从本地不可达的节点同样要排除，`main` 因此用显式名单而不是笼统的 `reality`。依据见 README「公司路由器现行方案」。

### 5.3 全局（配置 → 全局）

- **`check_tolerance: 50ms`**（dae 默认；语义：新节点延迟 ≤ 现任−容差才切换。0 容差会在几 ms 差距间反复换节点）；
- 引导解析器留空或 `223.5.5.5:53`，备用解析器 IPv4；TCP/UDP 节点检测按 `daed-global-settings.txt`（IPv4-only）。

### 5.4 粘贴并重启

DNS / Routing 标签页用生成片段**整体替换**保存，然后：

```sh
/etc/init.d/daed restart    # 完整重启；daed UI 里的停止/启动可能只拉起面板、核心不加载
```

## 6. 验证

```sh
sh /root/openwrt-dns-daed/openwrt-dns-daed.sh check          # 期望 0 FAIL
grep -c "no proper dialer" /var/log/daed/daed.log             # 新窗口期应为 0
grep "group=proxy" /var/log/daed/daed.log | tail                # 应列出美国三台的 reality 节点及延迟
nslookup www.baidu.com 192.168.10.1 && nslookup www.google.com 192.168.10.1
grep "Health check will only verify" /var/log/daed/daed.log | tail -1   # outbounds 应含 proxy/ai（及 tailscale-ctrl）
```

## 7. 附录：无 GUI 时的直改库流程（高级，慎用）

> **先检查管理通道是否依赖 daed（2026-10-30 实战教训）**：若这台路由器只能经 tailnet/自建 DERP 访问，而 DERP 流量本身又由 daed 分组承载（`tailscale-ctrl` 等），则**停 daed = 自断管理通道**。在这种设备上绝不能用「SSH 里跑一长串 stop→改→start」的裸流程——连接中途断开就会把 daed 永远停在 stopped，只能现场断电。安全做法：把整个变更写成一个**自愈式一次性脚本**放到路由器上，脱离会话执行；并在动手前预置一条兜底延时启动（见下）。
>
> **注意 `nohup` 并不总在（2026-11-02 实战）**：Kwrt 部分构建的 busybox 不带 `nohup` applet，模板直接报 `nohup: command not found`、脚本静默未执行。用 `setsid` 等价替代（先 `command -v setsid` 确认），并务必 `</dev/null` 重定向 stdin。下方模板已改为 setsid 写法（有 nohup 的设备两者等价）。

> **优先走 GraphQL**：分组、订阅、Routing 都可以用 daed 的 GraphQL 接口改（`http://<路由器>:2023/graphql`，用面板账号对应的 JWT 认证），不碰 `wing.db` 就没有 WAL 损坏的风险。2026-11-25 的公司路由器改造全程走接口完成。下面的直改库流程仅作存档，其中的 `doh` 分组是旧模型。

远程自动化或无法访问 GUI 时可直改 `wing.db`（本流程 2026-10-30 实战验证）。脚本本身仍不写库，此为人工操作：

0. **自愈式执行模板**（管理通道依赖 daed 的设备必用；其他设备也建议用，等价于崩溃安全）：

   ```sh
   cat > /tmp/fix-daed.sh <<'EOS'
   #!/bin/sh
   # 幂等：new 库在则换入；无论如何最后都把 daed 拉起
   if [ -f /tmp/wing.db.new ]; then
       rm -f /etc/daed/wing.db-wal /etc/daed/wing.db-shm
       mv /tmp/wing.db.new /etc/daed/wing.db
       chmod 640 /etc/daed/wing.db
   fi
   /etc/init.d/daed restart
   EOS
   # 兜底：即使主脚本意外中断，120 秒后也强制拉起 daed
   setsid sh -c 'sleep 120; /etc/init.d/daed start' </dev/null >/dev/null 2>&1 &
   setsid sh /tmp/fix-daed.sh </dev/null >/tmp/fix-daed.log 2>&1 &
   ```

1. 备份三件套：`cp -a /etc/daed/wing.db* <备份目录>/`；
2. `/etc/init.d/daed stop`；拉回 db+`-wal`+`-shm`，用 SQLite 工具改：
   - 新增分组：`INSERT INTO groups (id,name,policy,version,system_id) VALUES (…,'doh','min',1,1)`；
   - 成员：`INSERT INTO group_subscriptions (group_id,subscription_id,name_filter_regex) VALUES (…,'hysteria2|reality')`；
   - 规则文本：改 `routings.routing`（DoH 钉住行 `-> doh`）等，并 `version=version+1`；`dns.dns` / `configs.global` 同理（check_tolerance 在 `configs.global`）；
   - `PRAGMA wal_checkpoint(TRUNCATE)` 后 **`VACUUM`**（清历史行残影，否则脚本「主库含明文即明文」检查可能误报，见 [troubleshooting 情况 E](troubleshooting.md)）；
3. 回传主库，**先删目标机 `-wal`/`-shm` 再覆盖**，`chmod 640`；
4. `/etc/init.d/daed start`，按第 6 节验证（分组/数据完整性在覆盖前用只读查询自证）。

## 8. 附录：MTU 黑洞加固项（脚本不管，重置/重装后必须手工恢复）

上游隧道对国际方向 :443 实测 MTU≈1280，超尺寸段静默丢弃（完整分析见 [troubleshooting 情况 N](troubleshooting.md)）。重放本手册**不会**覆盖这两项，但路由器重置/重装会丢，恢复清单：

1. `/etc/nftables.d/20-mss-clamp.nft` —— fw4 include，output/forward 链把 SYN 上 `MSS>1240` 钳到 1240（模板见 README「上游 MTU 黑洞加固」）；
2. `uci` 的 `vpsmtu*` /32 mtu 1280 主机路由——**每台在用的 VPS 一条**，新增 VPS 后同步补（2026-11-25 补齐了 lightlayer-zh）。

验收：路由器本机 `curl https://relay.haoqi90.top/` 1s 内返回 401/200，daed 日志无 `failed to dial <VPS>:443`。

## 9. 相关文档

- README：「fallback DoH 上游钉到 doh 专用组」（doh 组设计与 add_latency 结论）、「自建 Headscale 场景」、「追加自定义 Routing 规则」、「上游 MTU 黑洞加固」
- [troubleshooting](troubleshooting.md)：情况 D（回环）、E（明文残影/VACUUM）、F 附（DoH 经分组转发）、L（no proper dialer）、N（MTU 黑洞）
