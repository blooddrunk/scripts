# 单 Tailnet / 可迁移 Headscale 控制平面：两轮实施计划

状态：**主体完成——双向提升演练与公司侧验收已过，旧控制域名 hs-raksmart/hs-bitsflow 已于 2026-11-21 退役，router-home 调优与离线设备待办**
制定日期：2026-11-20  
仓库：blooddrunk/scripts  
权威网络：Raksmart 现有 Headscale 数据与节点；Bitsflow 现有 Headscale 作为独立旧网络，仅供过渡与归档  
目标控制地址：**https://hs.haoqi90.top**（长期固定，与任何 VPS 主机名解耦）

2026-11-20 的实际变更、回退、生产演练与公司侧验收证据见
[执行记录](ONE_TAILNET_2026-11-20_EXECUTION.md)。以下为原计划，不能将
计划中的目标视为已经全部验收。日常操作（Headplane 登录、加设备、
常用命令）见[快速开始](ONE_TAILNET_QUICKSTART.md)。

## 0. 先明确保证范围

- **支持的目标**：完成首次迁移后，未来 VPS 更换时只在服务端执行一致性备份/恢复、HTTPS 站点准备、DNS 指向切换。若维持相同的 Headscale 数据库、Noise 私钥、配置、节点密钥状态和稳定 server_url，**已注册到该稳定 URL 的客户端通常无需重新加入或修改 LoginServer**。DNS 缓存、短时服务中断和客户端重连仍不可避免；**不承诺零中断、零数据丢失或自动无条件接管**。
- **首次迁移与日后更换不同**：当前客户端可能还保存 hs-raksmart.haoqi90.top 或 hs-bitsflow.haoqi90.top 作为 ControlURL。更改 Headscale server_url **不会自动改写客户端 LoginServer**。必须检查各设备 profile/ControlURL；根据实测，迁移到新 URL 可能需要一次客户端操作甚至重新认证。保持旧域名反代兼容，逐台小批切换，不要强行 purge 身份。
- **两个既有 Tailnet 不能合并 SQLite 数据库**：Raksmart 数据为唯一权威，不拼接 Bitsflow 的 node/user/route 表；只注册在 Bitsflow 的节点必须逐个加入权威 Tailnet。迁移完成后 Bitsflow 旧数据库归档（加密保留），不再同时提供第二套活跃 Tailnet。
- **不要混淆三种“自动”**：① DNS 指向新 VPS 后客户端自行恢复控制面连接是本期目标；② 运维 agent 使用一个命令/脚本完成备份、准备、提升主机和 DNS 切换是本期目标；③ 无人监管、仅凭 /health 触发自动主备选举 **不在本期范围内**（无法在有限迭代内可靠解决旧主 fencing、数据新鲜度和网络分区导致的双主）。
- Tailscale 在控制面短时故障时，已有部分 P2P 数据面仍可工作，但新节点注册/密钥刷新/策略变更不可用；不要用 /health 断言整个网络已不可用。
- **仅一个 Headscale 写入者和唯一活动控制平面**。备用机存一致性快照，不共享在线 SQLite、不做 PostgreSQL 多活、不在活动主机上的数据被写入时把裸 db.sqlite rsync 到正在运行的备用 Headscale。
- 系统仍采用原生 Headscale + systemd、1Panel OpenResty、单实例 Headplane 和 OpenWrt tailscaled。无需 Kubernetes、Keepalived、共享数据库、复杂监控或另一套自研管理面板。

参考：
- Headscale 官方升级和备份：https://headscale.net/stable/setup/upgrade/
- Headscale Noise key 配置：https://headscale.net/stable/ref/configuration-options/
- Tailscale 自定义协调服务器：https://tailscale.com/docs/how-to/set-up-custom-coordination-server
- Tailscale 控制面故障行为：https://tailscale.com/docs/reference/coordination-server-down
- Headplane Native Mode：https://headplane.net/install/native-mode
- Cloudflare DNS API：https://developers.cloudflare.com/api/resources/dns/subresources/records/

