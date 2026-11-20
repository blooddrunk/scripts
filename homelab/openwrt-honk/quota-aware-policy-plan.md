# quota-aware-policy-plan：流量感知节点策略（router-home / honk）

> 状态：**已执行（2026-11-20 晚）**。实施结论见 §10；§7 score 日常观察留待使用中判断。
> 依据 2026-11-20 对 honk 文档与 raksmart 现场（Sub-Store/komari）的实测研究。

## 0. 已定决策记录

| 决策点 | 结论 | 理由 |
|---|---|---|
| `proxy` 组策略 | **先 `score`**，不稳再回退 `min_moving_avg` | 回退成本 = 改一行 + 热重载；score 语义（30min 半衰期业务成功率 + 目标质量 + 在任者保护/探索预算）最贴近"网络稳定走 bitsflow" |
| 订阅入口 | **Sub-Store 统一入口，但用每 VPS 单订阅导出**（`/download/<name>`），不用 collection+region 切片 | region 标签是自动分组（US15/DE25 前科不稳）；单订阅导出的 tag 即池身份（subtag 过滤），节点改名/换协议不影响；不经 collection 的 profile 管道，"profile=all"天然成立 |
| `premium` 组 | **selector（fixed）+ 外部脚本按月流量切换** | 配额是商务策略不是网络状态；selector 选择持久化于 `state/honk.db`（跨 reload/重启），`honk-core proxy <组> <成员>` 一条命令切换 |
| nosla | 不订阅，自动绝迹 | 退役中 |
| 池划分 | us = raksmart(+lightlayer)、jp = bitsflow | 用户指定；lightlayer 去留不影响结构（删一行 filter/订阅即可） |

## 1. 目标拓扑与行为矩阵

| 流量 | 组 | 默认 | 切换条件（脚本） |
|---|---|---|---|
| AI/质量敏感（geosite:anthropic、openai、paypal 等） | `premium` | us（raksmart） | raksmart 剩余流量 < 10% → jp；回升 ≥ 20% → 回 us（滞回） |
| 日常（fallback） | `proxy`(score) | 自动 | 纯网络判断，脚本不干预；bitsflow 稳定时天然占优（JP 低延迟），劣化自动让位 raksmart |
| fallback DoH | `doh`(min_moving_avg) | 自动 | 不变（跨 VPS + 双协议） |

附加保险（可选实现）：脚本同时检查 us 池整体健康（clash API `GET /proxies/us`），全灭时也切 premium → jp——把"配额不足"与"整池故障"收进同一闭环。

## 2. 前置条件（执行前逐项确认）

- [x] **bitsflow 节点可用**：Sub-Store 已有名为 `bitsflow` 的单订阅（2026-11-20 实测确认存在），执行时先 `curl` 验证 `/download/bitsflow` 返回节点 → 三条 `/download/<name>` 全 200/0.7-1.0s，bitsflow 2 节点（hy2 固定口 47625 + reality-443，域名单点解析到其公网 IP）
- [x] **lightlayer 去留**：保留则订阅 `lightlayer` 并入 us 池；不保留则相关行全删 → **保留**，us=raksmart(2)+lightlayer(2)
- [x] **komari 可达**：router-home 能访问 komari 公网地址（openresty 反代 raksmart:25774；域名执行时从 DNS/1Panel 确认，下称 `KOMARI_BASE`）。若域名被墙：komari 在 raksmart 本机，可走 honk 代理（curl 经 `fallback: proxy` 自动生效）或 `--resolve` 直钉 raksmart IP → **KOMARI_BASE = https://monitor.haoqi90.top**（raksmart 1Panel/openresty `monitor.conf` → 127.0.0.1:25774），router-home 直连可达
- [x] 用户在家、**经 LAN 直连 router-home**（本次变更为 reload 级 + 订阅重建，有短暂 fail-closed 窗口，不走 tailnet 作业）

## 3. komari 能力清单（2026-11-20 对 raksmart :25774 实测）

