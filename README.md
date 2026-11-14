# Tampermonkey 脚本集

个人油猴（Tampermonkey）脚本仓库，**每个脚本一个独立目录**，互不依赖。

## 脚本索引

| 脚本 | 说明 |
|---|---|
| [网页体验自由 (Web UX Freedom)](web-ux-freedom/) | 解除网站前端体验限制：切屏/失焦检测、强制全屏、禁复制/禁右键、快捷键屏蔽、反反调试、诚信粘贴守卫、PrintScreen 拦截、键盘行为记录器等，全部功能可按站点开关 |

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