## 1. 目标拓扑及职责

~~~text
                     DNS only A/AAAA, TTL 60s where supported
hs.haoqi90.top ───────────────────────────────> 当前 Active VPS
                                                        |
                         1Panel OpenResty (TLS 443, /health, /admin)
                              |                      |
                         Headscale systemd      Headplane systemd
                              |
                SQLite + Noise/DERP keys + config
                              |
                         唯一权威 Tailnet
                              |
            公司 OpenWrt ×2 (daed) + 家用 OpenWrt (Passwall2)
            + 其他已注册客户端（同一 login-server）

当前 Standby VPS：
  预装兼容版本的 Headscale/Headplane、证书、站点和恢复工具
  保留加密一致性备份；Headscale 停止/禁用或明确隔离
  提升时：fence old -> 恢复快照 -> 校验 -> 启动 -> DNS 更新
~~~

- Headscale：单份节点身份、策略、路由批准和 MagicDNS 状态；保留权威来源的原有节点 IP，**不要无意变更 prefixes 或 dns.base_domain**。若当前 MagicDNS 基域带旧 VPS 名，本期也保留，未来另行迁移。
- 1Panel：证书、OpenResty 站点、443 入口；Headscale 原有监听 127.0.0.1:8080、9090、50443 仍只在 loopback。Cloudflare 记录维持 **DNS Only（灰云）**；无不必要的 AAAA 记录。目标 IP 变化时站点证书仍匹配 hs.haoqi90.top。
- Headplane：只在 Active 提供 https://hs.haoqi90.top/admin/ 管理。优先 pin 兼容版本，headscale.url 指向本机 HTTP listener，使用有效 API key、安全 Cookie/鉴权、持久化 data_path，管理端口只监听 loopback；**暂不给配置文件写入权限或 proc 重启权限**。节点/密钥/路由等走 API；如高级功能缺失则不为了开启它而绕开 bootstrap 的配置事务。Headplane 数据可单独备份，丢失不能影响 Tailnet。
- Bootstrap：拥有服务器监听、DERP/MagicDNS 受托配置、配置校验、生命周期、备份/恢复、角色状态。Headplane 负责日常 API 资源，二者不得竞态覆盖 config.yaml。
- OpenWrt：最终单 profile；去除持续性“跨两个独立 Tailnet”故障切换，但保留原 watchdog/注册配置的备份与旧脚本向后兼容。新服务器切换由控制域名 DNS 完成，不要求路由器切换 Profile。
- 嵌入式 DERP：先盘点是否启用。若启用，保留 DERP 私钥和 region identity；提升时更新实际公网 IPv4/可用 IPv6、确认 UDP 3478/STUN、长连接反代、确保官方 DERP map 仍作兜底。**不能把旧 VPS 的 DERP IP 继续下发给客户端**。不在本期增加单独 DERP 集群。

## 2. 迭代一：盘点 + 统一权威网络与稳定域名

### 2.1 agent 只读发现（不计额外迭代）

Agent 可通过已有 SSH 配置访问 Raksmart、Bitsflow、公司 daed 路由器两台、家庭 Passwall2 路由器。不要假设 SSH alias、IP、用户或 sudo 权限；先从本机配置获取，再只读发现。

- VPS：Headscale 版本、systemd、config.yaml 的 server_url/noise.private_key_path、DNS/MagicDNS、policy.mode、DERP 开关及 region、SQLite 路径和 WAL 模式、node/user/route 数量与节点身份摘要、1Panel 站点/证书/端口、OpenResty 配置归属、主机公网 IP、Cloudflare 记录状态。**不输出私钥、数据库、auth key、API token 或完整敏感配置**。
- 路由器：tailscale status --json、tailscale debug prefs、活动 ControlURL/profile、netmap/路由批准、实际远程 SSH 入站所依赖的通道；daed 分组/routing/DNS 和公司网络 MTU 配置；家庭 passwall2、ALL_PROXY、SOCKS 可用性。现网值优先于 README 的历史示例。
- 盘点 Raksmart 与 Bitsflow 的节点是否重合，列出仅在 Bitsflow 的节点（脱敏标识即可）。**不要尝试自动合并两份数据库**。其它未授权或不可 SSH 设备列为首次迁移人工例外清单。
- 检查是否拥有 scoped Cloudflare DNS Edit token（仅 hs.haoqi90.top 所在 zone，尽量限制记录变更权限）；不在仓库保存。缺少 token 时可先实现受控的手动 DNS 更新步骤，不能偷用 1Panel/Cloudflare 全局凭据。

