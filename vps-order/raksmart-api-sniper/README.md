# Raksmart API 直连抢购

绕过 `billing.raksmart.com/ec` 订购页（React/Next.js），直接调用其背后的
Medusa 后端完成「登录 → 报价校验 → 下单 → 结账」全链路的抢购脚本。
页面卡顿、渲染失败、按钮点不动等问题从此无关紧要。

本目录包含：

- `raksmart-api-sniper.mjs` —— Node.js 独立抢购脚本（零依赖，Node ≥ 18）
- `config.example.json` —— 配置模板
- 本 README —— 完整接口逆向文档（两个方案共用）

## 快速开始

### 一键启动自动下单

首次使用时，在脚本目录把 `config.example.json` 复制为 `config.json`，填写商品、预期价格和场次。

如果已在 `config.json` 填写 `email` 和 `password`，从仓库根目录启动。

```bash
cd vps-order/raksmart-api-sniper && node raksmart-api-sniper.mjs
```

也可以在启动时传入账号环境变量。先替换示例邮箱和密码，再从仓库根目录运行。环境变量优先于 `config.json` 中的账号字段。

```bash
cd vps-order/raksmart-api-sniper && \
  RAKSMART_EMAIL='you@example.com' RAKSMART_PASSWORD='your-password' \
  node raksmart-api-sniper.mjs
```

**这会自动加购，并在确认活动价和优惠券后自动结账。** 脚本先登录和预热，进入配置的场次后尝试加购。库存不足时会在场次内重试。结账成功后输出订单、账单和付款信息，付款由你手动完成。本地 `.raksmart-sniper-state.json` 记录场次结果，避免重复下单。

建议提前至少 10 分钟启动，给登录、校时、读取商品模板和首次报价留出时间。

### 默认配置和 `--config`

在脚本目录运行时，`node raksmart-api-sniper.mjs` 和 `node raksmart-api-sniper.mjs --config ./config.json` 等效，两条命令都读取当前工作目录的 `config.json`。要用另一份配置时，传入它的路径，例如 `--config ./another-sale.json`。相对路径以运行命令时所在的目录为准。

### 只测试报价

以下命令在脚本目录运行。两种模式都不会加购或结账。

| 命令 | 行为 |
|---|---|
| `node raksmart-api-sniper.mjs --once` | 启动后立即登录、读取商品模板并检查一次报价，然后退出。无需配置场次，适合核对活动价。使用时启用报价预检（`skipPreflightQuote: false`），并省略 `--skip-quote`。 |
| `node raksmart-api-sniper.mjs --dry-run` | 按配置的场次运行。进入场次后定期检查报价，场次结束后退出。需要有效的场次配置，适合测试定时和轮询。 |

`--force` 可跳过本地的场次去重记录。`--skip-quote` 可让脚本开抢时直接加购，详情见下文。

## 哪些步骤是下单必须的？

| 步骤 | 必须？ | 失败时的行为 |
|---|---|---|
| 登录（4 跳 OAuth） | ✅ 必须 | 网络类错误**指数退避持续重试直到场次结束**；账密错误立即终止 |
| 商品模板 `products/template/whmcs/<pid>` | ✅ 必须（下单 body 的 product_id/group_id/配置项来自它） | **持续重试直到场次结束**，场次结束仍失败才终止 |
| 时钟校准 | ❌ 非必须 | 从登录链路/模板响应的 `Date` 头**顺带采样**（零额外请求）；采不到就按偏移 0 继续，只影响开抢卡点精度 |
| 首次报价 `get-product-price` | ❌ 加购前可跳过 | 校验预期价格和优惠券。预检失败仍可加购 |
| 等待期报价巡检 | ❌ 非必须 | 尽力刷新报价和优惠券。最后 60 秒停止发起巡检 |
| `add-order` 加购 | ✅ 必须 | 库存不足时按 `orderIntervalMs` 重试 |
| `checkout-cart` 自动结账 | ✅ 生成订单和账单 | 需要通过价格和优惠券校验的报价。结账超时或仍在处理时，会轮询确认结果 |

### `--skip-quote` / `skipPreflightQuote: true`

在配置中设 `skipPreflightQuote: true`，或在启动命令末尾加 `--skip-quote`，即可跳过首次报价和等待期巡检。到点后直接加购。加购一直失败时，整场都不会查询报价。

