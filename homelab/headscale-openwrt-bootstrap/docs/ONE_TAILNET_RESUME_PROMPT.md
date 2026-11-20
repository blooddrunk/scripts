# 回家后继续执行的提示词

用户回家后可复制下面整段，交给同一工作区中的 coding agent。这里记录的
线上状态是 2026-11-20 暂停前的观察，继续时必须重新核实。

```text
我已回家。请继续完成 blooddrunk/scripts 的单 Tailnet 迁移；此前工作在
家庭路由器管理通道尚未恢复时暂停。先阅读：

- homelab/headscale-openwrt-bootstrap/docs/ONE_TAILNET_2026-11-20_EXECUTION.md
- homelab/headscale-openwrt-bootstrap/docs/ONE_TAILNET_PORTABLE_CONTROL_PLANE.md
- homelab/headscale-openwrt-bootstrap/docs/ONE_TAILNET_ACCESS.md
- homelab/openwrt-dns-daed/README.md 的单 Tailnet 和远程恢复说明

承接现有成果，以原请求的最多两个紧凑迭代完成，不要重做已验证步骤。
先检查 Git 工作区、现有本地提交和最新 main，安全保留所有未提交工作；
不要为了同步而 reset、丢弃提交或覆盖用户改动。暂停时已有本地提交
62a918b，尚未推送。下面的设备和服务状态是历史观察，不是当前状态。

第一优先级是恢复家庭路由器的 WireGuard 和 SSH 管理通道：此前它的
Headscale 控制连接在线，节点私钥对应的公钥、IP 和已批准路由与备份
一致，disco ping 成功，但 TSMP/WireGuard 和 SSH 均超时。我已回家；
请先自行检查本机网络、已有 SSH 配置及家庭 LAN 可达性。若能确认有
不依赖 Tailnet 的家庭 LAN/控制台入口，先只读检查 tailscaled、接口、
路由、防火墙和日志；需要时在保留该独立入口的前提下，安全重启家庭
路由器的 tailscale-core。若仍需我提供入口或操作本地控制台，说明
精确的缺失条件和步骤，不要索要密码或密钥。恢复后必须验证真实的
TSMP/WireGuard、SSH、预期子网路由和对端访问，不能把 disco ping 或
控制面 /health 当作数据面通过。不要删除或重置 tailscaled.state，
不要未经备份 logout、purge 或重新注册身份。家庭路由器使用 honk；
Passwall2 是我有意停用的，继续保持停用，不要停止 honk。

保护所有远程管理通道：此前本机连接的 192.168.10.1 公司路由器若
停止 daed，本机就会失去网络；曾有一次需要我手动启动它。不要停止或
重启承载当前运维通道的 daed，除非已经实际验证不依赖它的管理和恢复
路径以及可靠回退。此前两台公司路由器的双 HY2 控制组曾出现候选同时
不可用、HTTPS 间歇失败；请只读定位并安全修复，复核独立 DNS、UDP
3478、针对实际 VPS IP 的主机路由 MTU/MSS、代理以及真实长连接。
不要把当前 SSH 可用或一次 HTTP 200 当作长期稳定或独立恢复的证明。

暂停前 Raksmart 是唯一活动的权威 Headscale，稳定地址为
https://hs.haoqi90.top，旧 hs-raksmart 域名仍保留。上次实际
Raksmart -> Bitsflow 提升在旧域名转发验证失败后、DNS 更新之前回退：
先封锁 Bitsflow，再从它的最新停机快照恢复 Raksmart。OpenResty 上游
证书验证深度和重载重试已修正，但没有成功完成新的真实切换。Bitsflow
的主 Headscale 被 mask；为恢复旧 DERP 901，那里有仅开放 DERP、使用
独立空数据库且控制监听仅回环的恢复实例和已启用的持久单元，主服务
被 mask 时才允许启动，重启未实测；它占用 UDP/3478。提升工具现在会
在目标有该服务或 DERP 区域与目标 IP 冲突时拒绝操作。请先弄清实际
DERP 拓扑及如何安全处理 901、999 和官方兜底，避免两个区域指向同一
后端造成不可靠连接；不能简单移除仍在使用的中继，也不能把 HTTP
探测当作 DERP/WireGuard 验收。

家庭管理通道和公司控制出口恢复稳定之后，重新核验 DNS、TLS、
Headscale/Headplane、节点身份、密钥、路由和各设备，生成并异地校验
最新一致性加密备份；保留每次实际操作的自动或可执行回滚。完成实际
受控提升与恢复演练：先能证明旧主持久 fencing，再恢复新主，确认唯一
权威写入者、旧域名兼容、Headplane、DERP 和真实客户端后才切 DNS；
目标可能已经写入时，回退必须先封锁目标并从其最新快照恢复，无法
确认目标封锁时不要重启旧主。不要合并两套 SQLite、重置节点身份或
声称自动选主。确保未来服务端提升与回退流程可复用，记录 RPO 和
剩余限制；证书续期和双机部署此前尚未自动化。
此前稳定域名的网站与证书只是直接写入 1Panel OpenResty 文件，未
登记到 1Panel 的网站/证书数据库，用户在面板中看不到。请在保护现有
443/Xray 分流并准备回退的前提下，将其安全纳入面板管理和续期流程；
不要直接重复创建同域名站点，也不要误认为当前证书由 1Panel 托管。

迁移已可安全访问的其余客户端并核实实际 ControlURL：本机 Windows
WORK-PC 已是权威网络节点，用户希望从家中远程连接它；已启用无人值守，
实际远程桌面 TCP 建连和网络级身份验证协议协商通过，账号登录尚未测试。
它仍指向旧 Raksmart
域名，当前网络新建控制面 TLS 连接曾失败，需要保留兼容入口，不要
重复注册或在连接不可靠时贸然切换登录地址。
旧 Bitsflow 的 qi-work-pc 我明确选择暂不处理，完成后我会手动添加；
不要以它的 SSH 或在线迁移为阻塞。旧网络还有离线设备，也应列为
逐台手工加入的例外，不要合并数据库。

受限 Cloudflare token 和加密备份口令已分别保存在运维端
~/.config/headscale-dr/cloudflare-token 和 backup.passphrase，
不要输出、上传或提交它们；加密口令须另存到安全的独立位置。最新
已验证快照、回退操作、证书到期和现场证据见执行记录；不可把旧快照
当作实时复制。优先自动化，测试保持精简，修复代码和文档并记录
脱敏证据，完成后提交；按真实结果说明任何未完成项目和必要人工操作。
```
