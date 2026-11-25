# router-home LAN 侧操作卡（应急恢复 + 物理设备抽查）

tailnet 侧验收与调优已于 2026-11-21 凌晨完成（见
../headscale-openwrt-bootstrap/docs/ops-log-2026-11.md 事件十一）。
本卡片只覆盖**必须人在家（LAN 192.168.100.0/24）**才能做的事。

## 一、应急恢复（仅当远程 tailnet SSH 失联时用）

从家里任一设备 `ssh root@192.168.100.1`（密码/密钥同既有），按序尝试：

```sh
# 1. 看 tailscale 状态与最近日志
tailscale status
logread | grep -iE 'tailscale|wireguard|derp' | tail -30

# 2. 最常见恢复动作：重启 tailscale（身份/路由不受影响）
/etc/init.d/tailscale-core restart
sleep 8 && tailscale status      # 应回到 Running，IP 100.64.0.1

# 3. 若怀疑 honk 规则拦路（tailscale 直连规则失效）：
#    先只 reload 配置；无效再按 README「回滚手册」整体回 passwall2
/usr/bin/honk-core config validate --config /etc/honk/config.dae
/usr/bin/honk-core reload
```

红线：不删 `tailscaled.state`、不 `tailscale logout`、不 `--reset`；
honk 配置改动前先 `cp -p /etc/honk/config.dae /etc/honk/config.dae.bak-<时间>`。

## 二、物理设备抽查（一次性 5–10 分钟，验收清单遗留项）

tailnet 远程测不到 br-lan 拦截面，需要手边设备确认：

1. **LAN 客户端的 Tailscale 直连生效**（2026-11-21 新规则）：
   - 手机/PC 连家庭 Wi-Fi，开 Tailscale；
   - `tailscale status`（或 App 内）看对公司节点应出现 `direct`
     （此前 LAN 客户端的 DERP/控制流量走 honk 代理出口）；
   - 断开家里光猫直连测试可选：honk 停 30 秒
     （`/etc/init.d/honk stop` → 测 → `start`），期间 Tailscale
     设备应保持在线（这就是新规则的意义）。
2. **DNS 抽查**（honk README 验证清单遗留）：
   - LAN 设备上 `nslookup openai.com` → 应为 Cloudflare 真段
     （104.x），而非假 IPv6/污染 IP；
   - `ping router.lan` 或访问 `http://router.lan` → `.lan` 本地域名
     应正常解析（dnsmasq 本地记录，不被 honk 接管）；
   - 访问一个国内站（baidu.com）→ 正常直连速度。
3. **doona 面板直达**：浏览器开 `http://192.168.100.1:9527/ui/`
   （password_auth，凭据在 `/root/honk-migration-20261120/panel-credentials.txt`）。
4. **IPv6 不绕过代理**（2026-11-24 审查补充，tailnet 侧测不到 br-lan 的 v6 路径）：
   找一台有全局 IPv6 地址的 LAN 设备，关闭其自带代理，浏览器访问
   `https://ipv6.icanhazip.com` 或 `https://test-ipv6.com` 的出口 IP 页 →
   应显示节点的 IPv6 出口，或干脆打不开（节点是 IPv4-only，fail-closed）；
   **绝不应显示家宽的 2409: 开头地址**。

## 三、已知无需处理的现象

- netcheck 里自建区 `raksmart` 无 STUN 延迟：raksmart→国内方向回程
  UDP 被运营商丢（既有结论），官方 Tokyo 兜底，DERP 999 握手实测正常。
- `raksmart-reality` 节点 DataUdp 检测 dead：同上，TCP 承载不受影响。
- honk 偶发 "doh has no available node" WARN：检测窗口瞬时全灭会自愈，
  持续出现才需要查（见 README 已知观察）。
