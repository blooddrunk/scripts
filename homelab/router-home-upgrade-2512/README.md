# router-home iStoreOS 24.10 → 25.12 升级 runbook

- **日期**：2026-11-19（执行）/ 2026-11-19（记录）
- **设备**：router-home（ssh profile，`root@192.168.100.1`，密钥 `~/.ssh/id_ed25519`），Intel N150 虚拟机（QEMU Q35，EFI 启动），系统盘 sdb→**新系统枚举为 sda**（2.4G），数据盘 sda→**新系统 sdb**（16G ext4，挂 `/mnt/vio4-1`）
- **升级路径**：iStoreOS 24.10.8-2026073111 → **iStoreOS 25.12.6-2026110611**（`sysupgrade -n` 全清配置，OpenWrt 25.12.6 底座，内核 6.6.144 → 6.12.112，包管理 opkg → apk）
- **备份**（升级前 2026-11-19 13:35 UTC）：
  - 本地：`homelab/router-home-upgrade-2512/backup-20261119-133545/`（gitignore）
  - 异地：`raksmart:/root/router-home-backups/`（同内容）
  - 内容：`sysupgrade -b` 全量包、定向包（/etc/tailscale、/etc/config 全量、init.d/tailscale-*、watchdog、dropbear、shadow、crontabs、/root/.ssh、bootstrap 与 vps-proxy-rollout 目录）、docker 补充包（/root/iptv-api bind-mount 目录 + 三容器 inspect JSON）、opkg 已装清单

## 时间线

| 时间 (CST) | 事件 |
|---|---|
| 21:3x | 只读体检 + 三份备份（本地 + raksmart） |
| 21:4x | 预下载官方 apk、Are-u-ok .run、固件 ×2（BIOS 版，后证明用错） |
| 22:02 | 第一次 sysupgrade 被 `/lib/upgrade/platform.sh` 拒绝：**"Flashing a non EFI image to EFI system!"** |
| 22:0x | 改从 `fw.koolcenter.com/iStoreOS/x86_64_efi/` 取 **-efi.img.gz**（本地 scp 到路由器仅 74KB/s，改由路由器自 wget koolcenter，秒下） |
| 22:1x | 第二次 sysupgrade -n 成功，22:2x 回网 25.12.6；**用户手动在 LuCI 配好 PPPoE**（账号不变） |
| 23:0x | 恢复 authorized_keys + 旧 root shadow 哈希；补 Modem 口（eth0 静态 192.168.1.254，wan zone）；dhcp 静态租约 ×4 |
| 23:05 | tailscale：恢复 tailscaled.state + tailscale-bootstrap → install/apply/enable-site-to-site/enable-failover --failback true，一次通过 |
| 23:08-23:10 | passwall2：官方 26.11.11 apk 套件（raksmart:8899 HTTP 中转，路由器直连 GitHub 不通）+ 恢复 /etc/config/passwall2 |
| 23:10 | **docker 零操作自愈**：iStoreOS 25.12 自带 docker 并自动识别 `/mnt/vio4-1/docker`，三容器（watchtower/cloudflared/iptv-api）随 restart=unless-stopped 自启，cloudflared 隧道重连 |
| 23:15 | 订阅源直连 CF 超时 → `access_mode='proxy'` 后订阅成功，hy2 节点全部匹配 |
| 23:2x | 排查 LAN 透明代理黑洞 → `tcp_proxy_way` tproxy→**redirect** 修复；全项验收通过 |

## 恢复明细（按序）

1. **登录凭据**：root 空密码首登（dropbear none-auth 可用）→ 写回旧 shadow root 哈希 + `/etc/dropbear/authorized_keys` → ssh profile 密钥登录复活
2. **PPPoE**：用户 LuCI 手工配置（wan pppoe@eth0 + wan6 dhcpv6）；Modem 口由脚本补：`uci set network.Modem`（static/eth0/192.168.1.254/24）+ wan zone `list network 'Modem'`，光猫 192.168.1.1 可达
3. **tailscale**（仓库 `../headscale-openwrt-bootstrap/`）：先 `watchdog.enabled=0` 绕过自检 → 恢复 state/bootstrap 配置 → `install --login-server https://hs-raksmart.haoqi90.top` → `apply` → `enable-site-to-site`（已含子网广播 192.168.100.0/24）→ `enable-failover --failback true`。身份未变，headscale 侧路由审批无需重做
4. **passwall2**：官方 26.11.11-1 apk（luci-app + xray-core 26.11.9 + sing-box 1.14.4 + chinadns-ng + geoview + v2ray geodata + tcping）`apk add --allow-untrusted` 本地安装；恢复 `/etc/config/passwall2`(+`_server`)；hysteria 二进制 GitHub latest 静态版手工放 `/usr/bin/hysteria`（25.12 feed 已移除 hysteria，且 Are-u-ok .run 也不含）
5. **docker**：确认自愈 + 补 `dockerd.globals.registry_mirrors += https://registry.linkease.net:5443`
6. **crontab**：无需手工恢复，passwall2 26.11 启动时自行重写（4:00 重启 / 5:00 订阅，语义同旧）

