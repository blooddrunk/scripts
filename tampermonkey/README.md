# Tampermonkey 脚本集

个人油猴（Tampermonkey）脚本仓库，**每个脚本一个独立目录**，互不依赖。

## 脚本索引

| 脚本 | 说明 |
|---|---|
| [网页体验自由 (Web UX Freedom)](web-ux-freedom/) | 解除网站前端体验限制：切屏/失焦检测、强制全屏、禁复制/禁右键、快捷键屏蔽、反反调试、诚信粘贴守卫、PrintScreen 拦截、键盘行为记录器等，全部功能可按站点开关 |
| [Raksmart VPS 自动下单助手](raksmart-auto-order/) | Raksmart 特价 VPS 定时抢购（**v2 API 直连**）：绕过页面 DOM 直接调 Medusa 后端完成报价校验与下单结账，页面卡顿不影响；按单次/每日场次调度、防重复下单锁、状态面板 |
| [Raksmart API 直连抢购 (Node)](raksmart-api-sniper/) | 上表的独立 Node.js 版本（零依赖）：自动登录（OAuth 四跳）、时钟校准、场次等待、并行下单、结账输出账单链接；含完整接口逆向文档 |

## 目录约定

添加新脚本时，新建一个与脚本同名的目录，按以下布局放置（`demo/` 与
`references/` 按需可选）：

```
<脚本名>/
├── <脚本名>.user.js   # 脚本本体（Tampermonkey 直接安装）
├── README.md          # 该脚本的文档：安装、功能、原理、已知限制
├── demo/              # （可选）复刻目标行为的 demo 页 + Playwright 自动化验证
└── references/        # （可选，已 gitignore）逆向分析材料等本地文件
```

- 验证工具链（Playwright）由根目录 `package.json` 统一提供，
  `npm install` 一次即可供所有脚本的 `demo/verify.mjs` 使用
- `references/` 与 `.playwright-cli/` 在 `.gitignore` 中按目录名匹配
  （任意层级生效），各脚本目录下均会自动忽略

## 验证

```bash
npm install && npx playwright install chromium
node web-ux-freedom/demo/verify.mjs
```