| 能力 | 端点 | 实测结果 | 结论 |
|---|---|---|---|
| 节点清单/限额 | `GET /api/nodes` | 200，**公开无鉴权**；字段：`name`/`uuid`/`traffic_limit`(=1073741824000，整 1TiB)/`traffic_limit_type`('sum'，上下行合并计)/`billing_cycle`(0)/`expired_at`/`auto_renewal` | ✅ 直接可用 |
| 累计流量 | `GET /api/recent/{uuid}` | 200，公开；`data[0].network.totalUp/totalDown`（实测 Raksmart ≈21.2G/23.2G） | ⚠️ **周期语义待验证**（见下） |
| 月度用量真值 | — | 未在本版公开 API 中直接暴露 | 执行期按下述决策树定 |

**月度用量来源决策树（执行时必须先走完）**：

1. 对拍验证：`totalUp+totalDown` 与 komari 面板节点卡片显示的月流量是否一致，且月初（或 agent 重启后）是否按计费周期归零——一致则直接用 `/api/recent`
2. 若 totals 是"agent 启动以来"累计（与面板对不上）：改用**管理 API**——komari 后台生成 Admin Token（设置页），脚本带 token 调 `/api/admin/*` 取真实月记录（执行时 curl 探明具体端点与鉴权头）
3. 兜底：脚本自累计（每次采样 ratchet 累加，周期日重置）——冷启动有盲区，仅在前两条都不可行时用

计费周期字段 `billing_cycle: 0` 的具体含义（0=自然月？）同样在执行期从面板配置确认，确保"月初自动回切"成立。

### 3.1 决策树最终结论（2026-11-20 执行期钉死）

**走第 1 条路：`/api/recent` 直接可用，但有两处修正。**

1. **月度语义成立，机制与预想不同**：komari-agent（raksmart 上）以 `--month-rotate 16` 启动（TZ=UTC），agent 侧维护按 2s 采样、10min 落盘的流量时间序列（`net_static.json`，bind mount `/opt/1panel/docker/compose/komari-agent/traffic-data/`，watchtower 重建容器不丢），`totalUp/totalDown = GetTotalTrafficBetween(上次重置日, now)` —— **真·月度值，计费周期 = 每月 16 日 00:00 UTC**（非自然月！）。uptime 字段是宿主机口径（agent 容器重启不清零计数）。
2. **对拍成立（源码级）**：komari 服务端告警（`utils/notifier/traffic.go`）与首页流量进度条都直接消费 latest report 的 `totalUp/totalDown`，与 `/api/recent` 同源同值，无独立月度账本。admin API 不需要。
3. **冷启动缺口（需补偿）**：raksmart 的序列文件 2026-11-19 02:26 UTC 才开始持久化，本周期（11-16 起）缺失 ~36G（ens7 since-boot 88.6G − 序列累计 52.9G 实测推得）。**脚本加自过期 bootstrap offset 36GiB（至 2026-12-16T00:00Z 失效）**；下周期起序列完整覆盖，无需人工清理。
4. **`billing_cycle: 0` 是订阅周期展示字段，与流量重置无关**；"月初自动回切"由 16 日重置后剩余率回升 ≥20% 达成。
5. 遗留假设：bitsflow 侧 agent 的 `--month-rotate` 无法 ssh 验证（假定与 raksmart 同模板部署）；其数字仅作监控告警（jp 无处可切场景），风险可接受。

## 4. honk 配置变更（/etc/honk/config.dae）

### 4.1 subscription（替换现有单条）

```text
subscription {
    # Sub-Store 统一入口的每 VPS 单订阅导出；tag 即池身份
    # token 同现有 MySub 的路径 token（执行时从 /root/honk-migration-20261120 或现有 config 取）
    raksmart:   'https://substore.haoqi90.top/<TOKEN>/download/raksmart'   { route: direct }
    lightlayer: 'https://substore.haoqi90.top/<TOKEN>/download/lightlayer' { route: direct }
    bitsflow:   'https://substore.haoqi90.top/<TOKEN>/download/bitsflow'   { route: direct }
}
```

