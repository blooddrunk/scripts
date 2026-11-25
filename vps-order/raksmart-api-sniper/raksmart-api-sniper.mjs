#!/usr/bin/env node
/**
 * Raksmart VPS API 直连抢购脚本（零依赖，Node >= 18）
 *
 * 完全绕过 billing.raksmart.com/ec 的 React 页面，直接调用其 Medusa 后端：
 *   登录(表单 POST + OAuth 四跳) → 轮询报价 → add-order → checkout-cart
 *
 * 用法：
 *   node raksmart-api-sniper.mjs [--config ./config.json] [--dry-run] [--force] [--once]
 *
 * 配置优先级：命令行 --config 指定的 JSON > 环境变量 > 内置 DEFAULTS。
 * 密码建议用环境变量 RAKSMART_PASSWORD 传入，避免写进文件。
 *
 * --dry-run 走完整链路但只轮询报价、绝不提交订单；
 * --force   忽略本地“本场已下单”状态文件强制开跑；
 * --once    不等待场次，立即做一次报价校验后退出（用于验证配置/登录）。
 */

import { readFileSync, writeFileSync, existsSync } from 'node:fs';
import { argv, env, exit } from 'node:process';
import { randomBytes } from 'node:crypto';

/* ================= 常量（逆向自 rak-ec-service.raksmart.com） ================= */

const API_BASE = 'https://rak-ec-service.raksmart.com/store';
const BILLING_ORIGIN = 'https://billing.raksmart.com';
const PUBLISHABLE_KEY = 'pk_01HKV8SJC13X083AN545TWEPS4';
const LOGIN_URL = `${BILLING_ORIGIN}/whmcs/medusa-login.php`;
const STATE_FILE = new URL('./.raksmart-sniper-state.json', import.meta.url);

const DAY_MS = 24 * 60 * 60 * 1000;

/* ================= 默认配置 ================= */

const DEFAULTS = {
  // 账号（密码优先走环境变量 RAKSMART_PASSWORD）
  email: '',
  password: '',

  // 商品三要素（订购页 URL 里的 pid / sample_id / activity_id）
  pid: '1889',
  sampleId: 'psam_01M24N3CFJDQ6AX41HVYFT5GCF',
  activityId: 'act_01M4TBMTG2VMWJPXXCYX2WZS2S',

  // 期望价（USD）与付款周期（Medusa 周期码：monthly/quarterly/semiannually/annually）
  expectedPrice: 49.9,
  billingcycle: 'annually',

  // 主机名 / root 密码；留空则自动生成。注意 need_host_name 的商品必须填或生成。
  hostname: '',
  rootpw: '',

  // 是否勾选“内网”（对应 customfield private_network_enabled）
  internalNetwork: true,

  // 场次：once = 单次 ISO8601 起止；daily = 每日多个 HH:mm 时段 + UTC 偏移分钟
  scheduleMode: 'once',
  saleStart: '',                    // 例 '2026-11-16T20:00:00+08:00'
  saleEnd: '',
  dailyWindows: [{ start: '20:00', end: '20:10' }],
  utcOffsetMinutes: 480,

  // 抢购节奏
  preflightQuoteIntervalMs: 5000,   // 等待期报价巡检间隔（顺带刷新结账用折扣 id）
  skipPreflightQuote: false,        // true 等同命令行 --skip-quote：跳过预检，开抢直接加购
  orderIntervalMs: 450,             // 场次内 add-order 重试间隔（库存不足时）
  orderTimeoutMs: 15000,            // 单次下单请求超时
  quoteTimeoutMs: 15000,
  checkoutQuoteMaxAttempts: 3,     // 结账前补报价的总次数；0 表示临时故障时持续重试
  parallel: 1,                      // 场次内并行下单请求数（1-5）。多个成功只会结账第一个，其余停留在购物车不生成订单

  // 与服务器时间的手动偏移（毫秒，正数=本机慢）。默认自动用响应 Date 头校准。
  clockOffsetMs: null,
};

/* ================= 极简命令行解析 ================= */

function parseArgs() {
  const out = { flags: new Set(), configPath: null };
  for (let i = 2; i < argv.length; i += 1) {
    const arg = argv[i];
    if (arg === '--config') out.configPath = argv[++i];
    else if (arg.startsWith('--')) out.flags.add(arg.slice(2));
    else console.error(`未知参数：${arg}`);
  }
  return out;
}

const { flags, configPath } = parseArgs();
const DRY_RUN = flags.has('dry-run');
const FORCE = flags.has('force');
const ONCE = flags.has('once');
const SKIP_QUOTE_FLAG = flags.has('skip-quote') || flags.has('no-quote');

function loadConfig() {
  let fromFile = {};
  if (configPath && existsSync(configPath)) {
    fromFile = JSON.parse(readFileSync(configPath, 'utf8'));
  } else if (existsSync('config.json')) {
    fromFile = JSON.parse(readFileSync('config.json', 'utf8'));
  }
  const cfg = { ...DEFAULTS, ...fromFile };
  if (env.RAKSMART_EMAIL) cfg.email = env.RAKSMART_EMAIL;
  if (env.RAKSMART_PASSWORD) cfg.password = env.RAKSMART_PASSWORD;
  if (env.RAKSMART_ORDER_INTERVAL_MS) cfg.orderIntervalMs = Number(env.RAKSMART_ORDER_INTERVAL_MS);
  return cfg;
}