### 2.2 首次切换的安全顺序

1. 在所有服务器、三台路由器做本地备份；Headscale 最终可恢复备份采用短暂停服务冷快照，或 SQLite 官方 Online Backup API 后再做一致性检查，并连同 /etc/headscale、/var/lib/headscale（Noise、DERP 私钥、SQLite/WAL）一起作为**同一逻辑时间点**的恢复集合。目录权限严格控制、加密异地归档；不要把带在线 WAL 的裸 SQLite 文件随意复制成“可恢复备份”。快照具备 manifest、版本、来源、生成时间。
2. Racksmart 首先保持服务可用，不立刻删除旧域名。提前配置 hs.haoqi90.top DNS Only、适用的 TLS 证书和 OpenResty 虚拟主机；保留 hs-raksmart.haoqi90.top 的兼容入口。确认 hs.haoqi90.top 没有与 MagicDNS base_domain 冲突。
3. 调整 bootstrap 的“稳定 control URL / 物理 VPS 身份”分离：Headscale server_url 可固定为 https://hs.haoqi90.top；公网 IP/DERP/证书/健康检查可指向当前 Active 主机。现有 vps_render_config() 使用 VPS_EFFECTIVE_DOMAIN 同时渲染 server_url，且 apply 的公网健康校验依赖域名 DNS，必须拆分并支持 **DNS 切换前对待提升主机的 curl --resolve / SNI 验证**。不可直接盲改 --domain 然后重跑 apply。
4. 在 Raksmart 改 canonical server_url 后先对一个非关键客户端试点，确认 node identity、Tailnet IP、访问权限、route approval 未变化，并记录 ControlURL 是否仍指旧域名；实际需要时只对该客户端以官方支持方式改到新 URL。**不使用 tailscale up --reset、purge-identity、自动 logout**；如版本行为要求重新登录，使用临时 pre-auth key 并优先保留恢复旧 profile 的能力。
5. 分批迁移公司路由器 ×2、家庭路由器及能访问的普通设备，保证每次有非 Tailnet SSH 或自动回滚兜底。只在 Bitsflow 的节点加入 Raksmart 权威 Tailnet；确认远程管理对端节点、LAN 子网和 route approval 全部可用。原 Bitsflow 旧域名过渡期不提前停用。
6. 三台路由器全部确认活动 ControlURL 使用 hs.haoqi90.top、接受预期路由之后，才关闭旧的多 profile watchdog，清理无用的备用 profile；**先备份，再移除，不删除 tailscaled.state**。Bootstrap 的新单网络模式不再要求 ≥2 profiles；保留旧用法向后兼容供回退。
7. 为旧 hs-raksmart 域名保留一段时间的 TLS + 反代兼容（透传 API/长轮询/WebSocket，不依赖浏览器 301/302 跳转）。待发现无旧客户端才停用。hs-bitsflow 的旧身份必须先迁移到 Raksmart 权威库，之后才能决定是否保留别名；旧别名不能让原 Bitsflow 节点“自动并入”新库。

### 2.3 路由器改造：必须和域名切换同批提交

参阅：
- ../README.md “与透明代理共存（daed/dae、passwall2 等）”
- ../../openwrt-dns-daed/README.md “自建 Headscale 场景”“MTU 黑洞加固”
- ../../openwrt-dns-daed/docs/troubleshooting.md 情况 M、N
- ../../openwrt-dns-daed/docs/reapply-runbook.md（远程修改 daed 的自恢复）
- ../../openwrt-dns-daed/examples/routing-extra.dae.example