### 4.2 group（整体替换）

```text
group {
    us {                                   # 美国优化线池（raksmart[+lightlayer]）
        filter: subtag(raksmart)
        filter: subtag(lightlayer)
        policy: min_moving_avg
        tolerance: 50
    }
    jp {                                   # 日本低延迟线池（bitsflow）
        filter: subtag(bitsflow)
        policy: min_moving_avg
        tolerance: 50
    }
    proxy {                                # 日常：score 先行，不稳回退 min_moving_avg（一行）
        filter: group(us)
        filter: group(jp)
        policy: score
    }
    premium {                              # AI/质量敏感：selector，脚本按配额切 us|jp
        filter: group(us)
        filter: group(jp)
        policy: fixed
        default: us
    }
    doh {                                  # 维持跨 VPS + 双协议
        filter: group(us)
        filter: group(jp)
        policy: min_moving_avg
    }
}
```

> 语法注意（实测/文档）：`group(...)` 必须独占整个 filter 值；多行 filter 是 OR；selector 的 `default:` 字段与 `fixed` 搭配需 `config validate` 实测通过（不通过则去掉 default，初始选择用脚本 `honk-core proxy premium us` 钉一次，选择本就持久化）。selector 成员可为嵌套组，运行时 `honk-core proxy premium jp` 即切。

### 4.3 routing（仅两处）

```text
# premium 特例行替换为（geosite 列表按需增删）：
domain(geosite:anthropic, geosite:openai, geosite:paypal) -> premium
# fallback 行目标不变：
fallback: proxy
```

其余全部保持现状（tailnet 保命、节点入口直连、doh 钉住、reality 443、CN 直连等）。节点入口直连规则需**补 bitsflow**：hy2 固定口 dip/sip + 跳跃段（如启用）+ reality 443 的 IP 列表加入 bitsflow 公网 IP（从 `/download/bitsflow` 节点信息取）。

## 5. 配额切换脚本（新文件）

- 位置：router-home `/root/honk-quota/quota-switch.sh`（+ 配置 `/root/honk-quota/config.sh`：KOMARI_BASE、节点名映射 {Raksmart→us, Bitsflow→jp}、阈值 T_HIGH=20% T_LOW=10%、Admin Token 可选）
- 逻辑（幂等，每次运行）：
  1. `GET $KOMARI_BASE/api/nodes` → 找 Raksmart 的 `traffic_limit`；`GET /api/recent/<uuid>`（或 admin API/自累计，按 §3 决策树）→ 本月用量 → 剩余率
  2. 同法算 bitsflow 剩余率（仅监控告警用）
  3. 决策：`剩余率 ≥ T_HIGH → target=us`；`< T_LOW → target=jp`；区间内 → 维持现状（滞回）；可选：`剩余量/剩余天数 < 近7日日均` 提前切 jp
  4. 保险：`curl -H "Authorization: Bearer <clash_secret>" 127.0.0.1:9090/proxies/us` 查 us 池是否有存活成员，全灭且 target=us → 实际切 jp 并 `logger` 告警
  5. 目标 ≠ 当前（clash API `now`）时执行 `honk-core proxy premium <target>`；bitsflow 剩余率 < 10% 时 `logger -s` 告警（无处可切）
  6. 全程 `logger -t honk-quota` 留痕；任一步失败静默退出非零（下次 cron 重试）
- cron：`*/30 * * * * /root/honk-quota/quota-switch.sh >/dev/null 2>&1`（追加到 crontab，注意别动已注释的 passwall2 行）

## 6. 执行顺序（回家 LAN 后）