const CONFIG = loadConfig();

// 跳过加购前的报价请求。自动结账仍需确认报价和优惠券。
const SKIP_QUOTE = SKIP_QUOTE_FLAG || Boolean(CONFIG.skipPreflightQuote);

/* ================= 日志 ================= */

function log(...parts) {
  const ts = new Date().toLocaleTimeString('zh-CN', { hour12: false, timeZone: 'Asia/Shanghai' });
  console.log(`[${ts}]`, ...parts);
}

function die(message, code = 1) {
  console.error(`✗ ${message}`);
  exit(code);
}

/* ================= Cookie Jar（够用的最小实现） ================= */

class CookieJar {
  constructor() { this.jar = new Map(); }

  static domainMatch(host, cookieDomain) {
    return host === cookieDomain || host.endsWith(`.${cookieDomain}`);
  }

  store(url, setCookieHeaders) {
    const host = new URL(url).hostname;
    for (const raw of setCookieHeaders) {
      if (!raw) continue;
      const [pair, ...attrs] = raw.split(';');
      const eq = pair.indexOf('=');
      const name = pair.slice(0, eq).trim();
      const value = pair.slice(eq + 1).trim();
      let domain = host;
      let path = '/';
      let expires = Infinity;
      for (const attr of attrs) {
        const [k, v] = attr.split('=').map((s) => s.trim());
        const key = k.toLowerCase();
        if (key === 'domain' && v) domain = v.replace(/^\./, '');
        else if (key === 'path' && v) path = v;
        else if (key === 'max-age') expires = Date.now() + Number(v) * 1000;
        else if (key === 'expires') { const t = Date.parse(v); if (Number.isFinite(t)) expires = t; }
      }
      if (Date.now() >= expires) { this.remove(domain, name); continue; }
      this.jar.set(`${domain}|${path}|${name}`, { domain, path, name, value, expires });
    }
  }

  remove(domain, name) {
    for (const key of [...this.jar.keys()]) {
      if (key.startsWith(`${domain}|`) && key.endsWith(`|${name}`)) this.jar.delete(key);
    }
  }

  header(url) {
    const { hostname, pathname } = new URL(url);
    const pairs = [];
    for (const cookie of this.jar.values()) {
      if (!CookieJar.domainMatch(hostname, cookie.domain)) continue;
      if (pathname !== '/' && !pathname.startsWith(cookie.path)) continue;
      if (Date.now() >= cookie.expires) continue;
      pairs.push(`${cookie.name}=${cookie.value}`);
    }
    return pairs.join('; ');
  }
}

/* ================= HTTP（手动跟随重定向，跨域 cookie 分域保存） ================= */

const UA = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36';

class Http {
  constructor(jar) { this.jar = jar; }

  async request(url, { method = 'GET', body, headers = {}, timeoutMs = 15000, maxRedirects = 0, redirectOrigin } = {}) {
    let currentUrl = url;
    let currentMethod = method;
    let currentBody = body;
    let currentHeaders = { ...headers };
    let lastDateHeader = null;

    for (let hop = 0; hop <= maxRedirects; hop += 1) {
      const parsed = new URL(currentUrl);
      const baseHeaders = {
        'User-Agent': UA,
        Accept: 'application/json, text/plain, */*',
        'Accept-Language': 'zh-CN,zh;q=0.9,en;q=0.8',
      };
      // 跨站跳转后仍以 billing 为来源（OAuth 链路按浏览器行为带 Origin/Referer）
      const originHeader = redirectOrigin || (parsed.hostname.endsWith('raksmart.com') ? BILLING_ORIGIN : undefined);
      if (originHeader) {
        baseHeaders.Origin = originHeader;
        baseHeaders.Referer = `${BILLING_ORIGIN}/`;
      }
      const cookie = this.jar.header(currentUrl);
      if (cookie) baseHeaders.Cookie = cookie;

      const controller = new AbortController();
      const timer = setTimeout(() => controller.abort(), timeoutMs);
      const fetchStartedAt = Date.now();
      let response;
      try {
        response = await fetch(currentUrl, {
          method: currentMethod,
          headers: { ...baseHeaders, ...currentHeaders },
          body: currentBody,
          redirect: 'manual',
          signal: controller.signal,
        });
      } catch (error) {
        if (error?.name === 'AbortError') {
          throw new Error(`请求超时（${timeoutMs}ms）：${currentMethod} ${currentUrl}`);
        }
        throw error;
      } finally {
        clearTimeout(timer);
      }

      const dateHeader = response.headers.get('date');
      if (dateHeader) {
        lastDateHeader = dateHeader;
        recordClockSample(currentUrl, dateHeader, fetchStartedAt, Date.now());
      }
      this.jar.store(currentUrl, response.headers.getSetCookie?.() ?? []);

      if ([301, 302, 303, 307, 308].includes(response.status) && hop < maxRedirects) {
        const location = response.headers.get('location');
        if (!location) return { response, url: currentUrl, dateHeader: lastDateHeader };
        currentUrl = new URL(location, currentUrl).toString();
        if ([301, 302, 303].includes(response.status) && currentMethod !== 'GET' && !(currentBody instanceof FormData)) {
          currentMethod = 'GET';
          currentBody = undefined;
          currentHeaders = {};
        }
        continue;
      }
      return { response, url: currentUrl, dateHeader: lastDateHeader };
    }
    return die('重定向次数超限（登录链路异常）');
  }