**两台公司 daed 路由器：**
- DAED_DNS_SELF_DOMAINS 维持自建域名独立 AliDNS DoH 解析，覆盖 hs.haoqi90.top。不能让新控制域名解析依赖 fallback DoH 的单一 VPS 代理组；用可信解析器交叉验证结果。
- 清理已不需要的 hs-raksmart/hs-bitsflow 控制域名专用分流，新增 hs.haoqi90.top 匹配；但旧客户端未迁完前仍保留旧规则。**不能简单把新域名永远锁到 Raksmart 的 tailscale-ctrl 出口**，否则换 VPS 后仍跨故障 VPS 绕行甚至失联。改为有至少两条独立出口的稳态 HY2-only 控制面组，并在主机提升/换 VPS 时自动更新承载组和有效出口；不使用容易“min=0s”反复翻动的 Reality 作为长轮询控制面候选。涉及分组策略时以真实 wing.db 和 daed GUI 能力为准，避免假设其支持未实现的 add_latency。
- 继续保留跨 VPS + 跨协议的 doh 组，保留 UDP/3478 STUN 直连；区分代理节点入口和 Headscale 的 443：仅精确 IP+端口+协议 must_direct，**Reality TCP/443 不放入 NODE_ENDPOINTS 的最顶部规则**，避免抢在 SNI 控制域名之前；更新旧 IP 的 endpoint/sip 规则。
- 公司 WAN 的 TCP:443 MTU 黑洞（历史测试约 1280）必须验证：换主机 IP 后同步更新每 VPS /32 host route MTU 与 MSS 钳制，**不要全局把 WAN MTU 降为 1280**、不要误伤 HY2 UDP；先检查网关、出口设备和新 IP，不能照抄示例中的历史 IP。
- daed 需要完整 restart 才加载 eBPF/Routing；如果 SSH 管理通道依赖 daed，按 reapply-runbook 使用脱离 SSH 的自恢复脚本和延迟兜底启动，绝不裸 stop 后手动 start。完成后验证 PollNetMap、DNS、代理与 Tailnet 路由。

**家庭 Passwall2 路由器：**
- 将控制域名收敛到 hs.haoqi90.top，旧域名在兼容期间保留；tailscale.com 相关官方 DERP 和 UDP/3478 STUN 保持绕过不正确的透明代理转发。规则的域名与 UDP/3478 端口 **必须分开**，否则 Passwall2 的规则 AND 语义可能导致失效。
- 必须保留同机代理服务器 IP 全部排除导致的回环风险：若现有 tailscale-core 依赖 ALL_PROXY=socks5://127.0.0.1:1081 等显式 SOCKS，则验证其可用性后保留/按实测调整，不可简单改为直连或删去环境变量。旧 watchdog 的 https_proxy 随单 Tailnet 停用后再清理，不能提前使远程管理通道失联。
- 变更后真实 tailnet 对端可达；确认 STUN/P2P 与 DERP 路径符合预期，切换期间保留回退点。

## 3. 迭代二：一致性恢复 / 手动触发自动提升 + Headplane

### 3.1 最小 DR 工具（不重写现有 bootstrap）