加购成功后，若没有通过校验的预检报价，脚本才会查价。**报价必须等于 `expectedPrice`，并返回有效的 `use_discount_ids`，才会自动结账。** 正常只查一次。遇到超时、429、5xx 等临时故障时，失败后等 3 秒再试，直到取得有效报价或达到 `checkoutQuoteMaxAttempts`。该配置默认是 `3`，含首次补报价。填 `0` 可不限次数地重试临时故障。补报价可以持续到加购场次结束后，按 `Ctrl+C` 可停止。

价格不符、缺少优惠券 ID、活动无效或达到重试上限时，脚本会保留购物车，显示原因和购物车链接。

启用预检时可以复用已通过校验的报价，省掉加购后的查询。后续巡检若返回无效报价，旧报价会作废。当前结账请求没有价格上限字段，因此即使跳过预检，自动结账仍需要至少一次通过校验的报价。付款前请核对最终账单金额。

**结账超时的处理**（实测踩坑）：结账请求客户端超时 ≠ 失败——服务端
可能已受理并最终建单（实测超时请求在 ~35s 后成功生成订单，期间重试
返回 500 `cart xxx is processing`）。脚本对超时/`is processing`/5xx
按 5–8 秒间隔轮询确认，最多 8 次；始终无法确认时保留购物车并提示
人工核对订单列表，绝不自动重跑（防止重复下单）。

## 与 Tampermonkey 版（`../../tampermonkey/raksmart-auto-order/`）的关系

| | 本脚本（Node） | Tampermonkey v2 |
|---|---|---|
| 运行环境 | 任意有 Node 的机器（可放低延迟 VPS） | 浏览器标签页 |
| 登录 | 脚本内自动登录（账密 → OAuth 四跳） | 复用浏览器已有会话 |
| 抗页面卡顿 | 完全无关（根本没有页面） | 无关 DOM，但标签页冻结会拖慢定时器 |
| 并行下单 | 支持 `parallel: 1–5` | 单发在途 |
| 时钟校准 | 自动（服务器 Date 头中位数） | 手动 `clockOffsetMs` |
| 适合场景 | 认真抢、追求极限 | 顺手抢、免配置 |

两边的接口调用逻辑一致，可同时运行但**不建议**（会互相竞争同一账号会话）。

## 接口逆向文档