  async apiJson(path, { method = 'POST', payload, timeoutMs = 15000 } = {}) {
    const url = `${API_BASE}${path}`;
    const { response } = await this.request(url, {
      method,
      headers: {
        'Content-Type': 'application/json',
        'x-publishable-api-key': PUBLISHABLE_KEY,
      },
      body: payload === undefined ? undefined : JSON.stringify(payload),
      timeoutMs,
      redirectOrigin: BILLING_ORIGIN,
    });
    let json = null;
    try { json = await response.json(); } catch { /* 非 JSON 响应按 null 处理 */ }
    return { status: response.status, ok: response.ok, body: json };
  }
}

/* ================= 时钟校准（从已有响应的 Date 头顺带采样，零额外请求） ================= */

// Date 头只有秒级精度，且反映响应生成时刻，因此样本 = 服务器时间 - 请求起止中点。
// 只采 rak-ec-service（放库存的机器）的响应；中位数抵消单次网络抖动。
const clockSamples = [];

function recordClockSample(url, dateHeader, startedAt, endedAt) {
  try {
    if (new URL(url).hostname !== 'rak-ec-service.raksmart.com') return;
    const serverMs = Date.parse(dateHeader);
    if (Number.isFinite(serverMs)) clockSamples.push(serverMs - (startedAt + endedAt) / 2);
  } catch (error) { /* 忽略异常样本 */ }
}

function medianClockOffset() {
  if (!clockSamples.length) return null;
  const sorted = [...clockSamples].sort((a, b) => a - b);
  return sorted[Math.floor(sorted.length / 2)];
}

/* ================= 登录：medusa-login.php → /store/oauth → authorize.php → callback ================= */

// deadlineTs 前持续重试（网络类错误）；账号密码错误会在 loginOnce 里立即终止。
async function login(http, deadlineTs = Date.now() + 90_000) {
  let attempt = 0;
  let waitMs = 3000;
  while (true) {
    try {
      return await loginOnce(http);
    } catch (error) {
      attempt += 1;
      if (Date.now() > deadlineTs) die(`登录持续失败（共 ${attempt} 次，最后错误：${error.message}）`);
      log(`登录第 ${attempt} 次尝试失败（${error.message}），${waitMs / 1000}s 后重试`);
      await sleep(waitMs);
      waitMs = Math.min(waitMs * 2, 15000);
    }
  }
}

async function loginOnce(http) {
  const form = new URLSearchParams({
    username: CONFIG.email,
    password: CONFIG.password,
  }).toString();

  // 第一跳：表单 POST（浏览器里登录页就是直接跨域提交到这里，无 CSRF token）
  const first = await http.request(LOGIN_URL, {
    method: 'POST',
    headers: {
      'Content-Type': 'application/x-www-form-urlencoded',
      Origin: 'https://www.raksmart.com',
      Referer: 'https://www.raksmart.com/',
    },
    body: form,
    timeoutMs: 20000,
    maxRedirects: 0,
  });
  if (![302, 303].includes(first.response.status)) {
    // 服务器明确拒绝（如账密错误跳到登录页）：重试无意义，立即终止
    die(`登录第一跳失败（HTTP ${first.response.status}），请检查账号密码`);
  }

  // 后续 OAuth 链路整体跟随（同源 cookie 已在 jar 中分域保存）。
  // 高峰期 5xx 常见——抛错交给 login() 指数退避重试，而不是退出。
  const chain = await http.request(`${API_BASE}/oauth?rt=${encodeURIComponent('https://www.raksmart.com')}`, {
    maxRedirects: 5,
    timeoutMs: 25000,
    redirectOrigin: BILLING_ORIGIN,
  });
  if (chain.response.status >= 400) {
    throw new Error(`OAuth 链路失败（最终 HTTP ${chain.response.status}）`);
  }

  const me = await http.apiJson('/customers/me', { method: 'GET', timeoutMs: 15000 });
  if (me.status !== 200 || !me.body?.customer) {
    throw new Error(`登录后校验失败（customers/me HTTP ${me.status}）：${JSON.stringify(me.body)?.slice(0, 200)}`);
  }
  const customer = me.body.customer;
  log(`✓ 登录成功：${customer.email}（${customer.id}）`);
  return customer;
}

/* ================= 时钟校准（用响应 Date 头，中位数） ================= */

// 校时不再发起专用请求：登录 OAuth 链路与商品模板请求的响应 Date 头
// 已被 recordClockSample 顺带采样。样本不足时才补打少量请求（失败可忽略）。
async function calibrateClock(http) {
  if (CONFIG.clockOffsetMs !== null) {
    log(`时钟偏移（手动）：${CONFIG.clockOffsetMs}ms`);
    return CONFIG.clockOffsetMs;
  }
  if (clockSamples.length < 2) {
    for (let i = 0; i < 3 && clockSamples.length < 2; i += 1) {
      try {
        await http.request(`${API_BASE}/products/template/native-ip`, {
          method: 'GET',
          headers: { 'x-publishable-key': PUBLISHABLE_KEY },
          timeoutMs: 10000,
          redirectOrigin: BILLING_ORIGIN,
        });
      } catch (error) { /* 校时是尽力而为，失败不影响下单 */ }
    }
  }
  const offset = medianClockOffset();
  if (offset === null) {
    log('⚠ 未能采样服务器时间，跳过校准（偏移按 0 处理，不影响下单，仅影响开抢时刻精度）');
    return 0;
  }
  log(`时钟偏移（服务器-本机，${clockSamples.length} 个样本中位数）：${offset >= 0 ? '+' : ''}${offset}ms`);
  return offset;
}