新增小型专用入口，例如 headscale-dr.sh，重用现有 lib/backup.sh、状态和校验工具，提供：
- doctor：只读显示 Active/Standby、domain/IP、DNS only 状态、备份年龄、版本/身份 fingerprint（只输出 hash 的摘要，不输出私钥）、证书与 proxy 可用性；支持 JSON/机器可读结果。
- snapshot：一致性备份数据库、/etc/headscale 和 /var/lib/headscale、相关版本/策略/DERP 配置、Headplane data_path 可选；加密保存至独立存储并转移到 standby。推荐 systemd timer 定期快照（例如每小时或每天，依据实际变更频率），保留至少若干历史版本，标明 RPO；不得假设快照与实时主机完全一致。
- prepare：在备用 VPS 安装**同一兼容 Headscale 版本**并准备 1Panel 站点、证书和持久目录；将恢复集还原到隔离位置。此时 **Headscale 不启动对外服务**。用本地 configtest、SQLite quick_check、静态检查验明完整性，保留原 Bitsflow 数据加密快照。
- promote：默认先通过独立 SSH/主机管理层停止并 fence 旧 Active（或证明已下线），在新主机恢复完整状态并修正权限、物理 DERP IP，启动 Headscale，确认本地 /health 和 --resolve 指向新 IP 的 TLS+Headscale；随后用受限 Cloudflare API token 更新 hs.haoqi90.top 的 DNS Only A/AAAA（TTL 推荐 60 秒；若不具备 API 则提示人工一步操作）；最后才启用对应 Headplane 管理入口并执行轻量 smoke。
- rollback：若 DNS 更新前失败，不动线上 Active；若更新后失败，仅在保证两个主机 **不会同时写同一 Tailnet 身份** 的情况下回滚 DNS、恢复先前服务；如源主机不可达不能证明 fencing，严禁默默自动 promote，允许清晰标记的 break-glass 选择并记录风险。
- **不执行自动主备健康检测选举**：省去分布式锁、租约和一致性复制；现阶段仅实现“agent/运维命令一键切换”，且新 VPS 不要求预先与用户的旧 VPS 一致。

尽可能复用现有 bash + systemd，避免新服务栈；在日志中脱敏 API key、数据库、Noise 私钥、VPS 密码、Cloudflare token。仅输出无秘密的操作摘要。

### 3.2 Headplane（本期简化版）

- 在 Active VPS 使用 Headplane 官方 Native Mode + systemd，pin 验证过的版本；1Panel 将 /admin/ 反代至 127.0.0.1 的 Headplane，根路径继续为 Headscale；禁止将 Headplane 本地端口暴露公网。
- 首次只要求节点、用户、注册密钥、路由管理可用；先以 **只读 headscale.config_path 或完全不赋写权限**，关闭 proc 自动改配置/重启能力，避免绕过 bootstrap 的 configtest/备份事务。若某功能需要写权限，则保留该项 CLI 管理，后续再设计统一写入流程，不在本期追求全部高级功能。
- 使用短期有效且可轮换的 Headscale API key 登录；必须保证 /admin/ 不是无鉴权公网运维入口。Headplane 的 data_path、Cookie secret 与相关凭据仅入加密备份/主机受限文件，不进 Git。
- 切换 VPS 后新的 Active 才启动其 Headplane；旧 UI 即使尚能访问也不得拥有可写权限。旧 Headplane 宕机绝不能影响 Headscale/Tailnet。
- 不要求本期完成 OIDC、浏览器 SSH 或完整 Settings/DNS 可视化写入。

## 4. 必要且精简的验收（不做重型 CI）

实现以**至多两个紧凑 PR/迭代**完成；测试只做最关键的本地 shell 检查与真实设备 smoke，避免为 CI 性能或全量测试额外单列里程碑。

1. Shell 静态语法、已有最相关 fixture（如果改动涉及），Headscale configtest、SQLite quick_check、备份 manifest 可验证；升级/恢复不跨不兼容版本。
2. 两台 VPS 都能用 curl --resolve hs.haoqi90.top:443:<目标 IP> https://hs.haoqi90.top/health 校验正确 TLS/SNI 和 Headscale；实际 DNS 能解析 Active IP，Cloudflare DNS Only，旧 A/AAAA 没有残留或歧义。
3. 至少一个非关键试点确认旧->新 URL 的真实客户端行为（是否需要一次登录），节点 ID/IP/密钥状态是否保留，不能写“已无感迁移”却没有证据。公司两台 daed、家里 Passwall2 均需验证 BackendState=Running、控制域名、路由批准、实际可达对端及管理 SSH 不失联。
4. 三台路由器：daed 控制面长轮询不反复超时，DoH/代理仍可用，mtu-host-route 精确适用；Passwall2 显式 SOCKS/直连/3478 工作；至少一次 tailscale ping 真实对端，能区分 direct 与 DERP。
5. 一次 Raksmart -> Bitsflow 的**受控提升演练**即可验证主备切换：确认 Headscale 核心身份 fingerprint、节点数量和授权路由未意外改变；在 DNS 更新时间内客户端自行恢复，不要求零掉包。演练失败时执行已准备的安全回滚；**不得在两个主机同时开放同一身份的写入控制面**。
6. Headplane 通过 /admin/ 可登录并查看节点，Headplane 故障不影响 /health 和既有 P2P。保留旧域名兼容入口直到已迁客户端的 ControlURL 完成清点。

