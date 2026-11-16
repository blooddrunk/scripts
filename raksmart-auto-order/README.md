# Raksmart VPS 自动下单助手（API 直连版）

一个 Tampermonkey 脚本，用于 Raksmart（billing.raksmart.com）特价 VPS
的定时抢购。**v2.0 起不再操作订购页 DOM，而是直接调用页面背后的
Medusa 后端接口**完成登录检测、报价校验与下单结账——抢购高峰页面
卡死、按钮失灵等问题不再影响脚本（v1.x 的核心痛点）。

接口逆向细节见 [`../raksmart-api-sniper/README.md`](../raksmart-api-sniper/README.md)
（含完整接口文档与独立 Node.js 抢购脚本，追求极限时推荐用那个）。

## 安装

1. 浏览器安装 [Tampermonkey](https://www.tampermonkey.net/) 扩展
2. Tampermonkey → 管理面板 → 「+」新建脚本，粘贴
   `raksmart-auto-order.user.js` 全部内容并保存（Ctrl+S）
3. 在 billing.raksmart.com **登录账号**（脚本复用浏览器会话，
   无需在脚本里存密码；可选填账密用于会话失效时自动重登）
4. 打开任意 `billing.raksmart.com/ec/*` 页面（推荐订购页本身），
   右下角出现状态面板
5. 点击「设置」核对期望价格、付款周期与场次后保存——保存会刷新页面

## 工作方式（v2）

脚本仅把宿主页面当作运行环境（一个活的 JS 上下文 + 登录 Cookie），
全部业务动作走 `https://rak-ec-service.raksmart.com/store` 接口
（CORS 允许 billing 域携带凭证，Cookie 自动附带）：

1. `GET /customers/me` 检测登录态（面板实时显示；未登录可一键打开登录页，
   配置了账密则自动重登）
2. `POST /products/template/whmcs/<pid>` 拉取商品模板，构建完整下单参数
   （含页面隐藏配置项；内网对应 customfield `private_network_enabled`）
3. 场次外低频（30s）/ 预热与抢购中高频（5s）轮询
   `POST /custom/price/get-product-price`，校验活动状态、到手价与付款周期
4. 场次内价格匹配 → 每 `orderIntervalMs`（默认 1s）提交一次
   `POST /custom/cart/add-order`，库存不足自动重试
5. 下单成功（返回 cart id）→ 立即 `POST /custom/whmcs-cart/checkout-cart`
   结账 → 自动打开付款页，面板显示订单号/账单号

防重复下单锁沿用 v1：以「商品三参数 + 场次开始时间」为键，首个下单成功
即落锁，本场不再动作。

## 与 v1.x 的差异

| v1.x（DOM 模式） | v2（API 模式） |
|---|---|
| 等表单渲染、读价格节点、模拟点击「立即购买」 | 纯接口调用，页面无需渲染成功 |
| 页面卡死 → 脚本失效 | 页面卡死不影响（只有标签页冻结会拖慢定时器） |
| 自动勾选内网/服务条款 | 请求体直接携带 `agree_terms:1` 与内网字段 |
| 依赖 `.price-order-btn-buy` 等 class | 仅依赖接口路径与 publishable key |
| 页面异常自动刷新修复 | 不再需要刷新 |

设置项变化：移除了 DOM 相关项（勾选、报价稳定时长、刷新阈值等），
新增登录状态显示、自动重登（可选）、API 下单间隔。
v1 的已保存设置会自动迁移（DOM 专属字段被丢弃）。

## 已知限制（诚实声明）

- **换商品需要改代码**：下单配置项映射（`DEFAULT_CONFIGOPTIONS`）内置于
  脚本头部，针对 pid 1889 秒杀款抓包固定。换商品时在订购页选好配置，
  F12 抓 `get-product-price` 请求的 `configoptions` 填入（隐藏项脚本会
  从模板自动补全）。
- 接口逆向自公开前端，Raksmart 改版（publishable key、路径、参数结构
  变化）后需更新脚本。
- 下单成功 ≠ 抢到：仍需在付款页完成支付；脚本不代付。
- 抢购时请勿最小化/冻结标签页（后台节流会把 500ms 循环拖慢到 ≥1s）；
  也不要与 Node 版脚本同时抢同一商品（同账号会话竞争）。
- 请求超时存在歧义（服务端可能已受理），脚本会提醒人工核对账单列表。

## 使用须知

仅供个人为自己的订单自动化重复性操作。使用本脚本产生的任何账号
风控、订单纠纷或损失由使用者自行承担；请知悉并遵守目标网站的
服务条款。