/* ================= 场次计算（与 Tampermonkey 版一致） ================= */

function parseDailyTime(value) {
  const match = /^(\d{2}):(\d{2})$/.exec(String(value || ''));
  if (!match) return null;
  const hours = Number(match[1]);
  const minutes = Number(match[2]);
  if (hours > 23 || minutes > 59) return null;
  return (hours * 60 + minutes) * 60 * 1000;
}

function normalizeDailyWindows() {
  return (CONFIG.dailyWindows ?? [])
    .map((w) => ({
      startText: String(w?.start || '').trim(),
      endText: String(w?.end || '').trim(),
    }))
    .filter((w) => w.startText || w.endText)
    .map((w) => ({ ...w, start: parseDailyTime(w.startText), end: parseDailyTime(w.endText) }));
}

function resolveSchedule(timestamp) {
  if (CONFIG.scheduleMode === 'daily') {
    const windows = normalizeDailyWindows();
    const offset = Number(CONFIG.utcOffsetMinutes);
    if (!windows.length) return { configured: false, valid: false };
    if (!windows.every((w) => w.start !== null && w.end !== null && w.start !== w.end)
      || !Number.isFinite(offset)) {
      return { configured: true, valid: false };
    }
    const offsetMs = offset * 60 * 1000;
    const dayStart = Math.floor((timestamp + offsetMs) / DAY_MS) * DAY_MS - offsetMs;
    const occurrences = windows.map((w) => {
      let start = dayStart + w.start;
      let end = dayStart + w.end + (w.end <= w.start ? DAY_MS : 0);
      if (timestamp >= end) { start += DAY_MS; end += DAY_MS; }
      return { start, end };
    });
    const active = occurrences.filter((o) => timestamp >= o.start && timestamp < o.end)
      .sort((a, b) => a.start - b.start)[0];
    const upcoming = active || occurrences.filter((o) => o.start > timestamp)
      .sort((a, b) => a.start - b.start)[0];
    return {
      configured: true,
      valid: true,
      start: upcoming.start,
      end: upcoming.end,
      description: `每天 ${windows.map((w) => `${w.startText}–${w.endText}`).join('、')}（UTC${offset >= 0 ? '+' : ''}${offset / 60}）`,
    };
  }
  const start = CONFIG.saleStart ? Date.parse(CONFIG.saleStart) : NaN;
  const end = CONFIG.saleEnd ? Date.parse(CONFIG.saleEnd) : NaN;
  return {
    configured: Boolean(CONFIG.saleStart || CONFIG.saleEnd),
    valid: Number.isFinite(start) && Number.isFinite(end) && end > start,
    start,
    end,
    description: `${CONFIG.saleStart} 至 ${CONFIG.saleEnd}`,
  };
}

function now(clockOffset) {
  return Date.now() + clockOffset;
}

/* ================= 商品模板 → 下单参数 ================= */

// pid 1889 页面默认选中的配置（抓包自正式订购页；其他商品请用 config.json 覆盖）
const PAGE_DEFAULT_CONFIGOPTIONS = {
  29329: 322219,   // Zone: SV（隐藏）
  29330: 322486,   // Plan: 2 Cores,4 G
  29331: 363316,   // OS: CentOS 7.6
  29332: 322225,   // IP Type: 新建公网IP（隐藏）
  29333: 346588,   // VPC Type: None（隐藏）
  29334: 322229,   // Login Type: Password
  31352: 346151,   // Bandwidth Type: 精品CN2
  31354: '0',      // Bandwidth（数量型）
  31356: '1',      // IP Num（数量型）
  31358: 346158,   // IP Region: SV（隐藏）
  31383: 346207,   // DDoS IP 1: 无
  31384: 346220,   // DDoS IP 2: 无
  31385: 346235,   // DDoS IP 3: 无
  31386: 346244,   // DDoS IP 4: 无
  31387: 346257,   // DDoS IP 5: 无
  31450: 346591,   // Site IP: 无
  31451: '0',      // Native Regional IP（数量型）
  31452: 346611,   // Network Type: 经典网络（隐藏）
  31555: 348540,   // System Disk: NVMe 50G
  33050: 373777,   // Region: sv2（隐藏）
  33081: 373809,   // Billing Method: 流量计费
  33110: 386738,   // Traffic: Enterprise CN2 1T
  33311: 376318,   // Advanced BW（隐藏）
  33358: 376365,   // Advanced CPU: SV（隐藏）
  33371: 387530,   // MultiNetworkSiteIP（隐藏）
  33382: 378155,   // Os Type: OS
  33386: 378178,   // System disk type
  33393: 384965,   // Inbound Bandwidth（隐藏）
  29343: 322246,   // Data Disk Type: 无
};

// customfield 名称 → 下单值。内网 = private_network_enabled → "on"
const CUSTOMFIELD_RULES = {
  private_network_enabled: 'on',
};