1. 备份 `/etc/honk/config.dae` → `/root/honk-quota/config.dae.bak-$(date +%s)`
2. **§3 决策树走完**（komari 月度用量来源钉死、KOMARI_BASE 确认、从路由器 curl 通）
3. 预取验证：三条 `/download/<name>` 订阅 URL 从 router-home `curl -m 10` 各返回节点（`route: direct`）
4. 写新 config → `honk-core config validate` 通过（重点验 `default: us` 语法）
5. **watchdog 布防**（复用首部署套路）：nohup 420s 后 `cp 备份回 config.dae && /etc/init.d/honk restart` 的自杀定时
6. `honk-core reload` → 验证：三池节点数、proxy/premium/doh 成员、google 204、DNS、tailscale、cloudflared
7. 解除 watchdog → 部署 quota-switch 脚本 + config → 手动跑一次看 logger 输出 → 加 cron
8. 观察 score 行为（见 §7），仓库回写实施记录 + push

## 7. score 稳定性观察与回退判据（观察 3–7 天）

| 症状 | 判据 |
|---|---|
| 出口漂移 | 日常时段（非节点劣化）`proxy` 组 `now` 每日变化 > 数次且伴随会话中断 |
| 首包慢 | 空闲后首请求明显劣于 min_moving_avg 时期（探索预算开销） |
| 长会话被切 | SSH/下载类长连接频繁断（score 切换不复位既有流） |

命中任一 → `policy: score` 改回 `min_moving_avg` + `honk-core reload`（一行回退，不动其他），README 记录结论。

## 8. 验证清单

- [x] us/jp 池节点数正确（raksmart 2、lightlayer 2、bitsflow 2；nosla 为 0）
- [x] premium 切换后 AI 域名走目标池：jp 时组延迟对 chatgpt trace 52ms（jp=51/us=157 实锤 JP 出口）、bitsflow-reality 直测 api.openai.com 230ms 通；us 时 openai 401/chatgpt 200 正常抵达。出口 IP 直证（cdn-cgi/trace）受 chatgpt.com:443 既有连接池复用干扰不可靠，以组延迟+连接链路（/connections chains 含 premium）为准
- [x] 阈值触发滞回：T_HIGH=99/T_LOW=95 → us→jp；区间 [50,97) → hold jp；恢复 20/10 → jp→us（全 logread 留痕）
- [x] komari 月度数字与面板卡片一致（源码级同源证明，见 §3.1；无独立月度账本可对拍）
- [x] cron 生效（临时改每分钟实跑一次见 logger，恢复 */30）、logger 有痕、bitsflow 告警路径可用（JP_WARN_PCT=99.9 触发 logger -s 实证）
- [x] reload 后 selector 选择保持（PUT jp → honk-core reload → now 仍 jp）

## 10. 实施结果（2026-11-20 晚，当日完成）

**§6 全序执行完毕，与计划的偏差与发现：**

