#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""LightLayer 一键下单脚本（仅依赖 Python 3 标准库，无需 pip install）。

基于 https://account.lightlayer.net/index.php?/userapi 文档实现。

常用命令:
  python3 lightlayer.py products               # 列出产品分类与价格(用于查 product_id)
  python3 lightlayer.py forms                  # 查看产品下单表单(字段/item id/默认配置)
  python3 lightlayer.py payments               # 列出支付方式(用于查 Alipay 的数字 id)
  python3 lightlayer.py quote                  # 试算价格(不下单)
  python3 lightlayer.py order                  # 下单(先试算, 再人工确认)
  python3 lightlayer.py order -y               # 跳过确认, 真正"一键下单"
  python3 lightlayer.py                        # 无子命令时等同 order
  python3 lightlayer.py invoices               # 查看账单列表
  python3 lightlayer.py login                  # 验证账号密码 / token
  python3 lightlayer.py logout                 # 吊销 token 并清除本地缓存

配置: 同目录 .env (可参考 .env.example), 真实环境变量优先于 .env 文件。
认证: LIGHTLAYER_AUTH 或 --auth 参数, 可选 basic(默认)/auto/bearer, 详见 README。
"""

from __future__ import annotations

import argparse
import base64
import getpass
import json
import os
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

DEFAULT_API_BASE = "https://account.lightlayer.net/api"
CLIENT_AREA = "https://account.lightlayer.net/index.php?/clientarea"
DEFAULT_PAY_METHOD = "Alipay"
TIMEOUT = 30

SCRIPT_DIR = Path(__file__).resolve().parent
CACHE_DIR = Path(os.environ.get("XDG_CACHE_HOME", str(Path.home() / ".cache"))) / "lightlayer"
TOKEN_FILE = CACHE_DIR / "token.json"
ORDERS_LOG = CACHE_DIR / "orders.log"

CYCLE_HELP = "m=月付, q=季付, s=半年付, a=年付, b=两年付, t=三年付"

AUTH_MODES = ("basic", "bearer", "auto")


class ApiError(RuntimeError):
    """API 返回非 2xx 或业务错误。"""

    def __init__(self, status: int, payload, path: str):
        self.status = status
        self.payload = payload
        detail = payload if isinstance(payload, str) else json.dumps(payload, ensure_ascii=False)
        if len(detail) > 600:
            detail = detail[:600] + "...(截断)"
        super().__init__(f"API 请求失败 [{status}] {path}: {detail}")


# ---------------------------------------------------------------- 配置

CONFIG_KEYS = (
    "LIGHTLAYER_USERNAME", "LIGHTLAYER_PASSWORD", "LIGHTLAYER_PRODUCT_ID",
    "LIGHTLAYER_CYCLE", "LIGHTLAYER_PAY_METHOD", "LIGHTLAYER_PROMOCODE",
    "LIGHTLAYER_DOMAIN", "LIGHTLAYER_AFF_ID", "LIGHTLAYER_CUSTOM",
    "LIGHTLAYER_API_BASE", "LIGHTLAYER_AUTH",
)


def load_env_file(path: Path) -> dict:
    values = {}
    if not path.is_file():
        return values
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, val = line.partition("=")
        key, val = key.strip(), val.strip()
        if len(val) >= 2 and val[0] == val[-1] and val[0] in "\"'":
            val = val[1:-1]  # 引号内的值原样保留(可含 # 等字符)
        else:
            val = val.split(" #", 1)[0].rstrip()  # 未引号的值支持行内注释
        if key:
            values[key] = val
    return values


def load_config(explicit: str | None) -> dict:
    candidates = [Path(explicit)] if explicit else [
        Path.cwd() / ".env",
        SCRIPT_DIR / ".env",
        Path.cwd() / "lightlayer.env",
        SCRIPT_DIR / "lightlayer.env",
    ]
    cfg = {}
    for c in candidates:
        if c.is_file():
            cfg = load_env_file(c)
            cfg["__config_file"] = str(c)
            break
    # 真实环境变量优先于配置文件(且无需配置文件存在)
    for k in CONFIG_KEYS:
        if k in os.environ:
            cfg[k] = os.environ[k]
    return cfg


# ---------------------------------------------------------------- API 客户端

class LightLayer:
    def __init__(self, base: str, username: str | None, password: str | None,
                 auth_mode: str = "basic"):
        mode = (auth_mode or "basic").strip().lower()
        if mode not in AUTH_MODES:
            raise SystemExit(f"[!] 无效认证模式 {auth_mode!r}, 可选: {'/'.join(AUTH_MODES)}")
        self.base = base.rstrip("/")
        self.username = username
        self.password = password
        self.auth_mode = mode
        # basic 直接生效; auto 在 /login 被 WAF 拦截时置 True; bearer 永不降级
        self.use_basic = mode == "basic"
        self.token: str | None = None
        self.refresh_token: str | None = None

    # 底层请求, 返回 (status, payload)
    def _request(self, method: str, path: str, body=None, token: str | None = None,
                 basic: bool = False):
        url = self.base + path
        data = None
        headers = {"Accept": "application/json"}
        if body is not None:
            data = json.dumps(body).encode("utf-8")
            headers["Content-Type"] = "application/json"
        if token:
            headers["Authorization"] = "Bearer " + token
        elif basic and self.username and self.password:
            cred = base64.b64encode(f"{self.username}:{self.password}".encode()).decode()
            headers["Authorization"] = "Basic " + cred
        req = urllib.request.Request(url, data=data, method=method, headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
                status, raw = resp.status, resp.read().decode("utf-8", "replace")
        except urllib.error.HTTPError as e:
            status, raw = e.code, e.read().decode("utf-8", "replace")
        except urllib.error.URLError as e:
            raise SystemExit(f"[!] 网络错误, 无法访问 {url}: {e.reason}")
        try:
            payload = json.loads(raw) if raw.strip() else None
        except json.JSONDecodeError:
            payload = raw
        return status, payload

    @staticmethod
    def _raise_if_error(status: int, payload, path: str):
        if status >= 400 or (isinstance(payload, dict) and payload.get("error")):
            raise ApiError(status, payload, path)

    # ---- token 缓存 ----
    def _save_cache(self):
        CACHE_DIR.mkdir(parents=True, exist_ok=True)
        data = {
            "username": self.username,
            "token": self.token,
            "refresh": self.refresh_token,
            "updated": int(time.time()),
        }
        TOKEN_FILE.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        TOKEN_FILE.chmod(0o600)

    def _load_cache(self):
        try:
            data = json.loads(TOKEN_FILE.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return
        if isinstance(data, dict) and data.get("username") == self.username:
            self.token = data.get("token")
            self.refresh_token = data.get("refresh")

    def _clear_cache(self):
        TOKEN_FILE.unlink(missing_ok=True)

    # ---- 认证 ----
    def login(self):
        if not self.username or not self.password:
            raise SystemExit("[!] 缺少账号信息: 请在 .env 或环境变量中设置 LIGHTLAYER_USERNAME / LIGHTLAYER_PASSWORD")
        status, payload = self._request("POST", "/login",
                                        {"username": self.username, "password": self.password})
        self._raise_if_error(status, payload, "/login")
        if not isinstance(payload, dict) or not payload.get("token"):
            raise ApiError(status, payload, "/login")
        self.token = payload["token"]
        self.refresh_token = payload.get("refresh")
        self._save_cache()
        return payload

    def refresh(self) -> bool:
        if not self.refresh_token:
            return False
        status, payload = self._request("POST", "/token", {"refresh_token": self.refresh_token})
        if status >= 400 or not isinstance(payload, dict) or not payload.get("token"):
            self.token = self.refresh_token = None
            self._clear_cache()
            return False
        self.token = payload["token"]
        self.refresh_token = payload.get("refresh") or self.refresh_token
        self._save_cache()
        return True

    def _login_with_fallback(self):
        """登录; 仅 auto 模式在 POST /login 被站点 WAF 按路径拦截
        (403 返回 HTML)时, 退回官方文档同样支持的 HTTP Basic 认证。"""
        try:
            self.login()
        except ApiError as e:
            if self.auth_mode != "auto" or e.status != 403 or isinstance(e.payload, dict):
                raise  # bearer 模式或业务错误(如密码错误)不降级
            self.use_basic = True
            self.token = self.refresh_token = None
            self._clear_cache()
            print("[i] POST /login 被站点 WAF 拦截(与网页端验证码同源), "
                  "已自动切换为 HTTP Basic 认证(官方文档支持的认证方式)")

    def ensure_token(self):
        if self.use_basic or self.token:
            return
        self._load_cache()
        if self.token:
            return
        if self.refresh_token and self.refresh():
            return
        self._login_with_fallback()

    def logout(self):
        if self.use_basic and not self.token:  # Basic 模式无 token 可吊销
            self._clear_cache()
            return
        self.ensure_token()
        try:
            self._request("POST", "/logout", token=self.token)
        except SystemExit:
            pass
        if self.refresh_token:
            try:
                self._request("POST", "/revoke", {"refresh_token": self.refresh_token})
            except SystemExit:
                pass
        self.token = self.refresh_token = None
        self._clear_cache()

    # 带认证的请求: 401 时自动刷新/重登, 最多各重试一次
    def call(self, method: str, path: str, body=None):
        self.ensure_token()
        status, payload = self._request(method, path, body, self.token, basic=self.use_basic)
        if status in (401, 403) and not self.use_basic:
            if not self.refresh():
                self._login_with_fallback()
            status, payload = self._request(method, path, body, self.token, basic=self.use_basic)
        if status in (401, 403) and not self.use_basic:
            self._login_with_fallback()
            status, payload = self._request(method, path, body, self.token, basic=self.use_basic)
        self._raise_if_error(status, payload, path)
        return payload


def make_client(cfg: dict) -> LightLayer:
    username = cfg.get("LIGHTLAYER_USERNAME") or None
    password = cfg.get("LIGHTLAYER_PASSWORD") or None
    if username and not password and sys.stdin.isatty():
        password = getpass.getpass(f"请输入 {username} 的密码: ")
    return LightLayer(cfg.get("LIGHTLAYER_API_BASE") or DEFAULT_API_BASE,
                      username, password, cfg.get("LIGHTLAYER_AUTH") or "basic")


# ---------------------------------------------------------------- 业务逻辑

def resolve_pay_method(api: LightLayer, value) -> int:
    """value 为数字字符串时直接用 id, 不调用 /payment 接口;
    为名称(默认 Alipay)时通过 /payment 解析成 id。"""
    if value is None:
        value = DEFAULT_PAY_METHOD
    value = str(value).strip()
    if value.isdigit():
        print(f"[i] 支付方式: id={value} (数字ID, 跳过 /payment 接口校验)")
        return int(value)
    payments = api.call("GET", "/payment") or {}
    table = payments.get("payments", {}) if isinstance(payments, dict) else {}
    for pid, name in table.items():
        if str(name).strip().lower() == value.lower():
            print(f"[i] 支付方式: {name} (id={pid})")
            return int(pid)
    raise SystemExit(f"[!] 未找到名为 {value!r} 的支付方式。可用列表: {table}\n"
                     "    可运行 `python3 lightlayer.py payments` 查看全部支付方式, "
                     "或直接在配置中填数字 id。")


PAYMENT_ERROR_KEYWORDS = ("pay_method", "payment", "gateway", "pay method")


def maybe_show_payments_on_error(api: LightLayer, err_payload) -> bool:
    """下单/试算报错且疑似支付方式问题时, 自动拉取一次支付方式列表辅助排查。"""
    if err_payload is None:
        return False
    text = json.dumps(err_payload, ensure_ascii=False).lower()
    if not any(k in text for k in PAYMENT_ERROR_KEYWORDS):
        return False
    try:
        table = (api.call("GET", "/payment") or {}).get("payments", {})
    except (ApiError, SystemExit):
        return False
    if table:
        print("[!] 该错误疑似与支付方式有关, 已自动获取当前可用支付方式 (id -> 名称):")
        for pid, name in table.items():
            print(f"      {pid:>4}  {name}")
    return True


def do_quote(api: LightLayer, product_id: int, cycle: str, pay_method_id: int,
             promocode: str | None, domain: str | None, custom=None):
    item = {"type": "product", "product_id": product_id, "cycle": cycle}
    if domain:
        item["domain"] = domain
    if promocode:
        item["promocode"] = promocode
    if custom:
        item["custom"] = custom
    return api.call("POST", "/quote",
                    {"pay_method": pay_method_id, "output": "short", "items": [item]})


def print_quote(q):
    print("[i] 试算(quote)结果 — 仅供参考:")
    if not isinstance(q, dict):
        print("   ", q)
        return
    summary = q.get("summary") if isinstance(q.get("summary"), dict) else q
    shown = False
    for k in ("subtotal", "discount", "credit", "tax", "total", "recurring"):
        if k in summary:
            print(f"    {k:<10}: {json.dumps(summary[k], ensure_ascii=False)}")
            shown = True
    if not shown:
        print("   ", json.dumps(q, ensure_ascii=False))
    for it in (q.get("items") or []):
        if isinstance(it, dict) and it.get("error"):
            for e in it["error"]:
                print(f"    [!] {json.dumps(e, ensure_ascii=False)}")


def confirm(prompt: str, assume_yes: bool) -> bool:
    if assume_yes:
        return True
    if not sys.stdin.isatty():
        raise SystemExit("[!] 非交互环境下下单必须加 -y 参数")
    try:
        return input(prompt).strip().lower() in ("y", "yes")
    except (EOFError, KeyboardInterrupt):
        return False


def get_order_params(args, cfg: dict) -> dict:
    product_id = (getattr(args, "product_id", None) or cfg.get("LIGHTLAYER_PRODUCT_ID") or "").strip()
    if not product_id.isdigit():
        raise SystemExit("[!] 必须提供产品 product_id: 在 .env 设置 LIGHTLAYER_PRODUCT_ID,"
                         " 或加参数 --product-id\n    (运行 `python3 lightlayer.py products` 可查询所有产品)")
    cycle = (getattr(args, "cycle", None) or cfg.get("LIGHTLAYER_CYCLE") or "m").strip()
    custom_raw = getattr(args, "custom", None) or cfg.get("LIGHTLAYER_CUSTOM") or None
    try:
        custom = json.loads(custom_raw) if custom_raw else None
    except ValueError:
        raise SystemExit('[!] custom 必须是合法的 JSON 对象, 如 \'{"3615": {"32925": 1}}\'')
    if custom is not None and not isinstance(custom, dict):
        raise SystemExit("[!] custom 必须是 JSON 对象, 格式 {字段id: {item_id: 值}}")
    aff_id = cfg.get("LIGHTLAYER_AFF_ID") or None
    if aff_id and not str(aff_id).strip().isdigit():
        raise SystemExit("[!] LIGHTLAYER_AFF_ID 必须是数字")
    return {
        "product_id": int(product_id),
        "cycle": cycle,
        "promocode": getattr(args, "promocode", None) or cfg.get("LIGHTLAYER_PROMOCODE") or None,
        "domain": getattr(args, "domain", None) or cfg.get("LIGHTLAYER_DOMAIN") or None,
        "aff_id": aff_id,
        "custom": custom,
    }


def get_order_form(api: LightLayer, product_id: int) -> dict:
    data = api.call("GET", f"/order/{product_id}")
    return (data.get("product") or {}) if isinstance(data, dict) else {}


def build_custom(api: LightLayer, product_id: int, overrides) -> dict:
    """从 GET /order/{pid} 表单自动构造 custom 参数: 取各字段默认值,
    必填 checkbox(如退款政策)自动勾选, 再用 overrides(字段级)覆盖。"""
    forms = ((get_order_form(api, product_id).get("config") or {}).get("forms")) or []
    custom = {}
    for f in forms:
        if not isinstance(f, dict) or f.get("id") is None:
            continue
        fid = str(f["id"])
        val = f.get("value")
        if isinstance(val, dict) and val:
            custom[fid] = val
        elif f.get("required") and f.get("type") == "checkbox":
            items = f.get("items") or []
            if items and items[0].get("id") is not None:
                custom[fid] = {str(items[0]["id"]): 1}
    for k, v in (overrides or {}).items():
        custom[str(k)] = v  # 整字段替换, 避免下拉框出现多选叠加
    return custom


def log_order(payload, ctx: dict):
    try:
        CACHE_DIR.mkdir(parents=True, exist_ok=True)
        record = dict(ctx)
        if isinstance(payload, dict):
            record.update(payload)
        with ORDERS_LOG.open("a", encoding="utf-8") as f:
            f.write(json.dumps({"time": time.strftime("%Y-%m-%d %H:%M:%S"), **record},
                               ensure_ascii=False) + "\n")
    except OSError:
        pass


# ---------------------------------------------------------------- 子命令

def cmd_order(api: LightLayer, args, cfg: dict):
    p = get_order_params(args, cfg)
    pay_value = getattr(args, "pay_method", None) or cfg.get("LIGHTLAYER_PAY_METHOD") or DEFAULT_PAY_METHOD
    pay_method_id = resolve_pay_method(api, pay_value)
    custom = build_custom(api, p["product_id"], p["custom"])
    if custom:
        print(f"[i] 产品配置(custom)已自动填写 {len(custom)} 项, "
              f"可用 `forms` 命令查看各字段含义")

    if not getattr(args, "no_quote", False):
        try:
            print_quote(do_quote(api, p["product_id"], p["cycle"], pay_method_id,
                                 p["promocode"], p["domain"], custom))
        except ApiError as e:
            print(f"[!] 试算失败(不影响下单): {e}")
            maybe_show_payments_on_error(api, e.payload)

    print("\n---------- 下单信息 ----------")
    print(f"  产品 ID : {p['product_id']}")
    print(f"  计费周期: {p['cycle']}")
    print(f"  支付方式: {pay_value} (id={pay_method_id})")
    if p["promocode"]:
        print(f"  优惠码  : {p['promocode']}")
    if p["domain"]:
        print(f"  域名    : {p['domain']}")
    if custom:
        print(f"  产品配置: {json.dumps(custom, ensure_ascii=False)}")
    if not confirm("确认下单? [y/N]: ", getattr(args, "yes", False)):
        print("[i] 已取消")
        return

    # 使用批量端点 POST /order(与 /quote 的 items 结构同构, custom 嵌套字典格式已验证)
    item = {"type": "product", "product_id": p["product_id"], "cycle": p["cycle"]}
    if p["domain"]:
        item["domain"] = p["domain"]
    if p["promocode"]:
        item["promocode"] = p["promocode"]
    if custom:
        item["custom"] = custom
    body = {"pay_method": pay_method_id, "items": [item]}
    if p["aff_id"]:
        body["aff_id"] = int(p["aff_id"])

    try:
        payload = api.call("POST", "/order", body)
    except ApiError as e:
        maybe_show_payments_on_error(api, e.payload)
        raise
    log_order(payload, {"product_id": p["product_id"], "cycle": p["cycle"],
                        "pay_method_id": pay_method_id})

    print("\n[✓] 下单成功!")
    if isinstance(payload, dict):
        order_nums = payload.get("order_num_list")
        if not order_nums and payload.get("order_num"):
            order_nums = [payload["order_num"]]
        print(f"    订单号: {', '.join(map(str, order_nums)) if order_nums else payload.get('order_num')}")
        print(f"    账单号: {payload.get('invoice_id')}")
        print(f"    金额  : {payload.get('total')}")
        inv = payload.get("invoice_id")
        if inv:
            print(f"    支付  : 请打开 {CLIENT_AREA}/invoice/{inv} 完成支付")
    else:
        print("   ", payload)


def cmd_quote(api: LightLayer, args, cfg: dict):
    p = get_order_params(args, cfg)
    pay_value = getattr(args, "pay_method", None) or cfg.get("LIGHTLAYER_PAY_METHOD") or DEFAULT_PAY_METHOD
    pay_method_id = resolve_pay_method(api, pay_value)
    custom = build_custom(api, p["product_id"], p["custom"])
    try:
        print_quote(do_quote(api, p["product_id"], p["cycle"], pay_method_id,
                             p["promocode"], p["domain"], custom))
    except ApiError as e:
        maybe_show_payments_on_error(api, e.payload)
        raise


def cmd_forms(api: LightLayer, args, cfg: dict):
    pid = str(getattr(args, "product_id", None) or cfg.get("LIGHTLAYER_PRODUCT_ID") or "").strip()
    if not pid.isdigit():
        raise SystemExit("[!] 用法: python3 lightlayer.py forms --product-id 316")
    prod = get_order_form(api, int(pid))
    if not prod:
        raise SystemExit(f"[!] 产品 {pid} 无下单表单信息")
    print(f"产品 #{pid} {prod.get('name', '')} 下单表单:")
    forms = (prod.get("config") or {}).get("forms") or []
    for f in forms:
        req = "必填" if f.get("required") else "可选"
        print(f"\n  [{f.get('id')}] {f.get('title')}  ({f.get('type')}, {req})"
              f"  默认: {json.dumps(f.get('value'), ensure_ascii=False)}")
        for it in (f.get("items") or []):
            sel = "  <-- 当前默认" if it.get("selected") else ""
            print(f"      item {it.get('id')}: {it.get('title')!r} price={it.get('price')}{sel}")
    print('\n  覆盖方法: .env 中 LIGHTLAYER_CUSTOM={"字段id": {"item_id": 值}}, '
          '如换系统: LIGHTLAYER_CUSTOM={"3615": {"32925": 1}}')


def cmd_products(api: LightLayer, args, cfg):
    cats = api.call("GET", "/category") or []
    if isinstance(cats, dict):
        cats = cats.get("categories") or cats.get("list") or []
    if not cats:
        print("[i] 未获取到任何产品分类")
    for cat in cats:
        cid = cat.get("id") if isinstance(cat, dict) else cat
        name = cat.get("name", "") if isinstance(cat, dict) else ""
        print(f"\n== 分类 #{cid} {name} ==")
        products = api.call("GET", f"/category/{cid}/product") or []
        if isinstance(products, dict):
            products = products.get("products") or products.get("list") or []
        for prod in products:
            print(format_product(prod))


def format_product(p) -> str:
    if not isinstance(p, dict):
        return "  " + json.dumps(p, ensure_ascii=False)
    pid = p.get("id")
    name = p.get("name") or p.get("title") or "(未命名)"
    prices = p.get("pricing") or p.get("price") or p.get("periods") or p.get("cycles") or {}
    line = f"  #{pid}  {name}"
    if isinstance(prices, dict) and prices:
        line += "  [" + ", ".join(f"{k}={v}" for k, v in prices.items()) + "]"
    known = {"id", "name", "title", "pricing", "price", "periods", "cycles",
             "description", "type", "category", "hidden", "tagline", "stockcontrol"}
    extra = {k: v for k, v in p.items() if k not in known}
    if extra:
        line += "  " + json.dumps(extra, ensure_ascii=False)
    return line


def cmd_payments(api: LightLayer, args, cfg):
    payload = api.call("GET", "/payment") or {}
    table = payload.get("payments", {}) if isinstance(payload, dict) else {}
    print("可用支付方式 (id -> 名称):")
    for pid, name in sorted(table.items(), key=lambda x: int(x[0]) if str(x[0]).isdigit() else 0):
        mark = "   <-- 默认使用" if str(name).strip().lower() == "alipay" else ""
        print(f"  {pid:>4}  {name}{mark}")


def cmd_invoices(api: LightLayer, args, cfg):
    data = api.call("GET", "/invoice")
    invoices = data.get("invoices") if isinstance(data, dict) else data
    if not invoices:
        print("[i] 没有账单")
        return
    print(f"{'ID':<10}{'到期日':<12}{'金额':<12}{'状态':<10}")
    for inv in invoices:
        if not isinstance(inv, dict):
            continue
        due = str(inv.get("duedate") or inv.get("paybefore") or "")[:10]
        print(f"{str(inv.get('id')):<10}{due:<12}{str(inv.get('total')):<12}{str(inv.get('status')):<10}")
    print(f"\n支付页面: {CLIENT_AREA}/invoice/<账单ID>")


def cmd_invoice(api: LightLayer, args, cfg):
    payload = api.call("GET", f"/invoice/{args.id}")
    print(json.dumps(payload, ensure_ascii=False, indent=2))


def cmd_login(api: LightLayer, args, cfg):
    api.ensure_token()
    auth = "HTTP Basic" if api.use_basic else "Bearer token"
    d = {}
    try:
        d = api.call("GET", "/details") or {}
        if isinstance(d, dict) and isinstance(d.get("client"), dict):
            d = d["client"]  # 真实响应外层包了 client 键
    except ApiError:
        print("[!] GET /details 被站点 WAF 拦截, 以 /balance 验证登录")
    email = d.get("email") or cfg.get("LIGHTLAYER_USERNAME")
    cid = d.get("id", "?")
    print(f"[✓] 登录成功({auth}): {email} (客户ID: {cid})")
    try:
        bal = api.call("GET", "/balance") or {}
        if isinstance(bal, dict) and isinstance(bal.get("details"), dict):
            bal = bal["details"]  # 真实响应外层包了 details 键
        print(f"    余额: {bal.get('acc_balance')} {bal.get('currency', '')}"
              f" / 信用额: {bal.get('acc_credit')}")
    except ApiError:
        pass


def cmd_logout(api: LightLayer, args, cfg):
    api.logout()
    if api.use_basic and not api.token:
        print("[✓] Basic 认证模式无需吊销 token, 已清除本地缓存")
    else:
        print("[✓] 已登出并吊销 token, 本地缓存已清除")


# ---------------------------------------------------------------- 入口

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="lightlayer.py",
        description="LightLayer 一键下单脚本 (API: account.lightlayer.net)",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="无子命令时默认执行 order。首次使用请先: cp .env.example .env 并填写账号。",
    )
    parser.add_argument("--config", help="配置文件路径 (默认依次查找 ./.env、脚本目录/.env)")
    parser.add_argument("--auth", choices=AUTH_MODES,
                        help="认证模式 (默认取 LIGHTLAYER_AUTH 或 basic): "
                             "basic=HTTP Basic(当前 WAF 拦 /login 时唯一可用), "
                             "auto=优先 token、被拦时降级 basic, bearer=仅 token")
    parser.add_argument("-y", "--yes", action="store_true", help="跳过下单确认")
    # 子命令也接受 --config/--auth/-y, 用 SUPPRESS 避免覆盖顶层已解析的值
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--config", default=argparse.SUPPRESS, help=argparse.SUPPRESS)
    common.add_argument("--auth", choices=AUTH_MODES, default=argparse.SUPPRESS,
                        help=argparse.SUPPRESS)
    sub = parser.add_subparsers(dest="command")

    p_order = sub.add_parser("order", parents=[common], help="一键下单(默认命令)")
    p_order.add_argument("-y", "--yes", action="store_true", default=argparse.SUPPRESS,
                         help="跳过确认直接下单")
    p_order.add_argument("--no-quote", action="store_true", help="跳过下单前的价格试算")
    p_order.add_argument("--product-id", help="产品ID (默认取 LIGHTLAYER_PRODUCT_ID)")
    p_order.add_argument("--cycle", help=f"计费周期, {CYCLE_HELP} (默认取配置或 m)")
    p_order.add_argument("--pay-method", help="支付方式名称或数字ID (默认取配置或 Alipay)")
    p_order.add_argument("--promocode", help="优惠码")
    p_order.add_argument("--domain", help="产品需关联域名时填写")

    p_quote = sub.add_parser("quote", parents=[common], help="仅试算价格, 不下单")
    add_order_args(p_quote)

    sub.add_parser("products", parents=[common], help="列出产品分类/产品/价格 (查 product_id)")
    p_forms = sub.add_parser("forms", parents=[common], help="查看产品下单表单(字段/item id/默认值)")
    p_forms.add_argument("--product-id", help="产品ID (默认取 LIGHTLAYER_PRODUCT_ID)")
    sub.add_parser("payments", parents=[common], help="列出支付方式 (查 Alipay 的 id)")
    sub.add_parser("invoices", parents=[common], help="列出账单")
    p_inv = sub.add_parser("invoice", parents=[common], help="查看单个账单详情")
    p_inv.add_argument("--id", required=True, help="账单ID")
    sub.add_parser("login", parents=[common], help="验证登录")
    sub.add_parser("logout", parents=[common], help="登出并吊销 token")
    return parser


def add_order_args(p):
    p.add_argument("--product-id", help="产品ID (默认取 LIGHTLAYER_PRODUCT_ID)")
    p.add_argument("--cycle", help=f"计费周期, {CYCLE_HELP}")
    p.add_argument("--pay-method", help="支付方式名称或数字ID")
    p.add_argument("--promocode", help="优惠码")
    p.add_argument("--domain", help="产品需关联域名时填写")
    p.add_argument("--custom", help='产品配置覆盖, JSON 如 \'{"3615": {"32925": 1}}\'')


def main(argv=None):
    parser = build_parser()
    args = parser.parse_args(argv)
    command = args.command or "order"

    cfg = load_config(getattr(args, "config", None))
    if getattr(args, "auth", None):
        cfg["LIGHTLAYER_AUTH"] = args.auth  # 命令行参数优先于配置文件
    api = make_client(cfg)

    handlers = {
        "order": cmd_order,
        "quote": cmd_quote,
        "products": cmd_products,
        "forms": cmd_forms,
        "payments": cmd_payments,
        "invoices": cmd_invoices,
        "invoice": cmd_invoice,
        "login": cmd_login,
        "logout": cmd_logout,
    }
    try:
        handlers[command](api, args, cfg)
    except ApiError as e:
        print(f"[!] {e}", file=sys.stderr)
        sys.exit(1)
    except KeyboardInterrupt:
        print("\n[i] 已中断")
        sys.exit(130)


if __name__ == "__main__":
    main()