async function withRetries(label, fn, times = 3, waitMs = 3000) {
  for (let attempt = 1; attempt <= times; attempt += 1) {
    try {
      return await fn();
    } catch (error) {
      if (attempt === times) throw error;
      log(`${label} 第 ${attempt} 次尝试失败（${error.message}），${waitMs / 1000}s 后重试`);
      await sleep(waitMs);
    }
  }
}

// 商品模板是下单的硬依赖（product_id / group_id / 配置项映射），失败持续重试直到场次结束。
async function fetchTemplateUntil(http, deadlineTs) {
  let attempt = 0;
  let waitMs = 3000;
  while (true) {
    attempt += 1;
    let result;
    try {
      result = await http.apiJson(`/products/template/whmcs/${CONFIG.pid}`, {
        payload: { sample_id: CONFIG.sampleId, activity_id: CONFIG.activityId },
        timeoutMs: 30000,
      });
    } catch (error) {
      result = { status: 0, body: null, error: error.message };
    }
    if (result.status === 200 && result.body?.product) return result.body;
    const detail = result.error ?? `HTTP ${result.status} ${JSON.stringify(result.body)?.slice(0, 120)}`;
    if (Date.now() > deadlineTs) {
      die(`商品模板持续获取失败（共 ${attempt} 次，最后：${detail}）——没有模板无法构造下单参数，终止`);
    }
    log(`获取商品模板第 ${attempt} 次失败（${detail}），${waitMs / 1000}s 后重试`);
    await sleep(waitMs);
    waitMs = Math.min(waitMs * 2, 30000);
  }
}

function buildOrderParams(template) {
  const whmcs = template.product?.metadata?.whmcs ?? {};
  const configOptions = { ...PAGE_DEFAULT_CONFIGOPTIONS, ...CONFIG.configOptions };

  // 模板里出现而默认表缺失的配置项（含隐藏项）：补第一个未隐藏选项，数量型补 "0"/"1"
  for (const co of whmcs.configoptions?.configoption ?? []) {
    const key = String(co.id);
    if (configOptions[key] !== undefined) continue;
    const options = co.options?.option ?? [];
    if (!options.length) continue;
    if (String(co.type) === '4') {
      configOptions[key] = String(Number(options[0]?.qtyminimum ?? 0));
    } else {
      const chosen = options.find((o) => !Number(o.hidden)) ?? options[0];
      configOptions[key] = chosen.id;
    }
  }

  // 内网等 customfield
  const customFields = {};
  for (const cf of whmcs.customfields?.customfield ?? []) {
    const rule = CUSTOMFIELD_RULES[cf.name];
    if (rule === 'on' && cf.name === 'private_network_enabled' && !CONFIG.internalNetwork) continue;
    if (rule) customFields[String(cf.id)] = rule;
  }

  return {
    productId: template.product.id,
    groupId: whmcs.gid,
    configOptions,
    customFields,
    needHostname: Boolean(template.price_caculator_templates?.need_host_name),
    needRootPw: Boolean(template.price_caculator_templates?.need_console_pwd),
  };
}

function randomHostname() {
  return `vps-${randomBytes(3).toString('hex')}`;
}

function randomRootPw() {
  const alphabet = 'abcdefghjkmnpqrstuvwxyzABCDEFGHJKMNPQRSTUVWXYZ23456789';
  let pw = '';
  for (let i = 0; i < 14; i += 1) pw += alphabet[randomBytes(1)[0] % alphabet.length];
  return `${pw}!aA1`;
}

function buildOrderBody(params, customerId) {
  const hostname = CONFIG.hostname.trim() || (params.needHostname ? randomHostname() : '');
  const rootpw = CONFIG.rootpw.trim() || (params.needRootPw ? randomRootPw() : '');
  return {
    domain: hostname,
    rootpw,
    pid: String(CONFIG.pid),
    product_id: params.productId,
    billingcycle: CONFIG.billingcycle,
    quantity: 1,
    activity_id: CONFIG.activityId,
    price_list_id: '',
    configoptions_data: params.configOptions,
    customfields_data: params.customFields,
    sample_id: CONFIG.sampleId,
    aff_id: 0,
    aff_rtype: '',
    language: 'chinese-cn',
    group_id: params.groupId,
    notes: '',
    agree_terms: 1,
    customer_id: customerId,
  };
}

/* ================= 报价 / 下单 / 结账 ================= */

function buildQuoteBody(params) {
  return {
    product_id: params.productId,
    billingcycle: CONFIG.billingcycle,
    activity_id: CONFIG.activityId,
    price_list_id: '',
    quantity: 1,
    language: 'chinese-cn',
    configoptions: params.configOptions,
    sample_id: CONFIG.sampleId,
  };
}

async function quote(http, params, timeoutMs) {
  return http.apiJson('/custom/price/get-product-price', {
    payload: buildQuoteBody(params),
    timeoutMs: timeoutMs ?? CONFIG.quoteTimeoutMs,
  });
}

function parsePrice(value) {
  if (typeof value === 'number') return value;
  return typeof value === 'string' && /^\d+(?:\.\d+)?$/.test(value.trim()) ? Number(value) : NaN;
}

