// ==UserScript==
// @name         Raksmart VPS 自动下单助手
// @namespace    https://github.com/jelinenaro/tampermonkey
// @version      2.0.0
// @description  API 直连版：绕过页面 DOM，直接调用 Raksmart 订购接口完成报价校验与下单，页面卡顿不再影响抢购；保留场次调度、防重复锁与状态面板。
// @author       jelinenaro
// @match        https://billing.raksmart.com/ec/*
// @run-at       document-start
// @grant        GM_getValue
// @grant        GM_setValue
// @grant        GM_deleteValue
// @grant        GM_registerMenuCommand
// @grant        GM_xmlhttpRequest
// @connect      billing.raksmart.com
// @connect      rak-ec-service.raksmart.com
// @license      MIT
// ==/UserScript==

/*
 * v2.0.0 引擎说明（相对 v1.x 的重写）
 *
 * v1.x 完全依赖订购页 DOM：等表单渲染、读价格节点、点购买按钮——
 * 抢购高峰 React 页面卡死时脚本随之失效。
 *
 * v2 直接调用页面背后的 Medusa 后端（https://rak-ec-service.raksmart.com/store）：
 *   1. GET  /store/customers/me               → 登录态检测
 *   2. POST /store/custom/price/get-product-price → 报价（价格、周期、活动校验）
 *   3. POST /store/custom/cart/add-order       → 下单（返回 cart id）
 *   4. POST /store/custom/whmcs-cart/checkout-cart → 结账生成订单与账单
 *
 * 请求用页面内 fetch 发出：CORS 允许 billing.raksmart.com 携带凭证，
 * 登录 Cookie（connect.sid，SameSite=None）随请求自动带上。
 * 因此本脚本可在任意 /ec/* 页面运行，无需订购页本身正常渲染。
 *
 * 自动重新登录（可选）：在设置里填入账号密码后，会话失效时用
 * GM_xmlhttpRequest 走 medusa-login.php → OAuth 链路重登；不填则提示手动登录。
 */