## 5. 例外、停止条件与简化边界

- 禁止：删除或重置 tailscaled.state、未经备份移除 profile、把两个 Tailnet 数据库合并、把秘钥提交到公开仓库、静默更换 Headscale Noise 私钥、把活跃 SQLite 裸文件复制到运行中的备用实例、自动启动双主。
- 若 agent 对任一 VPS 无法访问，或管理 SSH 仅依赖将被修改的 tailnet/daed 且未配置自恢复，先修复可回退通道，不要在危险状态下继续。
- 必需人工介入仅限：原设备不支持远程 SSH、客户端认证确实要求交互、首次提供受限 Cloudflare API token、或旧主机失联时无法确认 fencing。列出精确手动步骤，其余由 agent 自行 SSH 检查和执行。
- 每个阶段写入简短证据（脱敏命令、结果、前后版本与结果摘要）和 rollback 命令。无需长测试报告、付费 CI、多轮无意义 Actions。
- **安全审查**：当前 GitHub仓库对外可见，但既有 README 仍提到可把真实凭据归档提交仓库/要求保持私有。先检查历史提交和可访问文件是否泄露过凭据；如发生泄露，按凭据敏感性轮换并清理公开历史风险。后续文档改为任何快照绝不提交，统一在受限加密存储中保管。不要把 SSH 密码、私钥、API token、wing.db、tailscaled.state 或从设备读取的敏感信息写入 PR/日志。

## 6. Coding agent 交付清单

- [x] 修改 bootstrap，使固定 control URL 与 VPS 的物理 host/IP、DERP 及预切换健康检查解耦，保留旧脚本模式兼容。
- [ ] 可复用的 DR snapshot/prepare/promote/rollback 工具、短操作手册与备份 manifest；避免重复建设整套服务管理框架。
- [x] 一次将现有 Raksmart 数据作为权威网络的迁移，单一 https://hs.haoqi90.top 控制入口与 1Panel TLS，保留旧域名兼容路径。
- [x] 公司 daed 路由器两台的规则/状态与新 URL 同步（2026-11-20 深夜
  验收：独立恢复演练通过、raksmart /32 MTU、官方 DERP 直连规则落地）；
  家庭路由器侧的测试与调优仍待执行（honk，Passwall2 已停用）。
- [x] Active Headplane Native 基础管理入口，Standby 侧准备过程不主动提供第二个可写 UI。
- [ ] Raksmart -> Bitsflow 受控提升演练、关键 smoke 和可复用回退方式；最终仅一个活动 Tailnet。
- [x] 更新本目录 README 和 ../../openwrt-dns-daed/README.md 中涉及固定旧控制域名、多 profile、daed 锁出口与敏感备份的文档（2026-11-20 深夜完成，含新增[快速开始](ONE_TAILNET_QUICKSTART.md)与废弃文档清理）。
- [ ] 输出：实际运行结果、遗留人工步骤（若有）、如何在**下一次换 VPS**时仅在服务端执行一次 promote。

**执行原则**：先同步 main 最新代码；先只读采集再改动；可随时用 SSH 调查 Raksmart、Bitsflow、公司 daed×2、家用 Passwall2；优先自动完成、少迭代、最小可用检查，保护远程连接与秘密；遇到不可验证的客户端 URL 迁移行为时先试点，不赌所有设备的结果。