function quoteVerdict({ status, body }) {
  if (status !== 200) {
    return { ok: false, reason: `报价接口 HTTP ${status}`, retryable: status === 408 || status === 429 || status >= 500 };
  }
  if (!body || typeof body !== 'object' || Array.isArray(body)) {
    return { ok: false, reason: '报价接口未返回有效的 JSON 对象', retryable: true };
  }
  const price = parsePrice(body.priceoverride ?? body.discounted_price ?? body.unit_price);
  const validate = body.activity_validate;
  if (validate && validate.status === false) {
    return { ok: false, reason: `活动校验失败：${validate.msg || '未开始/已结束'}`, price };
  }
  if (!Number.isFinite(price)) return { ok: false, reason: '响应缺少有效价格' };
  const expectedPrice = parsePrice(CONFIG.expectedPrice);
  if (!Number.isFinite(expectedPrice)) return { ok: false, reason: 'expectedPrice 无效', price };
  if (price !== expectedPrice) {
    return { ok: false, reason: `价格不符：$${price}（期望 $${expectedPrice}）`, price };
  }
  const discountIds = body.use_discount_ids;
  if (!Array.isArray(discountIds) || !discountIds.length || discountIds.some((id) => typeof id !== 'string' || !id.trim())) {
    return { ok: false, reason: '报价未返回有效的优惠券 ID', price };
  }
  return { ok: true, price, discountIds };
}

function describeQuote(verdict) {
  const detail = verdict.ok ? '✓ 匹配，已取得优惠券 ID' : verdict.reason;
  return Number.isFinite(verdict.price) ? `$${verdict.price.toFixed(2)}｜${detail}` : detail;
}

async function placeOrder(http, params, customerId, timeoutMs) {
  return http.apiJson('/custom/cart/add-order', {
    payload: buildOrderBody(params, customerId),
    timeoutMs: timeoutMs ?? CONFIG.orderTimeoutMs,
  });
}

async function checkoutCart(http, cartId, customerId, discountIds) {
  return http.apiJson('/custom/whmcs-cart/checkout-cart', {
    payload: {
      cart_id: cartId,
      customer_id: customerId,
      discount_ids: discountIds,
      consume_point: 0,
      notes: '',
      agree_terms: true,
      consume_voucher_ids: [],
      contact_id: 0,
    },
    timeoutMs: 25000,
  });
}

function paymentUrlFromBody(body) {
  if (!body) return null;
  if (typeof body.invoice_url === 'string' && body.invoice_url) {
    return body.invoice_url.includes('?')
      ? `${body.invoice_url}&language=chinese-cn`
      : `${body.invoice_url}?language=chinese-cn`;
  }
  const invoiceId = body.invoice_id ?? body.whmcs_order?.data?.invoiceid;
  if (invoiceId) return `${BILLING_ORIGIN}/whmcs/viewinvoice.php?id=${invoiceId}&language=chinese-cn`;
  return null;
}

/* ================= 防重复状态文件 ================= */

function attemptKey(schedule) {
  return [CONFIG.pid, CONFIG.sampleId, CONFIG.activityId, schedule.start].join(':');
}

function readState() {
  if (!existsSync(STATE_FILE)) return null;
  try { return JSON.parse(readFileSync(STATE_FILE, 'utf8')); } catch { return null; }
}

function writeState(state) {
  writeFileSync(STATE_FILE, JSON.stringify(state, null, 2));
}

/* ================= 主流程 ================= */

const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

function formatCountdown(ms) {
  const total = Math.max(0, Math.ceil(ms / 1000));
  const h = Math.floor(total / 3600);
  const m = Math.floor((total % 3600) / 60);
  const s = total % 60;
  return h > 0 ? `${h}时${m}分${s}秒` : `${m}分${s}秒`;
}

