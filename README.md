# LightLayer 一键下单脚本

基于 [LightLayer User API](https://account.lightlayer.net/index.php?/userapi) 的命令行下单工具。
仅依赖 Python 3 标准库，无需安装任何第三方包。

## 快速开始

```bash
cd /home/jelinenaro/code/scripts/lightlayer
cp .env.example .env     # 然后编辑 .env 填入账号密码和产品ID
python3 lightlayer.py quote    # 第一步: 试算价格(不会下单)
python3 lightlayer.py order    # 第二步: 正式下单(需输入 y 确认)
python3 lightlayer.py order -y # 跳过确认, 真正"一键"
python3 lightlayer.py          # 不带子命令 = order
```

## 命令一览

| 命令 | 作用 |
|---|---|
| `products` | 列出产品分类、产品、各周期价格 —— 用于查询 `product_id` |
| `forms` | 查看产品下单表单：字段 id、选项 item id、默认值 —— 用于写 `LIGHTLAYER_CUSTOM` |
| `payments` | 列出支付方式及数字 ID —— 用于查询 Alipay 的 ID |
| `quote` | 按 `.env` 配置试算价格，不产生订单 |
| `order` | 正式下单（默认先试算再确认；`-y` 跳过确认） |
| `invoices` / `invoice --id X` | 查看账单列表 / 单个账单详情 |
| `login` | 验证账号密码，显示客户信息和余额 |
| `logout` | 吊销 token 并删除本地缓存 |

`order` / `quote` 支持的临时参数（优先级高于 `.env`）：
`--product-id`、`--cycle`、`--pay-method`、`--promocode`、`--domain`、`--custom`、`-y`、`--no-quote`。

计费周期代码：`m` 月付、`q` 季付、`s` 半年付、`a` 年付、`b` 两年付、`t` 三年付。
具体产品支持哪些周期以 `products` 命令显示的价格为准。

## 配置说明

配置从 `.env` 文件读取（也可用系统环境变量，环境变量优先）。
查找顺序：`--config` 指定的路径 → 当前目录 `.env` → 脚本目录 `.env`。

所有字段见 `.env.example`。要点：

- `LIGHTLAYER_USERNAME` / `LIGHTLAYER_PASSWORD`：必填（密码也可留空，运行时交互输入）。
- `LIGHTLAYER_PRODUCT_ID`：下单必填。
- `LIGHTLAYER_PAY_METHOD`：默认 `Alipay`。两种配法：
  - **数字 ID**（如 `53`）：下单流程完全跳过 `/payment` 接口调用（少一次请求）；
    仅当下单/试算报错且疑似支付方式问题时，脚本才自动拉取一次支付方式列表辅助排查。
  - **名称**（如 `Alipay`，大小写不敏感）：每次下单前调用 `/payment` 把名称解析成 ID。
- `LIGHTLAYER_CUSTOM`：极少数产品下单需要提交自定义字段时，以 JSON 数组形式配置。

## 脚本做了什么（流程）

1. **认证**：优先使用本地缓存的 token（`~/.cache/lightlayer/token.json`，权限 0600）；
   token 过期时自动用 refresh token 换新（`POST /token`）；refresh 也失效时才用账号密码
   重新登录（`POST /login`）。整个刷新/重登过程对使用者透明。
2. **解析支付方式**：`LIGHTLAYER_PAY_METHOD` 为数字 ID 时直接使用（不调接口）；
   为名称时通过 `GET /payment` 解析成 ID。
3. **解析支付方式**：`LIGHTLAYER_PAY_METHOD` 是名称时，通过 `GET /payment` 找到对应数字 ID。
4. **自动填写产品配置**：`GET /order/{product_id}` 读取下单表单，按各字段默认值构造
   `custom`（格式 `{字段id: {item_id: 值}}`），必填复选框（如 Refunds Policy）自动勾选；
   `LIGHTLAYER_CUSTOM` 可按字段覆盖（例如更换操作系统）。
5. **试算**（可 `--no-quote` 跳过）：`POST /quote`，展示预计金额及字段校验错误。
4. **确认后下单**：`POST /order/{product_id}`，成功后打印订单号、账单号、金额。
5. **支付**：API 不返回支付跳转链接，下单成功后需登录用户中心支付账单：
   `https://account.lightlayer.net/index.php?/clientarea/invoice/<账单ID>`
   （脚本会直接打印该链接； unpaid 状态的订单在账单支付前不会开通）。
6. **确认后下单**：`POST /order/{product_id}`，成功后打印订单号、账单号、金额。
7. **支付**：API 不返回支付跳转链接，下单成功后需登录用户中心支付账单：
   `https://account.lightlayer.net/index.php?/clientarea/invoice/<账单ID>`
   （脚本会直接打印该链接； unpaid 状态的订单在账单支付前不会开通）。
8. 下单记录追加保存在 `~/.cache/lightlayer/orders.log`。

## 安全提示

- `.env` 中是明文密码，请勿提交到 git 或分享给他人（目录下可加 `.gitignore` 忽略 `.env`）。
- token 缓存仅本机可读（0600）；在公用机器上用完可执行 `python3 lightlayer.py logout` 吊销并清除。

## 常见问题

- **`product_id` 从哪来？** 运行 `python3 lightlayer.py products`，输出中 `#数字` 即产品 ID。
- **Alipay 的 ID 是多少？** 运行 `python3 lightlayer.py payments` 查看；脚本默认按名称
  `Alipay` 自动解析，无需手动填 ID。
- **怎么换操作系统/CPU/内存等配置？** 先 `python3 lightlayer.py forms` 查看字段和选项
  item id，然后在 `.env` 配置 `LIGHTLAYER_CUSTOM={"字段id": {"item_id": 值}}` 覆盖默认值。
- **优惠码报 "coupon_not_applicable"？** 说明该码不适用于当前产品/周期（服务端判定），
  订单仍可正常提交，只是没有折扣。
- **下单成功但服务没开通？** 正常，需先在用户中心支付账单（见上文第 7 步）。