(function () {
  'use strict';

  if (window.top !== window.self) return;

  /* ================= 接口常量（逆向自订购前端，改版时需更新） ================= */

  const API_BASE = 'https://rak-ec-service.raksmart.com/store';
  const PUBLISHABLE_KEY = 'pk_01HKV8SJC13X083AN545TWEPS4';
  const LOGIN_URL = 'https://billing.raksmart.com/whmcs/medusa-login.php';
  const CLIENTAREA_URL = 'https://billing.raksmart.com/whmcs/clientarea.php';

  // pid 1889（SV 2C4G 双十一秒杀）订购页默认配置，抓包自正式页面。
  // 换商品时：先在订购页把配置选好，F12 抓 get-product-price 的 configoptions 填进来；
  // 模板中未被覆盖的隐藏配置项会在运行时自动补第一个可选值。
  const DEFAULT_CONFIGOPTIONS = {
    29330: 322486,   // Plan: 2 Cores,4 G
    29331: 363316,   // OS: CentOS 7.6
    31352: 346151,   // Bandwidth Type: 精品CN2
    31354: '0',      // Bandwidth（数量型）
    31356: '1',      // IP Num（数量型）
    31383: 346207,   // DDoS IP 1: 无
    31384: 346220,   // DDoS IP 2: 无
    31385: 346235,   // DDoS IP 3: 无
    31386: 346244,   // DDoS IP 4: 无
    31387: 346257,   // DDoS IP 5: 无
    31450: 346591,   // Site IP: 无
    31555: 348540,   // System Disk: NVMe 50G
    33081: 373809,   // Billing Method: 流量计费
    33110: 386738,   // Traffic: Enterprise CN2 1T
    33382: 378155,   // Os Type: OS
    33386: 378178,   // System disk type
  };

  // 付款周期显示文本 → Medusa billingcycle 代码
  const CYCLE_MAP = [
    [/每月|monthly/i, 'monthly'],
    [/每季|季度|quarterly/i, 'quarterly'],
    [/半年|semiannually/i, 'semiannually'],
    [/每年|年度|年付|annually/i, 'annually'],
  ];

  /* ================= 默认配置 ================= */

  const DEFAULT_CONFIG = {
    productId: '1889',
    sampleId: 'psam_01M24N3CFJDQ6AX41HVYFT5GCF',
    activityId: 'act_01M4TBMTG2VMWJPXXCYX2WZS2S',

    expectedPrice: 49.90,
    expectedCycle: '每年',
    internalNetwork: true,

    // 可选：自动重新登录的账号密码（留空则会话失效时仅提示手动登录）
    loginEmail: '',
    loginPassword: '',

    // once：单次，使用带时区的 ISO 8601 时间；daily：每日多时段，每段使用 HH:mm。
    scheduleMode: 'once',
    saleStart: '',
    saleEnd: '',
    dailyWindows: [{ start: '20:00', end: '20:10' }],

    // 每日场次使用固定 UTC 偏移，默认 UTC+8，不受电脑时区影响。
    utcOffsetMinutes: 480,
    clockOffsetMs: 0,

    preheatBeforeMinutes: 10,
    preheatQuoteIntervalMs: 5000,   // 预热期报价巡检间隔
    idleQuoteIntervalMs: 30000,     // 场次外报价巡检间隔
    orderIntervalMs: 1000,          // 抢购中 add-order 重试间隔
    orderTimeoutMs: 15000,
    attemptLockEnabled: true,
  };

  const SETTINGS_KEY = 'raksmart-auto-order:settings';
  const LOG_PREFIX = '[Raksmart 自动下单]';
  const DAY_MS = 24 * 60 * 60 * 1000;

  /* ================= 持久存储 ================= */

  const store = {
    get(key) {
      try {
        if (typeof GM_getValue === 'function') return GM_getValue(key);
      } catch (error) {
        console.warn(LOG_PREFIX, '读取 GM 存储失败', error);
      }
      try {
        const value = localStorage.getItem(key);
        return value === null ? undefined : JSON.parse(value);
      } catch (error) {
        return undefined;
      }
    },
    set(key, value) {
      try {
        if (typeof GM_setValue === 'function') {
          GM_setValue(key, value);
          return;
        }
      } catch (error) {
        console.warn(LOG_PREFIX, '写入 GM 存储失败', error);
      }
      try {
        localStorage.setItem(key, JSON.stringify(value));
      } catch (error) {
        console.error(LOG_PREFIX, '无法保存设置或下单锁', error);
      }
    },
    remove(key) {
      try {
        if (typeof GM_deleteValue === 'function') {
          GM_deleteValue(key);
          return;
        }
      } catch (error) {
        console.warn(LOG_PREFIX, '删除 GM 存储失败', error);
      }
      try {
        localStorage.removeItem(key);
      } catch (error) {
        console.warn(LOG_PREFIX, '删除本地数据失败', error);
      }
    },
  };

  const savedSettings = store.get(SETTINGS_KEY);
  const CONFIG = {
    ...DEFAULT_CONFIG,
    ...(savedSettings && typeof savedSettings === 'object' ? savedSettings : {}),
  };

  // v1 → v2 字段迁移：丢弃仅 DOM 模式使用的设置
  function migrateLegacySettings(settings) {
    delete settings.autoCheckInternalNetwork;
    delete settings.autoAcceptTerms;
    delete settings.quoteStableMs;
    delete settings.reloadAfterMs;
    delete settings.errorReloadAfterMs;
    delete settings.retryIntervalMs;
  }
  migrateLegacySettings(CONFIG);
  if (!savedSettings) store.set(SETTINGS_KEY, CONFIG);

  /* ================= 状态与场次计算 ================= */

  let paused = false;
  let panel = null;
  let scheduled = false;

  // 登录态与商品模板缓存
  const identity = { customer: null, checkedAt: 0, checking: null, failCount: 0 };
  const product = { template: null, configOptions: null, customFields: null, productId: null };

  // 报价缓存
  const quoteState = {
    lastAt: 0, inFlight: null,
    price: null, cycleText: '', validateOk: null, validateMsg: '', discountIds: [],
  };

  // 抢购执行状态
  const hunt = {
    active: false,          // 正处于抢购窗口且已开火
    inFlightOrder: false,
    lastOrderAt: 0,
    attempts: 0,
    lastError: '',
    result: null,           // { cartId, orderId, invoiceId, invoiceUrl, at }
  };

  function now() {
    return Date.now() + Number(CONFIG.clockOffsetMs || 0);
  }

  function normalize(text) {
    return String(text || '').replace(/\s+/g, '');
  }

  function parseDailyTime(value) {
    const match = /^(\d{2}):(\d{2})$/.exec(value);
    if (!match) return null;
    const hours = Number(match[1]);
    const minutes = Number(match[2]);
    if (hours > 23 || minutes > 59) return null;
    return (hours * 60 + minutes) * 60 * 1000;
  }

  function formatUtcOffset(minutes) {
    const sign = minutes < 0 ? '-' : '+';
    const absolute = Math.abs(minutes);
    return `UTC${sign}${Math.floor(absolute / 60)}${absolute % 60 ? `:${String(absolute % 60).padStart(2, '0')}` : ''}`;
  }

  function normalizeDailyWindows(settings) {
    const windows = Array.isArray(settings.dailyWindows) ? settings.dailyWindows : [];
    return windows
      .map((window) => ({
        startText: String(window?.start || '').trim(),
        endText: String(window?.end || '').trim(),
      }))
      .filter((window) => window.startText || window.endText)
      .map((window) => ({
        ...window,
        start: parseDailyTime(window.startText),
        end: parseDailyTime(window.endText),
      }));
  }

  function resolveSchedule(timestamp, settings = CONFIG) {
    if (settings.scheduleMode === 'daily') {
      const windows = normalizeDailyWindows(settings);
      const offset = Number(settings.utcOffsetMinutes);
      if (windows.length === 0) return { configured: false, valid: false };
      if (!windows.every((window) => window.start !== null && window.end !== null
        && window.start !== window.end)
        || !Number.isFinite(offset) || offset < -840 || offset > 840) {
        return { configured: true, valid: false };
      }

      const offsetMs = offset * 60 * 1000;
      const dayStart = Math.floor((timestamp + offsetMs) / DAY_MS) * DAY_MS - offsetMs;
      const occurrences = windows.map((window) => {
        let start = dayStart + window.start;
        let end = dayStart + window.end + (window.end <= window.start ? DAY_MS : 0);
        if (timestamp >= end) {
          start += DAY_MS;
          end += DAY_MS;
        } else if (timestamp < start) {
          const previousStart = start - DAY_MS;
          const previousEnd = end - DAY_MS;
          if (timestamp >= previousStart && timestamp < previousEnd) {
            start = previousStart;
            end = previousEnd;
          }
        }
        return { start, end };
      });

      const active = occurrences
        .filter((occurrence) => timestamp >= occurrence.start && timestamp < occurrence.end)
        .sort((a, b) => a.start - b.start)[0];
      const upcoming = active || occurrences
        .filter((occurrence) => occurrence.start > timestamp)
        .sort((a, b) => a.start - b.start)[0];

      return {
        configured: true,
        valid: true,
        start: upcoming.start,
        end: upcoming.end,
        description: `每天 ${windows.map((window) => `${window.startText}–${window.endText}`).join('、')}（${formatUtcOffset(offset)}）`,
      };
    }

    const configured = Boolean(settings.saleStart || settings.saleEnd);
    const start = settings.saleStart ? Date.parse(settings.saleStart) : NaN;
    const end = settings.saleEnd ? Date.parse(settings.saleEnd) : NaN;
    return {
      configured,
      valid: Number.isFinite(start) && Number.isFinite(end) && end > start,
      start,
      end,
      description: `${settings.saleStart} 至 ${settings.saleEnd}`,
    };
  }

  function hasOverlappingDailyWindows(settings) {
    const windows = normalizeDailyWindows(settings);
    if (windows.length < 2) return false;
    const intervals = [];
    windows.forEach((window) => {
      const length = window.end > window.start
        ? window.end - window.start
        : window.end + DAY_MS - window.start;
      intervals.push({ window, start: window.start, end: window.start + length });
      intervals.push({ window, start: window.start + DAY_MS, end: window.start + DAY_MS + length });
    });
    return intervals.some((a, index) => intervals.some((b, other) => index !== other
      && a.window !== b.window
      && a.start < b.end && b.start < a.end));
  }

  function isActiveWindow(schedule, timestamp) {
    return schedule.valid && timestamp >= schedule.start && timestamp < schedule.end;
  }

  function isPreheatWindow(schedule, timestamp) {
    if (!schedule.valid || timestamp >= schedule.start) return false;
    const remaining = schedule.start - timestamp;
    return remaining <= CONFIG.preheatBeforeMinutes * 60 * 1000 && remaining > 0;
  }

  function attemptLockKey(schedule) {
    return [
      'raksmart-auto-order:attempt',
      CONFIG.productId,
      CONFIG.sampleId,
      CONFIG.activityId,
      schedule.start,
    ].join(':');
  }

  function readAttemptLock(schedule = resolveSchedule(now())) {
    if (!schedule.valid) return null;
    const lock = store.get(attemptLockKey(schedule));
    return lock && Number.isFinite(lock.clickedAt) ? lock : null;
  }

  function saveAttemptLock(schedule, payload) {
    store.set(attemptLockKey(schedule), {
      clickedAt: Date.now(),
      occurrenceStart: schedule.start,
      ...payload,
    });
  }

  function resetAttemptLock() {
    const schedule = resolveSchedule(now());
    if (!readAttemptLock(schedule)) return;
    const confirmed = window.confirm(
      '仅在确认此前没有创建订单后继续。重置后，若仍在抢购窗口且价格匹配，脚本可能再次下单。确定重置吗？',
    );
    if (!confirmed) return;
    store.remove(attemptLockKey(schedule));
    hunt.result = null;
    hunt.attempts = 0;
    updateStatus('本场下单锁已重置，重新检查', 'waiting');
    scheduleTick();
  }

  /* ================= API 客户端 ================= */

  async function apiFetch(path, { method = 'POST', payload, timeoutMs = 15000 } = {}) {
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), timeoutMs);
    try {
      const response = await fetch(`${API_BASE}${path}`, {
        method,
        credentials: 'include',
        headers: {
          'Content-Type': 'application/json',
          'x-publishable-api-key': PUBLISHABLE_KEY,
        },
        body: payload === undefined ? undefined : JSON.stringify(payload),
        signal: controller.signal,
      });
      let body = null;
      try {
        body = await response.json();
      } catch (error) {
        body = null;
      }
      return { status: response.status, ok: response.ok, body };
    } finally {
      clearTimeout(timer);
    }
  }

  function cycleCode() {
    const text = String(CONFIG.expectedCycle || '');
    for (const [pattern, code] of CYCLE_MAP) {
      if (pattern.test(text)) return code;
    }
    return 'annually';
  }

  /* ---------- 登录态 ---------- */

  function ensureIdentity({ maxAgeMs = 60000, force = false } = {}) {
    if (identity.checking) return identity.checking;
    if (!force && identity.customer && Date.now() - identity.checkedAt < maxAgeMs) {
      return Promise.resolve(identity.customer);
    }
    identity.checking = apiFetch('/customers/me', { method: 'GET', timeoutMs: 10000 })
      .then(({ status, body }) => {
        identity.checking = null;
        identity.checkedAt = Date.now();
        if (status === 200 && body?.customer) {
          identity.customer = body.customer;
          identity.failCount = 0;
          return identity.customer;
        }
        identity.customer = null;
        identity.failCount += 1;
        return null;
      })
      .catch(() => {
        identity.checking = null;
        identity.failCount += 1;
        return null;
      });
    return identity.checking;
  }

  // 自动重登：medusa-login.php 是跨域表单 POST，浏览器 fetch 跟不了整条重定向链，
  // 用 GM_xmlhttpRequest 走浏览器网络栈（中间跳转的 Set-Cookie 会被正常种下）。
  function gmRequest(url, { method = 'GET', data, headers = {}, timeout = 30000 } = {}) {
    return new Promise((resolve, reject) => {
      GM_xmlhttpRequest({
        method,
        url,
        data,
        headers,
        timeout,
        onload: (response) => resolve(response),
        onerror: (error) => reject(new Error('网络错误')),
        ontimeout: () => reject(new Error('请求超时')),
      });
    });
  }

  async function tryAutoRelogin() {
    if (!CONFIG.loginEmail || !CONFIG.loginPassword) return false;
    try {
      await gmRequest(LOGIN_URL, {
        method: 'POST',
        headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
        data: `username=${encodeURIComponent(CONFIG.loginEmail)}&password=${encodeURIComponent(CONFIG.loginPassword)}`,
      });
      // 链路走完后验证会话
      const customer = await ensureIdentity({ force: true });
      return Boolean(customer);
    } catch (error) {
      console.warn(LOG_PREFIX, '自动重登失败', error);
      return false;
    }
  }

  /* ---------- 商品模板与下单参数 ---------- */

  function buildConfigOptionsFromTemplate(template) {
    const whmcs = template?.product?.metadata?.whmcs ?? {};
    const merged = { ...DEFAULT_CONFIGOPTIONS };
    for (const configoption of whmcs.configoptions?.configoption ?? []) {
      const key = String(configoption.id);
      if (merged[key] !== undefined) continue;
      const options = configoption.options?.option ?? [];
      if (!options.length) continue;
      if (String(configoption.type) === '4') {
        merged[key] = String(Number(options[0]?.qtyminimum ?? 0));
      } else {
        const chosen = options.find((option) => !Number(option.hidden)) ?? options[0];
        merged[key] = chosen.id;
      }
    }
    return merged;
  }

  function buildCustomFields(template) {
    const fields = {};
    for (const customfield of template?.product?.metadata?.whmcs?.customfields?.customfield ?? []) {
      if (customfield.name === 'private_network_enabled' && CONFIG.internalNetwork) {
        fields[String(customfield.id)] = 'on';
      }
    }
    return fields;
  }

  async function ensureProductTemplate({ force = false } = {}) {
    if (!force && product.template && product.productId === CONFIG.productId) return product;
    const { status, body } = await apiFetch(`/products/template/whmcs/${CONFIG.productId}`, {
      payload: { sample_id: CONFIG.sampleId, activity_id: CONFIG.activityId },
      timeoutMs: 30000,
    });
    if (status !== 200 || !body?.product) {
      throw new Error(`获取商品模板失败（HTTP ${status}）${body?.message ? `：${body.message}` : ''}`);
    }
    product.template = body;
    product.productId = CONFIG.productId;
    product.configOptions = buildConfigOptionsFromTemplate(body);
    product.customFields = buildCustomFields(body);
    return product;
  }

  function buildQuoteBody() {
    return {
      product_id: product.template.product.id,
      billingcycle: cycleCode(),
      activity_id: CONFIG.activityId,
      price_list_id: '',
      quantity: 1,
      language: 'chinese-cn',
      configoptions: product.configOptions,
      sample_id: CONFIG.sampleId,
    };
  }

  function randomRootPw() {
    const alphabet = 'abcdefghjkmnpqrstuvwxyzABCDEFGHJKMNPQRSTUVWXYZ23456789';
    let pw = '';
    for (let i = 0; i < 14; i += 1) pw += alphabet[Math.floor(Math.random() * alphabet.length)];
    return `${pw}!aA1`;
  }

  function buildOrderBody(customerId) {
    return {
      domain: `vps-${Math.random().toString(16).slice(2, 8)}`,
      rootpw: randomRootPw(),
      pid: String(CONFIG.productId),
      product_id: product.template.product.id,
      billingcycle: cycleCode(),
      quantity: 1,
      activity_id: CONFIG.activityId,
      price_list_id: '',
      configoptions_data: product.configOptions,
      customfields_data: product.customFields,
      sample_id: CONFIG.sampleId,
      aff_id: 0,
      aff_rtype: '',
      language: 'chinese-cn',
      group_id: product.template.product.metadata.whmcs.gid,
      notes: '',
      agree_terms: 1,
      customer_id: customerId,
    };
  }

  /* ---------- 报价 ---------- */

  function requestQuote() {
    if (quoteState.inFlight) return quoteState.inFlight;
    quoteState.inFlight = apiFetch('/custom/price/get-product-price', {
      payload: buildQuoteBody(),
      timeoutMs: 12000,
    })
      .then(({ status, body }) => {
        quoteState.inFlight = null;
        quoteState.lastAt = Date.now();
        if (status === 200 && body) {
          quoteState.price = Number(body.priceoverride ?? body.discounted_price ?? body.unit_price);
          quoteState.cycleText = normalize(body.cycle);
          const validate = body.activity_validate;
          quoteState.validateOk = validate ? Boolean(validate.status) : null;
          quoteState.validateMsg = validate?.status ? '' : (validate?.msg || '活动未开始或已结束');
          quoteState.discountIds = Array.isArray(body.use_discount_ids) ? body.use_discount_ids : [];
        } else if (status === 401) {
          identity.customer = null;
          quoteState.price = null;
          quoteState.validateOk = null;
        } else {
          quoteState.price = null;
        }
        return quoteState;
      })
      .catch(() => {
        quoteState.inFlight = null;
        return quoteState;
      });
    return quoteState.inFlight;
  }

  function quoteMatches() {
    if (!Number.isFinite(quoteState.price)) return false;
    if (Math.abs(quoteState.price - Number(CONFIG.expectedPrice)) > 0.001) return false;
    if (CONFIG.expectedCycle && quoteState.cycleText
      && !quoteState.cycleText.includes(normalize(CONFIG.expectedCycle))) return false;
    return true;
  }

  /* ---------- 下单与结账 ---------- */

  async function fireOrder(customerId) {
    const { status, body } = await apiFetch('/custom/cart/add-order', {
      payload: buildOrderBody(customerId),
      timeoutMs: CONFIG.orderTimeoutMs,
    });
    return { status, body };
  }

  async function checkoutCart(cartId, customerId) {
    return apiFetch('/custom/whmcs-cart/checkout-cart', {
      payload: {
        cart_id: cartId,
        customer_id: customerId,
        discount_ids: quoteState.discountIds ?? [],
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
    if (invoiceId) return `https://billing.raksmart.com/whmcs/viewinvoice.php?id=${invoiceId}&language=chinese-cn`;
    return null;
  }

  async function handleOrderSuccess(schedule, body, customerId) {
    const cartId = body.cart.id;
    hunt.result = { cartId, at: new Date().toISOString() };
    if (CONFIG.attemptLockEnabled) {
      saveAttemptLock(schedule, { cartId });
    }
    updateStatus(`下单成功（cart ${cartId}），正在结账…`, 'locked');

    let checkout = null;
    let lastError = '';
    // 结账超时 ≠ 失败：服务端可能已受理并在处理（重试会返回 is processing）。
    // 对超时/处理中/5xx 轮询确认，最多 6 次；业务性错误立即停止交人工。
    for (let attempt = 1; attempt <= 6 && !checkout; attempt += 1) {
      try {
        checkout = await checkoutCart(cartId, customerId);
      } catch (error) {
        checkout = { status: 0, body: null, error: error.message };
      }
      if (checkout.status === 200 || checkout.status === 202) break;
      lastError = checkout.error
        ?? checkout.body?.message
        ?? JSON.stringify(checkout.body)?.slice(0, 120)
        ?? `HTTP ${checkout.status}`;
      const processing = /is processing/i.test(lastError);
      const transient = processing || checkout.status === 0 || checkout.status >= 500;
      if (!transient) break;
      console.warn(LOG_PREFIX, `结账第 ${attempt} 次未确认（${lastError}），服务端可能仍在处理`);
      await new Promise((resolve) => setTimeout(resolve, processing ? 5000 : 8000));
      checkout = null;
    }

    if (!checkout) {
      hunt.result = { cartId, checkoutError: lastError, at: new Date().toISOString() };
      updateStatus(`⚠ 结账未确认（${lastError}）。超时的请求可能已生成订单，请立即到订单列表核对`, 'error');
      if (CONFIG.attemptLockEnabled) {
        saveAttemptLock(schedule, hunt.result);
      }
      window.open('https://billing.raksmart.com/whmcs/clientarea.php', '_blank');
      return;
    }
    if (checkout.status === 200 || checkout.status === 202) {
      const invoiceUrl = paymentUrlFromBody(checkout.body);
      const orderId = checkout.body?.whmcs_order?.data?.orderid ?? null;
      const invoiceId = checkout.body?.invoice_id ?? checkout.body?.whmcs_order?.data?.invoiceid ?? null;
      hunt.result = { cartId, orderId, invoiceId, invoiceUrl, at: new Date().toISOString() };
      updateStatus(`🎉 结账成功：订单 ${orderId ?? '—'}，账单 ${invoiceId ?? '—'}`, 'locked');
      console.log(LOG_PREFIX, '结账成功', hunt.result);
      if (CONFIG.attemptLockEnabled) {
        saveAttemptLock(schedule, hunt.result);
      }
      if (invoiceUrl) window.open(invoiceUrl, '_blank');
    } else {
      hunt.result = { cartId, checkoutStatus: checkout.status, at: new Date().toISOString() };
      updateStatus(`⚠ cart 已创建但结账返回 HTTP ${checkout.status}，请手动完成结账`, 'error');
      if (CONFIG.attemptLockEnabled) {
        saveAttemptLock(schedule, hunt.result);
      }
    }
    scheduleTick();
  }

  /* ================= 设置表单 ================= */

  function appendField(form, labelText, name, type, value, attributes = {}) {
    const label = document.createElement('label');
    label.className = 'field';
    const caption = document.createElement('span');
    caption.textContent = labelText;
    const input = document.createElement(type === 'select' ? 'select' : 'input');
    input.name = name;
    if (type !== 'select') input.type = type;
    if (type === 'password') input.setAttribute('autocomplete', 'new-password');
    Object.entries(attributes).forEach(([key, attributeValue]) => {
      input.setAttribute(key, String(attributeValue));
    });
    if (type === 'select') {
      [['once', '单次抢购'], ['daily', '每日循环']].forEach(([optionValue, text]) => {
        const option = document.createElement('option');
        option.value = optionValue;
        option.textContent = text;
        input.appendChild(option);
      });
    }
    input.value = String(value ?? '');
    label.append(caption, input);
    form.appendChild(label);
    return input;
  }

  function appendCheckboxField(form, labelText, name) {
    const label = document.createElement('label');
    label.className = 'field checkbox-field';
    const input = document.createElement('input');
    input.type = 'checkbox';
    input.name = name;
    const caption = document.createElement('span');
    caption.textContent = labelText;
    label.append(input, caption);
    form.appendChild(label);
    return input;
  }

  function createDailyWindowsField(form) {
    const field = document.createElement('div');
    field.className = 'field';
    field.dataset.schedule = 'daily';
    const caption = document.createElement('span');
    caption.textContent = '每日抢购时段（可配置多个）';
    const rows = document.createElement('div');
    rows.className = 'window-rows';

    const addRow = () => {
      const row = document.createElement('div');
      row.className = 'window-row';
      const start = document.createElement('input');
      start.type = 'time';
      start.name = 'dailyWindowStart';
      start.setAttribute('aria-label', '时段开始时间');
      const end = document.createElement('input');
      end.type = 'time';
      end.name = 'dailyWindowEnd';
      end.setAttribute('aria-label', '时段结束时间');
      const remove = document.createElement('button');
      remove.type = 'button';
      remove.className = 'window-remove';
      remove.textContent = '删除';
      remove.setAttribute('aria-label', '删除此时段');
      remove.addEventListener('click', () => row.remove());
      row.append(start, end, remove);
      rows.appendChild(row);
      return row;
    };

    const addButton = document.createElement('button');
    addButton.type = 'button';
    addButton.className = 'window-add';
    addButton.textContent = '添加时段';
    addButton.addEventListener('click', addRow);

    field.append(caption, rows, addButton);
    form.appendChild(field);
    return { field, rows, addRow };
  }

  function fillDailyWindowRows() {
    if (!panel) return;
    panel.dailyWindowRows.innerHTML = '';
    const windows = Array.isArray(CONFIG.dailyWindows) && CONFIG.dailyWindows.length
      ? CONFIG.dailyWindows
      : [{}];
    windows.forEach((window) => {
      const row = panel.addDailyWindowRow();
      row.querySelector('[name="dailyWindowStart"]').value = String(window?.start || '');
      row.querySelector('[name="dailyWindowEnd"]').value = String(window?.end || '');
    });
  }

  function fillSettingsForm() {
    if (!panel) return;
    const { settingsForm } = panel;
    Object.entries({
      expectedPrice: CONFIG.expectedPrice,
      expectedCycle: CONFIG.expectedCycle,
      scheduleMode: CONFIG.scheduleMode,
      saleStart: CONFIG.saleStart,
      saleEnd: CONFIG.saleEnd,
      utcOffsetMinutes: CONFIG.utcOffsetMinutes,
      preheatBeforeMinutes: CONFIG.preheatBeforeMinutes,
      orderIntervalSeconds: CONFIG.orderIntervalMs / 1000,
      loginEmail: CONFIG.loginEmail,
      loginPassword: '',
      attemptLockEnabled: CONFIG.attemptLockEnabled,
      productId: CONFIG.productId,
      sampleId: CONFIG.sampleId,
      activityId: CONFIG.activityId,
    }).forEach(([name, value]) => {
      const control = settingsForm.elements.namedItem(name);
      if (!control) return;
      if (control.type === 'checkbox') control.checked = Boolean(value);
      else control.value = String(value ?? '');
    });
    settingsForm.elements.namedItem('internalNetwork').checked = Boolean(CONFIG.internalNetwork);
    panel.settingsError.textContent = '';
    fillDailyWindowRows();
    updateScheduleFields();
  }

  function updateScheduleFields() {
    if (!panel) return;
    const daily = panel.settingsForm.elements.namedItem('scheduleMode').value === 'daily';
    panel.settingsForm.querySelectorAll('[data-schedule]').forEach((field) => {
      field.hidden = field.dataset.schedule !== (daily ? 'daily' : 'once');
    });
  }

  function openSettings() {
    createPanel();
    if (!panel) return;
    fillSettingsForm();
    panel.settingsForm.hidden = false;
    panel.settingsButton.setAttribute('aria-expanded', 'true');
    panel.settingsForm.querySelector('input, select')?.focus();
  }

  function closeSettings() {
    if (!panel) return;
    panel.settingsForm.hidden = true;
    panel.settingsButton.setAttribute('aria-expanded', 'false');
    panel.settingsButton.focus();
  }

  function saveSettings(event) {
    event.preventDefault();
    const form = event.currentTarget;
    if (!form.reportValidity()) return;
    const data = new FormData(form);
    const dailyWindows = Array.from(form.querySelectorAll('.window-row'))
      .map((row) => ({
        start: String(row.querySelector('[name="dailyWindowStart"]').value).trim(),
        end: String(row.querySelector('[name="dailyWindowEnd"]').value).trim(),
      }))
      .filter((window) => window.start || window.end);
    const next = {
      ...CONFIG,
      expectedPrice: Number(data.get('expectedPrice')),
      expectedCycle: String(data.get('expectedCycle')).trim(),
      scheduleMode: String(data.get('scheduleMode')),
      saleStart: String(data.get('saleStart')).trim(),
      saleEnd: String(data.get('saleEnd')).trim(),
      dailyWindows,
      utcOffsetMinutes: Number(data.get('utcOffsetMinutes')),
      preheatBeforeMinutes: Number(data.get('preheatBeforeMinutes')),
      orderIntervalMs: Number(data.get('orderIntervalSeconds')) * 1000,
      internalNetwork: form.elements.namedItem('internalNetwork').checked,
      loginEmail: String(data.get('loginEmail')).trim(),
      // 密码留空表示不修改已存值
      ...(String(data.get('loginPassword')) ? { loginPassword: String(data.get('loginPassword')) } : {}),
      attemptLockEnabled: form.elements.namedItem('attemptLockEnabled').checked,
      productId: String(data.get('productId')).trim(),
      sampleId: String(data.get('sampleId')).trim(),
      activityId: String(data.get('activityId')).trim(),
    };
    const schedule = resolveSchedule(Date.now(), next);
    if (schedule.configured && !schedule.valid) {
      panel.settingsError.textContent = next.scheduleMode === 'daily'
        ? '每个每日时段都需填写有效的 HH:mm 开始与结束时间，且开始与结束不能相同。'
        : '单次时间需为带时区的 ISO 8601 格式，且结束时间晚于开始时间。';
      return;
    }
    if (next.scheduleMode === 'daily' && hasOverlappingDailyWindows(next)) {
      panel.settingsError.textContent = '每日各时段不能重叠（含跨天时段延伸到次日的部分）。';
      return;
    }
    store.set(SETTINGS_KEY, next);
    location.reload();
  }

  /* ================= 状态面板 ================= */

  function createPanel() {
    if (!document.body || panel?.host.isConnected) return;

    const host = document.createElement('div');
    host.id = 'raksmart-auto-order-panel';
    const shadow = host.attachShadow({ mode: 'open' });
    const style = document.createElement('style');
    style.textContent = `
      * { box-sizing: border-box; }
      [hidden] { display: none !important; }
      .panel {
        position: fixed; right: 12px; bottom: 12px; z-index: 2147483647;
        width: min(410px, calc(100vw - 24px)); max-height: calc(100vh - 24px);
        overflow-y: auto; padding: 14px; color: #e5e7eb;
        background: rgba(17, 24, 39, .97); border: 1px solid #4b5563;
        border-radius: 10px; box-shadow: 0 8px 30px rgba(0, 0, 0, .3);
        font: 13px/1.5 -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
      }
      .title, .settings-title { margin-bottom: 6px; color: #fff; font-size: 15px; font-weight: 650; }
      .status { margin-bottom: 6px; }
      .status[data-state="ready"] { color: #86efac; }
      .status[data-state="error"], .settings-error { color: #fca5a5; }
      .status[data-state="waiting"] { color: #fcd34d; }
      .status[data-state="locked"] { color: #93c5fd; }
      .detail { color: #aeb7c4; overflow-wrap: anywhere; }
      .link { color: #93c5fd; text-decoration: underline; cursor: pointer; }
      .actions { display: flex; flex-wrap: wrap; gap: 8px; margin-top: 10px; }
      button {
        flex: 1; min-height: 48px; padding: 8px 10px; color: #e5e7eb;
        background: #374151; border: 1px solid #6b7280; border-radius: 6px;
        font: inherit; cursor: pointer;
      }
      button:hover { background: #4b5563; }
      button:focus-visible, input:focus-visible, select:focus-visible {
        outline: 3px solid #93c5fd; outline-offset: 2px;
      }
      .settings-form { margin-top: 14px; padding-top: 12px; border-top: 1px solid #4b5563; }
      .field { display: block; margin-bottom: 10px; }
      .field > span { display: block; margin-bottom: 3px; color: #d1d5db; }
      .checkbox-field { display: flex; align-items: center; gap: 10px; min-height: 48px; }
      .checkbox-field input { width: 24px; min-height: 24px; flex: 0 0 auto; margin: 0; }
      .checkbox-field > span { margin: 0; }
      input, select {
        width: 100%; min-height: 48px; padding: 8px 10px; color: #f3f4f6;
        background: #111827; border: 1px solid #6b7280; border-radius: 6px;
        font: inherit; font-size: max(1rem, 1em);
      }
      .hint { margin: -4px 0 10px; color: #9ca3af; }
      .window-rows { display: grid; gap: 8px; margin-bottom: 8px; }
      .window-row { display: flex; gap: 8px; }
      .window-row input { flex: 1; min-width: 0; }
      button.window-remove { flex: 0 0 auto; width: 64px; }
      button.window-add { width: 100%; }
      .settings-error { min-height: 20px; }
    `;

    const root = document.createElement('div');
    root.className = 'panel';
    const title = document.createElement('div');
    title.className = 'title';
    title.textContent = 'Raksmart 自动下单（API 版）';
    const status = document.createElement('div');
    status.className = 'status';
    status.setAttribute('role', 'status');
    status.setAttribute('aria-live', 'polite');
    const detail = document.createElement('div');
    detail.className = 'detail';
    const actions = document.createElement('div');
    actions.className = 'actions';
    const pauseButton = document.createElement('button');
    pauseButton.type = 'button';
    pauseButton.textContent = '暂停抢购';
    const settingsButton = document.createElement('button');
    settingsButton.type = 'button';
    settingsButton.textContent = '设置';
    settingsButton.setAttribute('aria-expanded', 'false');
    settingsButton.setAttribute('aria-controls', 'raksmart-settings-form');
    const loginButton = document.createElement('button');
    loginButton.type = 'button';
    loginButton.textContent = '打开登录页';
    const resetButton = document.createElement('button');
    resetButton.type = 'button';
    resetButton.textContent = '重置下单锁';
    resetButton.hidden = true;
    actions.append(pauseButton, settingsButton, loginButton, resetButton);

    const settingsForm = document.createElement('form');
    settingsForm.id = 'raksmart-settings-form';
    settingsForm.className = 'settings-form';
    settingsForm.hidden = true;
    settingsForm.setAttribute('aria-labelledby', 'raksmart-settings-title');
    const settingsTitle = document.createElement('div');
    settingsTitle.id = 'raksmart-settings-title';
    settingsTitle.className = 'settings-title';
    settingsTitle.textContent = '自动下单设置（保存后刷新）';
    settingsForm.appendChild(settingsTitle);
    appendField(settingsForm, '期望价格（USD）', 'expectedPrice', 'number', '', { required: true, min: 0, step: '0.01' });
    appendField(settingsForm, '付款周期（如 每年，留空则不校验）', 'expectedCycle', 'text', '');
    appendCheckboxField(settingsForm, '勾选「内网」', 'internalNetwork');
    const scheduleMode = appendField(settingsForm, '场次类型', 'scheduleMode', 'select', 'once');
    const onceStart = appendField(settingsForm, '单次开始时间（含时区）', 'saleStart', 'text', '', { placeholder: '2026-11-15T20:00:00+08:00' });
    onceStart.dataset.schedule = 'once';
    const onceEnd = appendField(settingsForm, '单次结束时间（含时区）', 'saleEnd', 'text', '', { placeholder: '2026-11-15T20:10:00+08:00' });
    onceEnd.dataset.schedule = 'once';
    const dailyWindowsField = createDailyWindowsField(settingsForm);
    appendField(settingsForm, '每日场次的 UTC 偏移（分钟）', 'utcOffsetMinutes', 'number', '', { required: true, min: -840, max: 840, step: 1 });
    appendField(settingsForm, '提前多少分钟开始预热', 'preheatBeforeMinutes', 'number', '', { required: true, min: 0, max: 10080, step: 1 });
    appendField(settingsForm, '抢购下单重试间隔（秒）', 'orderIntervalSeconds', 'number', '', { required: true, min: 0.2, max: 60, step: '0.1' });
    appendField(settingsForm, '自动重登邮箱（可选）', 'loginEmail', 'email', '', { placeholder: '留空则会话失效时提示手动登录' });
    appendField(settingsForm, '自动重登密码（可选，留空不修改）', 'loginPassword', 'password', '', { autocomplete: 'new-password' });
    appendCheckboxField(settingsForm, '启用防重复下单锁（关闭后可能重复下单）', 'attemptLockEnabled');
    appendField(settingsForm, '商品 pid', 'productId', 'text', '');
    appendField(settingsForm, 'sample_id', 'sampleId', 'text', '');
    appendField(settingsForm, 'activity_id', 'activityId', 'text', '');
    const hint = document.createElement('div');
    hint.className = 'hint';
    hint.textContent = 'API 模式不依赖页面表单。换商品时需同步更新脚本内 DEFAULT_CONFIGOPTIONS（详见脚本头部注释）。';
    settingsForm.appendChild(hint);
    const settingsError = document.createElement('div');
    settingsError.className = 'settings-error';
    settingsError.setAttribute('role', 'alert');
    settingsForm.appendChild(settingsError);
    const settingsActions = document.createElement('div');
    settingsActions.className = 'actions';
    const saveButton = document.createElement('button');
    saveButton.type = 'submit';
    saveButton.textContent = '保存设置';
    const cancelButton = document.createElement('button');
    cancelButton.type = 'button';
    cancelButton.textContent = '取消';
    settingsActions.append(saveButton, cancelButton);
    settingsForm.appendChild(settingsActions);

    pauseButton.addEventListener('click', () => {
      paused = !paused;
      scheduleTick();
    });
    settingsButton.addEventListener('click', () => {
      if (settingsForm.hidden) openSettings();
      else closeSettings();
    });
    loginButton.addEventListener('click', () => {
      window.open('https://billing.raksmart.com/', '_blank');
    });
    resetButton.addEventListener('click', resetAttemptLock);
    scheduleMode.addEventListener('change', updateScheduleFields);
    cancelButton.addEventListener('click', closeSettings);
    settingsForm.addEventListener('submit', saveSettings);
    root.append(title, status, detail, actions, settingsForm);
    shadow.append(style, root);
    document.body.appendChild(host);
    panel = {
      host, status, detail, pauseButton, settingsButton, resetButton, loginButton,
      settingsForm, settingsError,
      dailyWindowRows: dailyWindowsField.rows,
      addDailyWindowRow: dailyWindowsField.addRow,
    };
  }

  function updateStatus(message, state = 'waiting') {
    createPanel();
    if (!panel) return;
    if (panel.status.textContent !== message) panel.status.textContent = message;
    panel.status.dataset.state = state;
    panel.pauseButton.textContent = paused ? '继续抢购' : '暂停抢购';
    panel.resetButton.hidden = !readAttemptLock();

    const loginText = identity.customer
      ? `已登录：${identity.customer.email}`
      : identity.failCount > 0 ? '未登录（点击「打开登录页」，登录后回到本页）' : '登录态检测中…';
    const priceText = Number.isFinite(quoteState.price)
      ? `$${quoteState.price.toFixed(2)} / ${quoteState.cycleText || '周期未知'}`
      : '报价未知';
    const schedule = resolveSchedule(now());
    const saleText = schedule.valid ? schedule.description : '抢购时间未配置或无效';

    panel.detail.textContent = `${loginText}｜当前报价 ${priceText}；期望 $${Number(CONFIG.expectedPrice).toFixed(2)} / ${CONFIG.expectedCycle}；${saleText}`;
    if (hunt.result?.invoiceUrl) {
      const link = document.createElement('a');
      link.className = 'link';
      link.href = hunt.result.invoiceUrl;
      link.target = '_blank';
      link.rel = 'noopener';
      link.textContent = `✅ 已下单，打开付款页（账单 ${hunt.result.invoiceId ?? '—'}）`;
      panel.detail.appendChild(document.createElement('br'));
      panel.detail.appendChild(link);
    } else if (hunt.result?.cartId) {
      panel.detail.appendChild(document.createTextNode(`｜cart ${hunt.result.cartId} 待结账`));
    }
    panel.loginButton.hidden = Boolean(identity.customer);
  }

  function formatCountdown(milliseconds) {
    const totalSeconds = Math.max(0, Math.ceil(milliseconds / 1000));
    const hours = Math.floor(totalSeconds / 3600);
    const minutes = Math.floor((totalSeconds % 3600) / 60);
    const seconds = totalSeconds % 60;
    return hours > 0 ? `${hours}时${minutes}分${seconds}秒` : `${minutes}分${seconds}秒`;
  }

  /* ================= 主循环（API 驱动） ================= */

  async function tick() {
    try {
      createPanel();
      if (!panel) return;

      await ensureIdentity();

      const schedule = resolveSchedule(now());
      if (!schedule.configured) {
        updateStatus('请点击右下角「设置」配置单次或每日抢购时间', 'waiting');
        return;
      }
      if (!schedule.valid) {
        updateStatus('抢购时间配置无效，请打开设置修正', 'error');
        return;
      }

      // 已有结果：展示并退出
      if (hunt.result) {
        updateStatus(hunt.result.invoiceUrl
          ? '🎉 已完成下单与结账，请在打开的账单页付款'
          : `cart ${hunt.result.cartId} 已创建，请手动完成结账`, 'locked');
        return;
      }
      if (CONFIG.attemptLockEnabled && readAttemptLock(schedule)) {
        updateStatus('本场已下单（检测到下单锁），已停止。可在账单列表确认结果', 'locked');
        return;
      }
      if (paused) {
        updateStatus('已暂停；恢复后将按场次继续', 'waiting');
        return;
      }
      if (CONFIG.scheduleMode !== 'daily' && now() >= schedule.end) {
        updateStatus('单次抢购窗口已结束，自动下单已停止', 'locked');
        return;
      }

      // 商品模板（含下单用配置项映射），失败会在下轮重试
      if (!product.template) {
        try {
          await ensureProductTemplate();
        } catch (error) {
          updateStatus(`商品模板加载失败：${error.message}，将重试`, 'error');
          return;
        }
      }

      const active = isActiveWindow(schedule, now());
      const preheating = isPreheatWindow(schedule, now());

      // 报价巡检：场次外低频、预热高频、抢购窗口内用下单间隔（开抢瞬间尽快发现价格翻转）
      const quoteInterval = active
        ? Math.max(500, CONFIG.orderIntervalMs)
        : preheating
          ? CONFIG.preheatQuoteIntervalMs
          : CONFIG.idleQuoteIntervalMs;
      if (!quoteState.inFlight && Date.now() - quoteState.lastAt > quoteInterval) {
        requestQuote();
      }

      if (!active) {
        const countdown = formatCountdown(schedule.start - now());
        const validateText = quoteState.validateOk === false
          ? `；活动暂未生效（${quoteState.validateMsg}），开抢后直接下单`
          : '';
        const priceWarning = Number.isFinite(quoteState.price) && !quoteMatches()
          ? '；当前报价与期望不同，开抢后仍会复核再下单'
          : '';
        updateStatus(`${preheating ? '预热中' : '待命'}：距开抢 ${countdown}${validateText}${priceWarning}`, preheating ? 'ready' : 'waiting');
        return;
      }

      // 抢购窗口内
      if (!identity.customer) {
        const relogged = await tryAutoRelogin();
        if (!relogged) {
          updateStatus('抢购窗口内但登录态失效！请立即登录后回到本页', 'error');
          return;
        }
      }

      if (!quoteMatches()) {
        const reason = !Number.isFinite(quoteState.price)
          ? '报价未知'
          : quoteState.validateOk === false
            ? `活动未生效：${quoteState.validateMsg}`
            : `价格不符（$${quoteState.price.toFixed(2)}）`;
        updateStatus(`抢购中：${reason}，继续刷新报价`, 'error');
        if (!quoteState.inFlight) requestQuote();
        return;
      }

      // 价格与周期匹配 → 开火（单发在途，避免重复创建订单）
      const elapsed = Date.now() - hunt.lastOrderAt;
      if (hunt.inFlightOrder || elapsed < CONFIG.orderIntervalMs) {
        updateStatus(`价格匹配（$${quoteState.price.toFixed(2)}），${hunt.inFlightOrder ? '下单请求在途' : `${Math.ceil((CONFIG.orderIntervalMs - elapsed) / 1000) || '0.1'} 秒后下单`}`, 'ready');
        return;
      }

      hunt.inFlightOrder = true;
      hunt.lastOrderAt = Date.now();
      hunt.attempts += 1;
      const attemptNo = hunt.attempts;
      updateStatus(`价格匹配，提交订单（第 ${attemptNo} 次）`, 'ready');

      try {
        const { status, body } = await fireOrder(identity.customer.id);
        if (status === 200 && body?.cart?.id) {
          await handleOrderSuccess(schedule, body, identity.customer.id);
          return;
        }
        if (status === 401) {
          identity.customer = null;
          const relogged = await tryAutoRelogin();
          hunt.lastError = relogged ? '会话过期，已自动重登，重试下单' : '会话过期，请手动登录';
        } else {
          const message = body?.message || body?.error || `HTTP ${status}`;
          hunt.lastError = String(message);
          if (!/库存不足|售罄|sold out|out of stock/i.test(hunt.lastError)
            && attemptNo <= 3) {
            console.warn(LOG_PREFIX, '下单返回异常', { status, body });
          }
        }
      } catch (error) {
        hunt.lastError = `网络异常：${error.message}（该请求可能已到达服务端，请稍后在账单列表确认）`;
        console.warn(LOG_PREFIX, hunt.lastError);
      } finally {
        hunt.inFlightOrder = false;
      }

      const outOfStock = /库存不足|售罄|sold out|out of stock/i.test(hunt.lastError);
      updateStatus(outOfStock
        ? `抢购中：库存不足（已尝试 ${attemptNo} 次），继续重试`
        : `抢购中：${hunt.lastError}，继续重试`, outOfStock ? 'ready' : 'error');
    } catch (error) {
      console.error(LOG_PREFIX, '执行异常', error);
      updateStatus(`脚本执行异常：${error.message}`, 'error');
    }
  }

  function scheduleTick() {
    if (scheduled) return;
    scheduled = true;
    setTimeout(async () => {
      scheduled = false;
      await tick();
    }, 50);
  }

  if (typeof GM_registerMenuCommand === 'function') {
    GM_registerMenuCommand('打开自动下单设置', openSettings);
    GM_registerMenuCommand('重置本场自动下单锁', resetAttemptLock);
    GM_registerMenuCommand('暂停 / 继续抢购', () => {
      paused = !paused;
      scheduleTick();
    });
    GM_registerMenuCommand('恢复默认设置（保留下单锁）', () => {
      if (!window.confirm('确定清除已保存的自动下单设置并恢复默认值吗？')) return;
      store.remove(SETTINGS_KEY);
      location.reload();
    });
  }

  document.addEventListener('DOMContentLoaded', scheduleTick, { once: true });
  setInterval(scheduleTick, 500);
  scheduleTick();
})();