## 验收结果（23:2x 全过）

- PPPoE up、国内 ping 6ms、光猫可达、DHCP 31 租约、DNS 正常
- 透明代理：LAN 客户端 v4 国外 200（出口 nosla 82.139.215.223）、socks 1070 出口同、UDP 国外正常
- tailscale：ControlURL=hs-raksmart、accept-routes、子网广播、watchdog 运行、双 headscale /health pass、kwrt-1 经 tokyo DERP 活跃
- docker：三容器 Up；cloudflared 隧道 lax01 注册
- 开机自启：tailscale-core(S90)/tailscale-failover(S95)/passwall2(S99)/dockerd(S99)

## 坑与差异（下次升级必读）

1. **x86 EFI 镜像在独立目录** `fw.koolcenter.com/iStoreOS/x86_64_efi/`（文件名带 `-efi`）；普通 `x86_64/` 目录是 BIOS 版，EFI 机器上 sysupgrade 会拒绝。平台判定：`part_magic_efi`（GPT）
2. **网卡枚举翻转**：24.10 的 WAN=eth1/br-lan=eth0 → 25.12 的 WAN=eth0/br-lan=eth1。跨大版本升级后**不要盲目恢复旧 network 配置**，以新系统实际端口为准
3. **passwall2 26.8.27+ 配置结构变更、无自动迁移**：`*_no_redir_ports` 选项已删（残留 'disable' 会漏进 nft 规则报错）；Hysteria2 独立节点类型已删（订阅更新会以新类型重建）；恢复旧配置后需删失效选项并重跑订阅
4. **内核 6.12 上恢复的 tproxy TCP 黑洞**：`tcp_proxy_way='tproxy'`（24.10 可用）在 25.12 上 LAN 转发流量全部超时，nft 表/999 策略路由/xray 监听均正常但流量不到 xray；切 **`redirect`** 立即恢复。UDP 保持 tproxy 正常
5. **订阅需走代理**：substore.haoqi90.top（CF）直连 000 超时、socks 代理 200 → `sub_hBFac.access_mode='proxy'`
6. **路由器直连 GitHub 完全不通**（无代理时）；大文件搬运走 raksmart 临时 HTTP 中转（`python3 -m http.server 8899`，用完即关）。本机→路由器 scp 仅 74KB/s（WiFi 链路），路由器自下 koolcenter 是秒级
7. **docker 全自动恢复**：数据盘不随 sysupgrade 变动，25.12 自带 docker 栈自动识别旧 data_root；唯一需补的是 bind-mount 在 overlay 上的 `/root/iptv-api`（docker 自启时会自动创建空 bind-mount 目录顶替，须 `docker stop` → 换回备份目录 → `docker start`，本次恢复后 42456 端口由 404 变 200）
8. 规则重生成时会打印两行 `Error: no match countrycode found`（geoview 查询输出，不影响功能，未深究）
9. tailscale-bootstrap 的 profile 表保留了历史 **hs-nosla（priority 30）**，failover 序 = raksmart(5) > bitsflow(20) > nosla(30)，与用户当前主备意图一致故未动

## 回滚指引（如需退回 24.10）

- 镜像：`staging/istoreos-24.10.8-2026073111-x86-64-squashfs-combined-efi.img.gz`（EFI 版；勿用 BIOS 版）
- 方式 A：25.12 在线时 `sysupgrade -n -v <24.10.8-efi.img.gz>`（平台检查会放行 EFI→EFI）
- 方式 B：无法启动时用 USB 启动盘 dd 写盘
- 刷回后 `sysupgrade -r backup-20261119-133545/sysupgrade-pre2512-*.tar.gz` 恢复全套配置