1. **`honk-core config validate` 不做 filter 表达式语义检查**（故意写错函数名 `subtagx()` 仍 validate 通过）——filter 解析失败在 honk 里是"忽略该行 + 组变空"（fail-closed 方向），validate 绿灯≠安全。本次改为对 v0.1.0-beta.6 源码逐项核实（`subtag()`/`group()` 独占整行/`policy: score|fixed|min_moving_avg`/`tolerance`/`default` 均支持）后才 reload。**后续改 filter 必须看 logread 有无 "could not parse this filter" 类告警**。
2. **`honk-core proxy premium jp` CLI 报 "Node 'jp' not found"**——proxy 子命令不接受嵌套组成员。quota-switch.sh 改用 clash API `PUT /proxies/premium {"name":"jp"}`（204 生效），效果等同且选择持久化不变。
3. `default: us` 语法 validate + 实际行为均通过（reload 后初始 now=us），无需删掉、无需脚本钉初始选择。
4. bitsflow 直连规则按实测补全（IP 为其域名单点解析的公网地址，真实值只在路由器 config）：hy2 固定口 **47625** dip/sip、跳跃段 48000-50000 双向、reality 443 加入既有 IP 列表行。
5. watchdog 420s 布防→reload→验证全绿→解除（未触发）。备份 `/root/honk-quota/config.dae.bak-1795183476`。
6. **premium 组流量分发实测正常**（LAN 客户端 chatgpt.com 链路 chains=[节点,us,premium]、rule=geosite）；selector 切换不复位既有长连接（既有流保持原出口，新流走新选择）。
7. **意外发现（与本次变更无关的既有问题）**：router 本机 `127.0.0.1:53`（dnsmasq→ISP 明文上游，pname 直连豁免路径）解析 openai/google 得到 GFW 污染答案（Facebook/Twitter IP）；污染 **IPv6** 目标时 curl 直连假 IP 挂起（本机 v6 出流未走 honk 的迹象），v4 因 SNI 嗅探+按域重拨不受影响。LAN/tailnet 客户端路径（honk 劫持→doh 组）解析完全干净。已记 README 已知观察；如需根治可给 dnsmasq 配 noresolv+上游钉 honk 或压 AAAA，另立任务。
8. **score 保留**：proxy 组即 score 策略上线，首晚表现正常（google 204 0.25s、LAN 会话无感），§7 三项判据留待日常观察，未回退。
9. quota-switch 部署细节：`/root/honk-quota/{quota-switch.sh,config.sh}`（含 §3.1 的 bootstrap offset）、cron `*/30`（已注释的 passwall2 行原样未动）、us 池保险从"组延迟探测"改为"遍历成员任一可达"（urltest 瞬时选中死节点会误报整池故障）。当前读数：us 剩余 91.5%（含 offset）、jp 99.6%、premium=us。

## 9. 执行 prompt（回家后整段粘贴给助手）

```text
请阅读并执行 /home/jelinenaro/code/scripts/homelab/openwrt-honk/quota-aware-policy-plan.md
（流量感知节点策略，计划已定稿、router-home 尚未做任何变更）。我现在在家，已通过
LAN 直连 router-home（ssh profile: router-home），plan §2 的前置条件视为满足。

现值来源（需要时自行取用，不要向我要）：
- Sub-Store token：router-home /etc/honk/config.dae 现有订阅行里；
- komari 地址：ssh profile raksmart 上 1Panel 站点/openresty 反代确认域名
  （容器端口 127.0.0.1:25774），拿不到域名就 --resolve 直钉 raksmart IP。

执行要求：
1. 严格按 §6 顺序；动配置前先备份，reload 前必须 watchdog 布防（§6.5），全绿才解除；
2. 先走完 §3 komari 月度用量决策树（totalUp/Down 与面板卡片对拍；不对则 admin API
   或脚本自累计），把结论写进脚本配置再继续；
3. §4 配置：订阅用 Sub-Store 每 VPS 单订阅导出（raksmart/lightlayer/bitsflow 三条，
   route: direct），池身份只用 subtag，禁止引入 name() 类脆弱过滤；premium=selector
   默认 us，`default:` 语法若 validate 不过就删掉、改用脚本 honk-core proxy 钉初始选择；
4. bitsflow 的节点入口直连规则（hy2 固定口 dip/sip、跳跃段、reality 443 IP 列表）
   按订阅返回的实际节点信息补全；raksmart/lightlayer 现有直连规则保持不动；
5. 部署 §5 quota-switch 脚本 + config + cron（T_HIGH=20% T_LOW=10%），手动跑一次
   看切换与滞回（可临时调阈值触发，验完恢复正常值）；
6. 红线：不动 passwall2 退役状态与其已注释的 cron 行；不动 tailnet 保命规则；
   任何一步失败立即用备份回滚 + honk-core reload 并如实报告，不要硬闯；
7. 收尾：勾选 §8 验证清单，把实施结果（含 komari 决策树的最终结论、score 是否
   保留）回写本 plan 和同目录 README，git commit + push，并更新记忆
   honk-home-router-deployment（待执行 → 已执行 + 关键结论）。
score 的日常稳定性观察（§7）不在本次范围，留待使用中判断。
```