以下内容逆向自订购页 JS bundle（`/ec/_next/static/chunks/`）并于
2026-11-16 实测验证。后端为 [Medusa](https://medusajs.com/)（电商框架）+
WHMCS（计费）混合架构。

### 基础信息

| 项 | 值 |
|---|---|
| API 基址 | `https://rak-ec-service.raksmart.com/store` |
| 发布键（固定 header） | `x-publishable-api-key: pk_01HKV8SJC13X083AN545TWEPS4` |
| 鉴权 | Cookie `connect.sid`（域 `rak-ec-service.raksmart.com`，HttpOnly，SameSite=None） |
| CORS | 允许 `Origin: https://billing.raksmart.com` + 凭证（页面内 fetch 可直调） |
| 业务身份 | Cookie `WHMCSCustomerID=cus_...`（域 `.raksmart.com`）即 customer_id |

### 1. 登录链路（四跳，无 CSRF token）

```
① POST https://billing.raksmart.com/whmcs/medusa-login.php
   Content-Type: application/x-www-form-urlencoded
   body: username=<邮箱URL编码>&password=<密码URL编码>
   → 302（种 WHMCS 会话 Cookie）
   ↓
② GET https://rak-ec-service.raksmart.com/store/oauth?rt=https%3A%2F%2Fwww.raksmart.com
   （必须带 Origin: https://billing.raksmart.com 头，否则 404）
   → 302 https://billing.raksmart.com/whmcs/oauth/authorize.php?...&state=...（种 connect.sid#1）
   ↓
③ GET authorize.php?response_type=code&redirect_uri=...&client_id=RAKSMART.164d6cf1a3b751d63eaf2187930c5279
   （携带 ① 的 WHMCS Cookie）
   → 302 .../store/oauth/callback?code=...&state=...
   ↓
④ GET callback（携带 ② 的 connect.sid）
   → 302 www.raksmart.com；种认证 connect.sid + WHMCSCustomerID=cus_...
```

之后所有 `/store/*` 请求带最终 `connect.sid` 即为登录态。
校验：`GET /store/customers/me` → 200 返回 `customer`；未登录 401 `{"code":401,"message":"Invalid token"}`。

### 2. 报价（**无需登录**，可匿名轮询）

```
POST /store/custom/price/get-product-price
{
  "product_id": "prod_01JMDSD0NVTZYWAW3X2TMZDYS7",   ← Medusa 商品 id（来自模板接口）
  "billingcycle": "annually",
  "activity_id": "act_...",
  "price_list_id": "",
  "quantity": 1,
  "language": "chinese-cn",
  "configoptions": { "<config_id>": <option_id> | "<数量>" , ... },
  "sample_id": "psam_..."
}
```

关键字段（响应）：

- `priceoverride` / `discounted_price` —— 活动到手价（如 `"49.90"`）
- `activity_validate.status` —— `false` 时 `msg` 为「活动不存在或活动状态未进行中」
- `cycle` —— 周期显示文本（`"每年"`）
- `use_discount_ids` —— **登录后**返回，需在结账时回传（活动优惠券自动下发）
- `is_guest` / `customer_id`

### 3. 商品模板（匿名可访问，响应约 500KB）

```
POST /store/products/template/whmcs/<pid>     body: { sample_id, activity_id }
```

提供：Medusa `product.id`、WHMCS `gid`（group_id）、全部 configoption
（含隐藏项）与 customfield 定义、价格计算模板（`need_host_name` 等）。

**注意**：下单接口要求 configoptions 覆盖**全部**配置项（含页面隐藏的
Zone/Region/IP Type 等），缺一个即 400 `configoption_value_invalid`。
报价请求只需可见项，两者不一致——脚本已处理。

### 4. 加购

```
POST /store/custom/cart/add-order             （需登录，401 表示会话失效）
{
  "domain": "vps-xxxxxx",                     // need_host_name 商品必填
  "rootpw": "随机生成",                        // need_console_pwd 商品必填
  "pid": "1889",
  "product_id": "prod_...",
  "billingcycle": "annually",
  "quantity": 1,
  "activity_id": "act_...",
  "price_list_id": "",
  "configoptions_data": { ...全部配置项... },
  "customfields_data": { "<customfield_id>": "on" },   // 内网 = private_network_enabled
  "sample_id": "psam_...",
  "aff_id": 0, "aff_rtype": "",
  "language": "chinese-cn",
  "group_id": 168,
  "notes": "",
  "agree_terms": 1,
  "customer_id": "cus_..."
}
```

响应：

- `200` → `body.cart.id`（加购成功，拿到购物车 ID）
- `400` `{"code":"activity_error","message":"Activity error msg:库存不足"}` → 没抢到，重试
- `400` `configoption_value_invalid` → 参数缺项，检查 configoptions_data
- `401` → 会话失效，重新走登录链路

### 5. 结账（生成正式订单与账单）

```
POST /store/custom/whmcs-cart/checkout-cart   （需登录）
{
  "cart_id": "<add-order 返回>",
  "customer_id": "cus_...",
  "discount_ids": [<报价响应的 use_discount_ids>],
  "consume_point": 0,
  "notes": "",
  "agree_terms": true,
  "consume_voucher_ids": [],
  "contact_id": 0
}
```

- `200` → `body.invoice_url`（付款页）或 `body.whmcs_order.data.orderid/invoiceid`
- `202` → 已下单，去账单列表付款：`https://billing.raksmart.com/whmcs/clientarea.php`

### 实测时延参考（2026-11-16，本机 → SJC 边缘）

| 接口 | 往返 |
|---|---|
| get-product-price | 0.8–8 s（波动大） |
| add-order | **约 4 s**（服务端算价慢，抢购节奏实际受此限制） |
| 高峰期 | 500/502 常见，脚本按可重试错误处理 |

## 配置说明（config.json）

见 `config.example.json` 内注释。要点：

- `parallel`（1–5）：并行下单请求数。多个 add-order 成功只会**结账第一个**，
  其余停留在购物车（不结账就不产生订单），属安全并发；但可能多占库存配额，慎用大值。
- `clockOffsetMs`：默认自动校准（服务器 Date 头中位数）；手工覆盖可填数字。
- 场次语法与 Tampermonkey 版一致（once：ISO 8601；daily：`HH:mm` 时段数组 + UTC 偏移）。

## 使用须知

- 仅供个人为自己的订单自动化重复性操作；使用产生的任何账号风控、
  订单纠纷或损失由使用者自行承担，请知悉并遵守目标网站服务条款。
- 接口逆向自公开前端，Raksmart 改版后可能失效（尤其 publishable key、
  登录入口、configoption 结构）。
- 抢购存在网络歧义：请求超时但服务端可能已受理。脚本会统计歧义请求并在
  结束时提醒人工核对账单列表；成功路径则立即停止重试防止重复下单。