async function run() {
  if (!CONFIG.email || !CONFIG.password) die('缺少账号：请在 config.json 或环境变量 RAKSMART_EMAIL / RAKSMART_PASSWORD 中配置');
  const maxQuoteAttempts = CONFIG.checkoutQuoteMaxAttempts;
  if (!Number.isSafeInteger(maxQuoteAttempts) || maxQuoteAttempts < 0) {
    die('checkoutQuoteMaxAttempts 必须是非负整数（0 表示不限次数）');
  }

  const schedule = resolveSchedule(Date.now());
  if (!ONCE && (!schedule.configured || !schedule.valid)) {
    die(`场次配置无效：${schedule.description || '未配置'}（once 需要 saleStart/saleEnd，daily 需要 dailyWindows）`);
  }

  const http = new Http(new CookieJar());
  const customer = await login(http, ONCE ? Date.now() + 120_000 : schedule.end);
  const clockOffset = await calibrateClock(http);

  const template = await fetchTemplateUntil(http, ONCE ? Date.now() + 120_000 : schedule.end);
  const params = buildOrderParams(template);
  log(`✓ 商品模板：${template.product.title}（${params.productId}，${Object.keys(params.configOptions).length} 个配置项）`);

  // 报价预检失败不阻塞加购。有效报价可复用，避免结账前重复查询。
  let checkoutQuote;
  let receivedQuote = false;
  if (SKIP_QUOTE) {
    log('--skip-quote：已跳过报价预检，开抢后直接加购');
  } else {
    try {
      const first = await quote(http, params);
      checkoutQuote = quoteVerdict(first);
      receivedQuote = first.status === 200 && Boolean(first.body);
      log(`报价：${describeQuote(checkoutQuote)}`);
    } catch (error) {
      log(`⚠ 首次报价失败（${error.message}），加购成功后会补查报价`);
    }
  }
  if (ONCE) {
    if (SKIP_QUOTE) die('--once 需要启用报价预检');
    if (!receivedQuote) die('--once 未取得报价响应');
    log('--once 模式：校验完成，退出（未提交订单）');
    return;
  }

  const key = attemptKey(schedule);
  const state = readState();
  if (state?.key === key && state.done && !FORCE) {
    die(`本场已成功下过单（${state.invoiceUrl ?? state.doneAt}）。如确需重跑请加 --force`);
  }

  log(`场次：${schedule.description}｜等待开始…`);
  let lastQuotePollAt = Date.now();
  while (now(clockOffset) < schedule.start) {
    const remain = schedule.start - now(clockOffset);
    if (remain > 60000) {
      // 等待期顺带巡检报价：刷新折扣 id、提示活动状态（尽力而为，失败静默）
      if (!SKIP_QUOTE && Date.now() - lastQuotePollAt > CONFIG.preflightQuoteIntervalMs) {
        lastQuotePollAt = Date.now();
        try {
          const check = await quote(http, params);
          if (check.status === 200) {
            // 新报价显式失效时废弃旧优惠，等加购成功后重新确认。
            checkoutQuote = quoteVerdict(check);
            process.stdout.write(`\r${' '.repeat(40)}\r`);
            log(`巡检报价：${describeQuote(checkoutQuote)}`);
          }
        } catch (error) { /* 等待期报价失败无需理会 */ }
      }
      process.stdout.write(`\r距开抢 ${formatCountdown(remain)}   `);
      await sleep(Math.min(remain - 60000, 30000) || 1000);
      continue;
    }
    // 最后 60 秒：不再发任何请求，精确卡点
    process.stdout.write(`\r距开抢 ${formatCountdown(remain)}   `);
    await sleep(Math.max(20, Math.min(remain, 200)));
  }
  console.log();
  if (now(clockOffset) >= schedule.end) {
    log('⚠ 预热重试耗时过长，窗口已在等待期间结束——未提交任何订单');
    writeState({ key, done: false, attempts: 0, reason: 'window-passed-during-preflight', finishedAt: new Date().toISOString() });
    return;
  }
  log('🚀 进入抢购窗口，开始提交订单');

  const windowEnd = schedule.end;
  const PARALLEL = Math.max(1, Math.min(5, Math.floor(Number(CONFIG.parallel) || 1)));
  const counter = { attempt: 0, ambiguous: 0, loginRetries: 0 };
  let success = null;
  let stopFlag = false;

  const purchaseWorker = async (workerIndex) => {
    // 并行时各 worker 错开半个间隔，避免完全同步打出
    await sleep(workerIndex * (CONFIG.orderIntervalMs / Math.max(1, PARALLEL)));

    if (DRY_RUN) {
      log('--dry-run：不提交订单，窗口内仅持续校验报价');
      while (now(clockOffset) < windowEnd) {
        const check = await withRetries('报价', () => quote(http, params), 2, 2000);
        log(`报价：${describeQuote(quoteVerdict(check))}`);
        await sleep(CONFIG.preflightQuoteIntervalMs);
      }
      return;
    }

    while (!stopFlag && !success && now(clockOffset) < windowEnd) {
      counter.attempt += 1;
      const attemptNo = counter.attempt;
      let result;
      try {
        result = await placeOrder(http, params, customer.id);
      } catch (error) {
        counter.ambiguous += 1; // 超时/网络错误：请求可能已到达服务端，不能盲目立即重发
        if (workerIndex === 0) {
          log(`#${attemptNo} 网络异常（${error.message}），继续重试（累计歧义请求 ${counter.ambiguous}）`);
        }
        await sleep(CONFIG.orderIntervalMs);
        continue;
      }

      const { status, body } = result;
      if (status === 200 && body?.cart?.id) {
        if (!success) {
          success = body;
          log(`✓ add-order 成功（cart ${body.cart.id}，第 ${attemptNo} 次尝试）`);
        } else {
          log(`⚠ 第 ${attemptNo} 次尝试也成功（cart ${body.cart.id}），仅结账第一个，多余购物车请到订购页清理`);
        }
        stopFlag = true;
        return;
      }
      if (status === 401) {
        counter.loginRetries += 1;
        if (counter.loginRetries > 3) die('会话反复失效，停止（请检查账号是否在其他设备登录被踢）');
        log(`#${attemptNo} 会话失效，重新登录（第 ${counter.loginRetries} 次）`);
        await login(http, Date.now() + 60_000);
        continue;
      }
      const message = body?.message || body?.error || JSON.stringify(body)?.slice(0, 120);
      if (status === 400 && /库存不足|售罄|sold out|out of stock/i.test(String(message))) {
        if (attemptNo <= 3 || attemptNo % 10 === 1) log(`#${attemptNo} 库存不足，继续重试`);
      } else if (status === 400 && /活动|activity/i.test(String(message))) {
        if (attemptNo % 20 === 1) log(`#${attemptNo} 活动未放量：${message}`);
      } else {
        log(`#${attemptNo} HTTP ${status}：${message}`);
        if (status === 400 && /configoption|customfield|invalid_data/i.test(String(body?.code))) {
          stopFlag = true;
          die('下单参数被服务端拒绝（配置项错误），终止。请核对 configOptions 配置');
        }
      }
      await sleep(CONFIG.orderIntervalMs);
    }
  };

  await Promise.all(Array.from({ length: PARALLEL }, (_, i) => purchaseWorker(i)));

  if (!success) {
    const { attempt, ambiguous } = counter;
    if (DRY_RUN) {
      log('窗口结束（--dry-run 模式，未提交任何订单）');
    } else if (ambiguous > 0) {
      log(`⚠ 窗口结束且存在 ${ambiguous} 个结果不明的请求，请人工确认是否已生成订单：${BILLING_ORIGIN}/whmcs/clientarea.php`);
    } else {
      log('✗ 窗口结束，未能抢到（库存不足）');
    }
    writeState({ key, done: false, attempts: attempt, finishedAt: new Date().toISOString() });
    return;
  }

  const cartId = success.cart.id;
  writeState({ key, done: 'cart', cartId, doneAt: new Date().toISOString() });

  // 只重试临时故障。0 不设次数上限，明确的拒绝仍会保留购物车。
  for (let attempt = 1; !checkoutQuote?.ok; attempt += 1) {
    try {
      const backfill = await quote(http, params, Math.min(8000, CONFIG.quoteTimeoutMs));
      checkoutQuote = quoteVerdict(backfill);
    } catch (error) {
      checkoutQuote = { ok: false, reason: `报价请求失败：${error.message}`, retryable: true };
    }
    if (!checkoutQuote.retryable || attempt === maxQuoteAttempts) break;
    log(`结账前报价第 ${attempt} 次失败（${checkoutQuote.reason}），3 秒后重试`);
    await sleep(3000);
  }
  if (!checkoutQuote.ok) {
    log(`⚠ 已保留购物车（${cartId}），未自动结账。原因：${checkoutQuote.reason}`);
    log(`请打开购物车核对价格和优惠券：${BILLING_ORIGIN}/ec/cart?lang=cn`);
    writeState({
      key, done: 'cart', cartId, reason: 'checkout-quote-unverified',
      quoteError: checkoutQuote.reason, doneAt: new Date().toISOString(),
    });
    return;
  }
  const { discountIds } = checkoutQuote;
  log(`立即结账（报价 $${checkoutQuote.price.toFixed(2)}，优惠券 ID：${discountIds.join(', ')}）`);

  // 结账请求超时 ≠ 失败：服务端可能已受理并在处理（实测：超时请求最终成功建单）。
  // 「is processing」也说明首次请求在途。因此超时/处理中/5xx 都轮询重试，业务性错误才停止。
  let checkout = null;
  let lastCheckoutError = '';
  for (let attempt = 1; attempt <= 8 && !checkout; attempt += 1) {
    let result;
    try {
      result = await checkoutCart(http, cartId, customer.id, discountIds);
    } catch (error) {
      result = { status: 0, body: null, error: error.message };
    }
    if (result.status === 200 || result.status === 202) {
      checkout = result;
      break;
    }
    lastCheckoutError = result.error
      ?? result.body?.message
      ?? JSON.stringify(result.body)?.slice(0, 150)
      ?? `HTTP ${result.status}`;
    const processing = /is processing/i.test(lastCheckoutError);
    const transient = processing || result.status === 0 || result.status >= 500;
    if (!transient) break; // 明确的业务拒绝（如折扣无效），重试无意义
    log(`结账第 ${attempt} 次未确认（${lastCheckoutError}）——服务端可能仍在处理，${processing ? 5 : 8}s 后重查`);
    await sleep(processing ? 5000 : 8000);
  }

  if (!checkout) {
    // 多次结账未确认：订单可能已在服务端生成（超时请求被受理的实测案例），
    // 不能自动重跑（会重复下单），交给人工核对。
    log(`⚠ 结账多次未确认（最后：${lastCheckoutError}）。超时的请求可能已生成订单，请立即核对：`);
    log(`  订单列表：${BILLING_ORIGIN}/whmcs/clientarea.php｜购物车：${BILLING_ORIGIN}/ec/cart?lang=cn`);
    writeState({ key, done: 'cart-unconfirmed', cartId, lastCheckoutError, doneAt: new Date().toISOString() });
    return;
  }

  if (checkout.status === 200 || checkout.status === 202) {
    const url = paymentUrlFromBody(checkout.body);
    const orderId = checkout.body?.whmcs_order?.data?.orderid;
    const invoiceId = checkout.body?.invoice_id ?? checkout.body?.whmcs_order?.data?.invoiceid;
    log(`🎉 下单成功！订单 ${orderId ?? '—'}｜账单 ${invoiceId ?? '—'}`);
    if (url) log(`付款页：${url}`);
    else log(`请在账单列表查看：${BILLING_ORIGIN}/whmcs/clientarea.php`);
    log('付款前请核对账单金额与活动价一致');
    writeState({
      key, done: true, cartId, orderId, invoiceId, invoiceUrl: url,
      discountIds,
      doneAt: new Date().toISOString(),
    });
  } else {
    log(`⚠ 结账返回 HTTP ${checkout.status}：${JSON.stringify(checkout.body)?.slice(0, 300)}`);
    log(`cart 已创建（${cartId}），可打开购物车手动完成结账：${BILLING_ORIGIN}/ec/cart?lang=cn`);
    writeState({ key, done: 'cart', cartId, checkoutStatus: checkout.status, doneAt: new Date().toISOString() });
  }
}

run().catch((error) => {
  console.error(error);
  exit(1);
});
