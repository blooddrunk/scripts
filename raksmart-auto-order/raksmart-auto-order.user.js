// ==UserScript==
// @name         Raksmart VPS 自动下单助手
// @namespace    https://github.com/jelinenaro/tampermonkey
// @version      1.2.0
// @description  自动勾选内网和服务条款，按单次或每日场次校验价格并购买；支持开抢前预修复、设置持久化和防重复下单。
// @author       jelinenaro
// @match        https://billing.raksmart.com/ec/price*
// @run-at       document-start
// @grant        GM_getValue
// @grant        GM_setValue
// @grant        GM_deleteValue
// @grant        GM_registerMenuCommand
// @license      MIT
// ==/UserScript==

(function () {
  'use strict';

  if (window.top !== window.self) return;

  /* ================= 默认配置 ================= */

  // 仅首次运行或恢复默认设置时使用。之后请在页面右下角面板中修改，更新脚本不会覆盖。
  const DEFAULT_CONFIG = {
    productId: '1889',
    sampleId: 'psam_01M24N3CFJDQ6AX41HVYFT5GCF',
    activityId: 'act_01M4TBMTG2VMWJPXXCYX2WZS2S',

    autoCheckInternalNetwork: true,
    autoAcceptTerms: true,
    expectedPrice: 49.90,
    expectedCycle: '每年',

    // once：单次，使用带时区的 ISO 8601 时间；daily：每日，使用 HH:mm。
    scheduleMode: 'once',
    saleStart: '',
    saleEnd: '',
    dailyStart: '20:00',
    dailyEnd: '20:10',

    // 每日场次使用固定 UTC 偏移，默认 UTC+8，不受电脑时区影响。
    utcOffsetMinutes: 480,
    clockOffsetMs: 0,

    preheatBeforeMinutes: 60,
    quoteStableMs: 800,
    reloadAfterMs: 12000,
    errorReloadAfterMs: 3000,
    attemptLockEnabled: true,
    retryIntervalMs: 5000,
  };

  const SETTINGS_KEY = 'raksmart-auto-order:settings';
  const LOG_PREFIX = '[Raksmart 自动下单]';
  const DAY_MS = 24 * 60 * 60 * 1000;
  const PREHEAT_RELOAD_CUTOFF_MS = 5000;
  const PAGE_STARTED_AT = Date.now();

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
  if (!savedSettings) store.set(SETTINGS_KEY, CONFIG);

  /* ================= 状态与场次计算 ================= */

  let paused = false;
  let purchaseClickedFor = null;
  let lastPurchaseAttemptAt = 0;
  let reloadScheduled = false;
  let quoteSignature = '';
  let quoteStableSince = 0;
  let panel = null;
  let scheduled = false;

  const lastCheckboxClick = new WeakMap();

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

  function resolveSchedule(timestamp, settings = CONFIG) {
    if (settings.scheduleMode === 'daily') {
      const configured = Boolean(settings.dailyStart || settings.dailyEnd);
      const startTime = parseDailyTime(settings.dailyStart);
      const endTime = parseDailyTime(settings.dailyEnd);
      const offset = Number(settings.utcOffsetMinutes);
      if (!configured) return { configured: false, valid: false };
      if (startTime === null || endTime === null || startTime === endTime
        || !Number.isFinite(offset) || offset < -840 || offset > 840) {
        return { configured: true, valid: false };
      }

      const offsetMs = offset * 60 * 1000;
      const dayStart = Math.floor((timestamp + offsetMs) / DAY_MS) * DAY_MS - offsetMs;
      let start = dayStart + startTime;
      let end = dayStart + endTime + (endTime <= startTime ? DAY_MS : 0);

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

      return {
        configured: true,
        valid: true,
        start,
        end,
        description: `每天 ${settings.dailyStart}–${settings.dailyEnd}（${formatUtcOffset(offset)}）`,
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

  function isActiveWindow(schedule, timestamp) {
    return schedule.valid && timestamp >= schedule.start && timestamp < schedule.end;
  }

  function isPreheatWindow(schedule, timestamp) {
    if (!schedule.valid || timestamp >= schedule.start) return false;
    const remaining = schedule.start - timestamp;
    return remaining <= CONFIG.preheatBeforeMinutes * 60 * 1000
      && remaining > PREHEAT_RELOAD_CUTOFF_MS;
  }

  function canAutoRepair(schedule, timestamp) {
    return isActiveWindow(schedule, timestamp) || isPreheatWindow(schedule, timestamp);
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

  function saveAttemptLock(price, schedule) {
    const lock = {
      clickedAt: Date.now(),
      occurrenceStart: schedule.start,
      price,
      url: location.href,
    };
    store.set(attemptLockKey(schedule), lock);
    purchaseClickedFor = schedule.start;
  }

  function resetAttemptLock() {
    const schedule = resolveSchedule(now());
    if (!readAttemptLock(schedule)) return;
    const confirmed = window.confirm(
      '仅在确认此前没有创建订单后继续。重置后，若仍在抢购窗口且价格匹配，脚本可能再次点击立即购买。确定重置吗？',
    );
    if (!confirmed) return;
    store.remove(attemptLockKey(schedule));
    purchaseClickedFor = null;
    quoteStableSince = 0;
    updateStatus('本场下单锁已重置，重新检查页面', 'waiting');
    scheduleTick();
  }

  /* ================= 页面元素 ================= */

  function productMatches() {
    const params = new URLSearchParams(location.search);
    return (!CONFIG.productId || params.get('pid') === CONFIG.productId)
      && (!CONFIG.sampleId || params.get('sample_id') === CONFIG.sampleId)
      && (!CONFIG.activityId || params.get('activity_id') === CONFIG.activityId);
  }

  function findInternalNetworkCheckbox() {
    const label = Array.from(document.querySelectorAll('label.ant-checkbox-wrapper'))
      .find((item) => normalize(item.textContent) === '内网');
    return label?.querySelector('input[type="checkbox"]') || null;
  }

  function findTermsCheckbox() {
    const direct = document.querySelector('.price-order-terms input[type="checkbox"]');
    if (direct) return direct;
    const label = Array.from(document.querySelectorAll('label.ant-checkbox-wrapper'))
      .find((item) => {
        const text = normalize(item.textContent);
        return text.includes('我已阅读并同意') && text.includes('服务条款');
      });
    return label?.querySelector('input[type="checkbox"]') || null;
  }

  function findBuyButton() {
    return Array.from(document.querySelectorAll('button.price-order-btn-buy, button'))
      .find((button) => /^(立即购买|立刻购买)$/.test(normalize(button.textContent))) || null;
  }

  function readPrice() {
    const element = document.querySelector('.price-order-summary-price-value');
    if (!element) return null;
    const match = element.textContent.replace(/,/g, '').match(/-?\d+(?:\.\d+)?/);
    if (!match) return null;
    const value = Number(match[0]);
    return Number.isFinite(value) ? value : null;
  }

  function readCycle() {
    return normalize(document.querySelector('.price-order-summary-price-cycle')?.textContent);
  }

  function isBuyButtonDisabled(button) {
    return !button
      || button.disabled
      || button.getAttribute('aria-disabled') === 'true'
      || button.classList.contains('ant-btn-loading')
      || Boolean(button.querySelector('.ant-btn-loading-icon'));
  }

  function ensureChecked(input, enabled) {
    if (!input || input.checked || !enabled || input.disabled) return Boolean(input?.checked);
    const current = Date.now();
    const previous = lastCheckboxClick.get(input) || 0;
    if (current - previous < 500) return false;
    lastCheckboxClick.set(input, current);
    input.click();
    return input.checked;
  }

  function hasOutOfStock() {
    const text = document.body?.innerText || '';
    return /目前该项目缺货|订购已被暂停|暂时缺货|已售罄|out of stock|sold out/i.test(text);
  }

  function hasLoadingError() {
    const text = document.body?.innerText || '';
    return hasOutOfStock()
      || /加载失败|请求失败|网络异常|网络错误|系统繁忙|服务不可用|Network Error|Bad Gateway|Gateway Timeout/i.test(text);
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

  function fillSettingsForm() {
    if (!panel) return;
    const { settingsForm } = panel;
    Object.entries({
      expectedPrice: CONFIG.expectedPrice,
      expectedCycle: CONFIG.expectedCycle,
      scheduleMode: CONFIG.scheduleMode,
      saleStart: CONFIG.saleStart,
      saleEnd: CONFIG.saleEnd,
      dailyStart: CONFIG.dailyStart,
      dailyEnd: CONFIG.dailyEnd,
      utcOffsetMinutes: CONFIG.utcOffsetMinutes,
      preheatBeforeMinutes: CONFIG.preheatBeforeMinutes,
      reloadAfterSeconds: CONFIG.reloadAfterMs / 1000,
      errorReloadAfterSeconds: CONFIG.errorReloadAfterMs / 1000,
      attemptLockEnabled: CONFIG.attemptLockEnabled,
      retryIntervalSeconds: CONFIG.retryIntervalMs / 1000,
      productId: CONFIG.productId,
      sampleId: CONFIG.sampleId,
      activityId: CONFIG.activityId,
    }).forEach(([name, value]) => {
      const control = settingsForm.elements.namedItem(name);
      if (!control) return;
      if (control.type === 'checkbox') control.checked = Boolean(value);
      else control.value = String(value ?? '');
    });
    panel.settingsError.textContent = '';
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
    const next = {
      ...CONFIG,
      expectedPrice: Number(data.get('expectedPrice')),
      expectedCycle: String(data.get('expectedCycle')).trim(),
      scheduleMode: String(data.get('scheduleMode')),
      saleStart: String(data.get('saleStart')).trim(),
      saleEnd: String(data.get('saleEnd')).trim(),
      dailyStart: String(data.get('dailyStart')),
      dailyEnd: String(data.get('dailyEnd')),
      utcOffsetMinutes: Number(data.get('utcOffsetMinutes')),
      preheatBeforeMinutes: Number(data.get('preheatBeforeMinutes')),
      reloadAfterMs: Number(data.get('reloadAfterSeconds')) * 1000,
      errorReloadAfterMs: Number(data.get('errorReloadAfterSeconds')) * 1000,
      attemptLockEnabled: form.elements.namedItem('attemptLockEnabled').checked,
      retryIntervalMs: Number(data.get('retryIntervalSeconds')) * 1000,
      productId: String(data.get('productId')).trim(),
      sampleId: String(data.get('sampleId')).trim(),
      activityId: String(data.get('activityId')).trim(),
    };
    const schedule = resolveSchedule(Date.now(), next);
    if (schedule.configured && !schedule.valid) {
      panel.settingsError.textContent = next.scheduleMode === 'daily'
        ? '每日时间需为有效的 HH:mm，且开始与结束时间不能相同。'
        : '单次时间需为带时区的 ISO 8601 格式，且结束时间晚于开始时间。';
      return;
    }
    store.set(SETTINGS_KEY, next);
    panel.settingsForm.querySelector('[type="submit"]').disabled = true;
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
        width: min(390px, calc(100vw - 24px)); max-height: calc(100vh - 24px);
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
      .settings-error { min-height: 20px; }
    `;

    const root = document.createElement('div');
    root.className = 'panel';
    const title = document.createElement('div');
    title.className = 'title';
    title.textContent = 'Raksmart 自动下单';
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
    const resetButton = document.createElement('button');
    resetButton.type = 'button';
    resetButton.textContent = '重置下单锁';
    resetButton.hidden = true;
    actions.append(pauseButton, settingsButton, resetButton);

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
    appendField(settingsForm, '付款周期（留空则不校验）', 'expectedCycle', 'text', '');
    const scheduleMode = appendField(settingsForm, '场次类型', 'scheduleMode', 'select', 'once');
    const onceStart = appendField(settingsForm, '单次开始时间（含时区）', 'saleStart', 'text', '', { placeholder: '2026-11-15T20:00:00+08:00' });
    onceStart.dataset.schedule = 'once';
    const onceEnd = appendField(settingsForm, '单次结束时间（含时区）', 'saleEnd', 'text', '', { placeholder: '2026-11-15T20:10:00+08:00' });
    onceEnd.dataset.schedule = 'once';
    const dailyStart = appendField(settingsForm, '每日开始时间', 'dailyStart', 'time', '');
    dailyStart.dataset.schedule = 'daily';
    const dailyEnd = appendField(settingsForm, '每日结束时间', 'dailyEnd', 'time', '');
    dailyEnd.dataset.schedule = 'daily';
    appendField(settingsForm, '每日场次的 UTC 偏移（分钟）', 'utcOffsetMinutes', 'number', '', { required: true, min: -840, max: 840, step: 1 });
    appendField(settingsForm, '提前多少分钟开始修复', 'preheatBeforeMinutes', 'number', '', { required: true, min: 0, max: 10080, step: 1 });
    appendField(settingsForm, '页面不完整多少秒后刷新', 'reloadAfterSeconds', 'number', '', { required: true, min: 1, max: 3600, step: 1 });
    appendField(settingsForm, '检测到错误多少秒后刷新', 'errorReloadAfterSeconds', 'number', '', { required: true, min: 1, max: 3600, step: 1 });
    appendCheckboxField(settingsForm, '启用防重复下单锁（关闭后可能重复创建订单）', 'attemptLockEnabled');
    appendField(settingsForm, '关闭下单锁时的重试间隔（秒）', 'retryIntervalSeconds', 'number', '', { required: true, min: 1, max: 300, step: 1 });
    appendField(settingsForm, '商品 pid（留空不限制）', 'productId', 'text', '');
    appendField(settingsForm, 'sample_id（留空不限制）', 'sampleId', 'text', '');
    appendField(settingsForm, 'activity_id（留空不限制）', 'activityId', 'text', '');
    const hint = document.createElement('div');
    hint.className = 'hint';
    hint.textContent = '每日结束时间早于开始时间时按跨天处理。单次开始与结束均留空可停用定时购买。';
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
      quoteStableSince = 0;
      scheduleTick();
    });
    settingsButton.addEventListener('click', () => {
      if (settingsForm.hidden) openSettings();
      else closeSettings();
    });
    resetButton.addEventListener('click', resetAttemptLock);
    scheduleMode.addEventListener('change', updateScheduleFields);
    cancelButton.addEventListener('click', closeSettings);
    settingsForm.addEventListener('submit', saveSettings);
    root.append(title, status, detail, actions, settingsForm);
    shadow.append(style, root);
    document.body.appendChild(host);
    panel = {
      host, status, detail, pauseButton, settingsButton, resetButton,
      settingsForm, settingsError,
    };
  }

  function updateStatus(message, state = 'waiting') {
    createPanel();
    if (!panel) return;
    if (panel.status.textContent !== message) panel.status.textContent = message;
    panel.status.dataset.state = state;
    panel.pauseButton.textContent = paused ? '继续抢购' : '暂停抢购';
    panel.resetButton.hidden = !readAttemptLock();

    const actualPrice = readPrice();
    const actual = actualPrice === null ? '未读取到' : `$${actualPrice.toFixed(2)}`;
    const schedule = resolveSchedule(now());
    const saleText = schedule.valid ? schedule.description : '抢购时间未配置或无效';
    panel.detail.textContent = `当前 ${actual}；期望 $${Number(CONFIG.expectedPrice).toFixed(2)} / ${CONFIG.expectedCycle}；${saleText}`;
  }

  function formatCountdown(milliseconds) {
    const totalSeconds = Math.max(0, Math.ceil(milliseconds / 1000));
    const hours = Math.floor(totalSeconds / 3600);
    const minutes = Math.floor((totalSeconds % 3600) / 60);
    const seconds = totalSeconds % 60;
    return hours > 0 ? `${hours}时${minutes}分${seconds}秒` : `${minutes}分${seconds}秒`;
  }

  /* ================= 刷新与购买 ================= */

  function scheduleReload(reason, schedule = resolveSchedule(now())) {
    const lockActive = CONFIG.attemptLockEnabled
      && (purchaseClickedFor === schedule.start || readAttemptLock(schedule));
    if (reloadScheduled || paused || lockActive) return;
    if (!canAutoRepair(schedule, now())) return;
    if (navigator.onLine === false) {
      updateStatus('浏览器当前离线，等待网络恢复', 'error');
      return;
    }

    const threshold = hasLoadingError() ? CONFIG.errorReloadAfterMs : CONFIG.reloadAfterMs;
    if (Date.now() - PAGE_STARTED_AT < threshold) return;

    reloadScheduled = true;
    updateStatus(`${reason}，即将刷新页面`, 'waiting');
    const jitter = 200 + Math.floor(Math.random() * 500);
    setTimeout(() => {
      const current = now();
      const currentSchedule = resolveSchedule(current);
      const lockActive = CONFIG.attemptLockEnabled
        && (purchaseClickedFor === currentSchedule.start || readAttemptLock(currentSchedule));
      if (canAutoRepair(currentSchedule, current) && !paused && !lockActive) {
        location.reload();
      } else {
        reloadScheduled = false;
      }
    }, jitter);
  }

  function attemptPurchase(button, price, schedule) {
    if (CONFIG.attemptLockEnabled && readAttemptLock(schedule)) {
      purchaseClickedFor = schedule.start;
      updateStatus('检测到本场已有点击记录，为防止重复订单已停止', 'locked');
      return;
    }

    const elapsed = Date.now() - lastPurchaseAttemptAt;
    if (!CONFIG.attemptLockEnabled && elapsed < CONFIG.retryIntervalMs) {
      const remaining = Math.ceil((CONFIG.retryIntervalMs - elapsed) / 1000);
      updateStatus(`下单锁已关闭，${remaining} 秒后重试点击`, 'waiting');
      return;
    }

    lastPurchaseAttemptAt = Date.now();
    if (CONFIG.attemptLockEnabled) {
      saveAttemptLock(price, schedule);
      updateStatus(`价格 $${price.toFixed(2)} 匹配，已点击立即购买；请确认后续页面`, 'locked');
    } else {
      purchaseClickedFor = null;
      const retrySeconds = Math.ceil(CONFIG.retryIntervalMs / 1000);
      updateStatus(`已点击立即购买；下单锁已关闭，将每 ${retrySeconds} 秒重试`, 'waiting');
    }
    console.log(LOG_PREFIX, '点击立即购买', { price, url: location.href, time: new Date().toISOString() });
    button.click();
  }

  /* ================= 主循环 ================= */

  function tick() {
    try {
      createPanel();
      if (!productMatches()) {
        updateStatus('当前 URL 的商品参数与配置不一致，已停止自动操作', 'error');
        return;
      }

      const internalNetwork = findInternalNetworkCheckbox();
      const terms = findTermsCheckbox();
      const buyButton = findBuyButton();
      const price = readPrice();
      const cycle = readCycle();
      ensureChecked(internalNetwork, CONFIG.autoCheckInternalNetwork);
      ensureChecked(terms, CONFIG.autoAcceptTerms);

      const current = now();
      const schedule = resolveSchedule(current);
      if (!schedule.configured) {
        updateStatus('请点击右下角“设置”配置单次或每日抢购时间；当前仅自动勾选', 'waiting');
        return;
      }
      if (!schedule.valid) {
        updateStatus('抢购时间配置无效，请打开设置修正', 'error');
        return;
      }
      if (paused) {
        updateStatus('抢购与页面修复已暂停；复选框仍会保持勾选', 'waiting');
        return;
      }
      if (CONFIG.attemptLockEnabled && (purchaseClickedFor === schedule.start || readAttemptLock(schedule))) {
        purchaseClickedFor = schedule.start;
        updateStatus('本场已有点击记录，为防止重复订单已停止；请确认订单结果', 'locked');
        return;
      }
      if (CONFIG.scheduleMode !== 'daily' && current >= schedule.end) {
        updateStatus('单次抢购窗口已结束，自动购买与刷新已停止', 'locked');
        return;
      }

      const active = isActiveWindow(schedule, current);
      const preheating = isPreheatWindow(schedule, current);
      if (hasOutOfStock()) {
        updateStatus(`${active ? '抢购中' : '开抢前'}：检测到缺货，等待自动刷新`, 'waiting');
        scheduleReload('检测到缺货', schedule);
        return;
      }
      const formReady = internalNetwork && terms && buyButton && price !== null;
      if (!formReady) {
        updateStatus(active ? '抢购中：等待表单与报价加载' : preheating ? '开抢前修复：等待表单与报价加载' : '开抢前：等待表单与报价加载', 'waiting');
        scheduleReload(active ? '页面长时间未完整加载' : '开抢前修复：页面未完整加载', schedule);
        return;
      }
      if (!internalNetwork.checked || !terms.checked) {
        updateStatus('等待内网和服务条款勾选成功', 'waiting');
        scheduleReload('复选框长时间未就绪', schedule);
        return;
      }
      if (isBuyButtonDisabled(buyButton)) {
        updateStatus(active ? '抢购中：购买按钮暂不可用' : '开抢前：购买按钮暂不可用', 'waiting');
        scheduleReload('购买按钮长时间不可用', schedule);
        return;
      }

      const nextSignature = `${price}|${cycle}|${internalNetwork.checked}|${terms.checked}`;
      if (nextSignature !== quoteSignature) {
        quoteSignature = nextSignature;
        quoteStableSince = Date.now();
      }

      if (!active) {
        const readyText = `页面已就绪，距开抢 ${formatCountdown(schedule.start - current)}`;
        const priceWarning = Math.abs(price - CONFIG.expectedPrice) > 0.001 ? '；当前价与目标不同，开抢后复核' : '';
        updateStatus(`${readyText}${priceWarning}`, 'ready');
        return;
      }

      if (CONFIG.expectedCycle && !cycle.includes(normalize(CONFIG.expectedCycle))) {
        updateStatus(`付款周期不符（当前 ${cycle || '未知'}），已阻止购买`, 'error');
        return;
      }
      if (Math.abs(price - CONFIG.expectedPrice) > 0.001) {
        updateStatus(`价格不符（当前 $${price.toFixed(2)}），已阻止购买`, 'error');
        return;
      }
      if (Date.now() - quoteStableSince < CONFIG.quoteStableMs) {
        updateStatus('价格匹配，等待报价稳定', 'ready');
        return;
      }

      updateStatus('价格与付款周期匹配，准备点击立即购买', 'ready');
      attemptPurchase(buyButton, price, schedule);
    } catch (error) {
      console.error(LOG_PREFIX, '执行异常', error);
      updateStatus(`脚本执行异常：${error.message}`, 'error');
    }
  }

  function scheduleTick() {
    if (scheduled) return;
    scheduled = true;
    setTimeout(() => {
      scheduled = false;
      tick();
    }, 50);
  }

  if (typeof GM_registerMenuCommand === 'function') {
    GM_registerMenuCommand('打开自动下单设置', openSettings);
    GM_registerMenuCommand('重置本场自动下单锁', resetAttemptLock);
    GM_registerMenuCommand('暂停 / 继续抢购与页面修复', () => {
      paused = !paused;
      quoteStableSince = 0;
      scheduleTick();
    });
    GM_registerMenuCommand('恢复默认设置（保留下单锁）', () => {
      if (!window.confirm('确定清除已保存的自动下单设置并恢复默认值吗？')) return;
      store.remove(SETTINGS_KEY);
      location.reload();
    });
  }

  const observer = new MutationObserver(scheduleTick);
  observer.observe(document, {
    childList: true,
    subtree: true,
    attributes: true,
    attributeFilter: ['class', 'disabled'],
  });
  document.addEventListener('DOMContentLoaded', scheduleTick, { once: true });
  document.addEventListener('visibilitychange', scheduleTick);
  window.addEventListener('focus', scheduleTick);
  window.addEventListener('pageshow', scheduleTick);
  setInterval(tick, 250);
  tick();
})();
