# scripts

个人工具脚本 monorepo：用途各异、运行环境各异的脚本按领域分组收纳，
一个仓库统一管理。原六个独立仓库已合并（历史经 `git subtree` 完整保留），
旧仓库已归档只读。

## 目录结构

```text
scripts/
├── homelab/            # 家庭网络 + 自建 VPS 基础设施（同一套网络栈，文档互有引用）
│   ├── headscale-openwrt-bootstrap/
│   ├── openwrt-dns-daed/
│   └── substore-operators/
├── vps-order/          # VPS 抢购/下单命令行工具（独立 CLI，不依赖浏览器）
│   ├── lightlayer/
│   └── raksmart-api-sniper/
├── tampermonkey/       # 油猴脚本集（依赖 Tampermonkey 浏览器插件运行）
│   ├── web-ux-freedom/
│   └── raksmart-auto-order/
└── wsl-dev-setup/      # Ubuntu/WSL2 开发环境一键安装
```

## 项目索引

| 项目 | 用途 | 运行环境 | 入口 |
|---|---|---|---|
| [homelab/headscale-openwrt-bootstrap](homelab/headscale-openwrt-bootstrap/) | 自建 Headscale（Tailscale 控制面）部署 + OpenWrt 路由器接入，事务式可回滚 | Debian/Ubuntu VPS + OpenWrt 路由器（POSIX sh） | `headscale-vps.sh` / `tailscale-openwrt.sh` |
| [homelab/openwrt-dns-daed](homelab/openwrt-dns-daed/) | OpenWrt 防 DNS 劫持一键加固（dnsmasq + DoH），并生成 daed 分流配置片段 | OpenWrt 24.10+（busybox ash） | `openwrt-dns-daed.sh` |
| [homelab/substore-operators](homelab/substore-operators/) | Sub-Store 远程 Operator 脚本管线（协议过滤→档案分池→Google 送中探测）+ VPS 缓存保热部署 | Sub-Store 后端（Node Docker，含 HTTP META） | `operators/*.js` + `configs/keepwarm.sh` |
| [vps-order/lightlayer](vps-order/lightlayer/) | LightLayer 特价 VPS 下单/守候 CLI（试算、下单、并发守候、防超订） | 本地终端，Python 3 纯标准库 | `lightlayer.py` |
| [vps-order/raksmart-api-sniper](vps-order/raksmart-api-sniper/) | Raksmart 特价 VPS 抢购 CLI（OAuth 四跳登录、时钟校准、并行下单），含完整接口逆向文档 | 任意 Node ≥ 18 机器（建议低延迟 VPS），零依赖 | `raksmart-api-sniper.mjs` |
| [tampermonkey/web-ux-freedom](tampermonkey/web-ux-freedom/) | 解除网站前端体验限制（切屏检测、强制全屏、禁复制、反反调试等），按站点可开关 | 浏览器 + Tampermonkey | `web-ux-freedom.user.js` |
| [tampermonkey/raksmart-auto-order](tampermonkey/raksmart-auto-order/) | Raksmart 特价 VPS 定时抢购（v2 API 直连，不操作 DOM） | 浏览器 + Tampermonkey | `raksmart-auto-order.user.js` |
| [wsl-dev-setup](wsl-dev-setup/) | Ubuntu/WSL2 开发环境一键安装（zsh + OMZ + P10k、nvm、pnpm、gh、CLI 工具集） | Ubuntu on WSL2 | `install.sh` |

各项目的安装、配置、原理与排障文档见其目录下的 `README.md`（及 `docs/`）。

## 分组约定

- **homelab/** —— 围绕同一套家庭网络与自建代理基础设施的工具（OpenWrt 路由器、
  Headscale 组网、daed 透明代理、Sub-Store 订阅处理），三者常有配置与文档上的联动。
- **vps-order/** —— 面向 VPS 商家抢购/下单的独立命令行工具（不同商家，同一主题）。
- **tampermonkey/** —— 依赖浏览器插件运行的油猴脚本，目录约定见
  [tampermonkey/README.md](tampermonkey/README.md)；不依赖浏览器的 CLI 不放这里
  （Raksmart 抢购的 Node 版因此在 `vps-order/`）。
- **wsl-dev-setup/** —— 开发机自身环境，独立成组。

新增项目时先判断归属分组；无合适分组再新增顶层目录，命名一律 kebab-case。

## 私密文件约定

真实凭据与部署实例文件**绝不入库**，由根 [.gitignore](.gitignore) 统一排除，
各目录只保留 `*.example.*` 模板：

- `vps-order/lightlayer/.env`（商家账号）
- `vps-order/raksmart-api-sniper/config.json`（下单参数与运行状态）
- `homelab/substore-operators/configs/{keepwarm.conf,node-profile-rules.json}`（订阅地址/节点信息）
- `homelab/headscale-openwrt-bootstrap/backups/`（路由器设备配置快照，含真实节点凭据；
  其历史已从本仓库公开历史中滤除，完整原始历史保存在归档的私有旧仓库中）

## 从旧仓库迁移记录（2026-11）

| 旧仓库（已归档） | 新路径 |
|---|---|
| headscale-openwrt-bootstrap（私有） | homelab/headscale-openwrt-bootstrap |
| openwrt-dns-daed | homelab/openwrt-dns-daed |
| substore-operators | homelab/substore-operators |
| lightlayer_order（私有） | vps-order/lightlayer（同步更名 snake_case → kebab-case） |
| tampermonkey | tampermonkey/ + vps-order/raksmart-api-sniper（Node CLI 移出） |
| wsl-dev-setup | wsl-dev-setup |

> Sub-Store 远程 Operator 的 raw 地址随迁移变化，新地址形如
> `https://raw.githubusercontent.com/blooddrunk/scripts/main/homelab/substore-operators/operators/<脚本>.js`，
> 见 [homelab/substore-operators/README.md](homelab/substore-operators/README.md)。

## 许可证

[MIT](LICENSE)
