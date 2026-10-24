// ==UserScript==
// @name         网页体验自由 (Web UX Freedom)
// @namespace    https://github.com/jelinenaro/web-ux-freedom
// @version      1.2.0
// @description  解除网站前端的体验限制：切屏/失焦检测、强制全屏与退出全屏检测、禁复制/禁右键/禁选中、屏蔽快捷键(F12/Ctrl+C/F5…)、离开页面拦截、鼠标移出弹窗、反反调试、缩放与滚动限制、"禁贴外部内容"诚信粘贴守卫与粘贴行为追踪。基于对 nowcoder.com 考试前端的分析编写，所有功能可按站点开关（油猴菜单），也可一键将站点加入/移出白名单（本站整体禁用）。
// @author       jelinenaro
// @match        *://*/*
// @run-at       document-start
// @grant        unsafeWindow
// @grant        GM_getValue
// @grant        GM_setValue
// @grant        GM_deleteValue
// @grant        GM_registerMenuCommand
// @grant        GM_unregisterMenuCommand
// @license      MIT
// ==/UserScript==

/*
 * 设计说明（针对 nowcoder.com 的实测结论，见 references/test.html）：
 *  1. 跳出检测：document 上监听 visibilitychange + window 上监听 blur/focus，
 *     离开页面 jumpOutTimeLimit 秒记一次，超过 jumpOutCountLimit 次强制收卷。
 *     → 拦截监听器注册 + document.hidden/visibilityState/hasFocus() 恒定伪装。
 *  2. 强制全屏：documentElement.requestFullscreen()，监听 4 种前缀的
 *     fullscreenchange；并有"全屏状态失步修复"循环（判定函数：
 *     fullscreenElement 存在 && outerWidth/outerHeight ≥ screen-20 &&
 *     matchMedia('(display-mode: fullscreen)') 不为 true &&
 *     outerHeight - innerHeight*(outerWidth/innerWidth) ∈ [40,250]）。
 *     → requestFullscreen 拦截为静默成功 + fullscreenElement 恒返回 documentElement
 *       + outerWidth/outerHeight 智能伪装（同时满足上述四个探测）。
 *  3. 禁复制：document 级 copy/cut 监听、selectstart/dragstart 拦截、
 *     内联 style.user-select:none（如 #richTextContent_444）、kindeditor 禁右键。
 *     → 高优先级 CSS + 注册拦截 + 捕获期兜底 stopImmediatePropagation。
 *  4. 反调试：console 禁用 + outer/inner 尺寸差探测开发者工具。
 *     → console 属性锁定 + 动态构造的 Function/eval 剥离 debugger。
 *  5. 剪贴板监视链路（AI 实战题"诚信作答"体系）：
 *     ① handleExternalCopy：document 级 copy/cut 读 getSelection().toString() 转发 iframe
 *        → 已被 aggressiveCopy 掐断（监听注册即丢弃）。
 *     ② 粘贴守卫 M0e/YAe/JAe：挂在答题 textarea（直接绑定）与非 contenteditable
 *        包裹元素（Vue on:{paste}）上，getData("text/plain") 读粘贴内容，与 5 分钟内
 *        站内复制台账（去空白子串比对）不匹配则 preventDefault + 弹"请勿粘贴外部内容"
 *        + 上报指纹（去空白长度+末8字符）。
 *        → pasteFreedom：注册期丢弃包裹层守卫；事件期对"可见的" input/textarea 把
 *          getData 伪装为空串 + preventDefault 空操作（守卫以为没粘贴，原生照常插入）。
 *          Monaco/CodeMirror 的隐藏输入区（尺寸极小）与 contenteditable 编辑器
 *          （需要读真实剪贴板）自动跳过，代码编辑器粘贴不受影响。
 *     ③ 顶层 document 捕获级 paste：updateCodePasted + codeBeforePasted 快照
 *        → 注册期丢弃（仅顶层窗口；iframe 内编辑器如 kindeditor 不受影响）。
 *
 * 诚实声明：本脚本只解除"前端"体验限制，无法也无意影响服务端行为（判题、
 * 计时、心跳、成绩）。请仅用于练习/模拟等自学场景；正式监考考试请遵守其规则。
 */

(function () {
  'use strict';

  /* ================= 0. 环境 ================= */

  const W = (typeof unsafeWindow !== 'undefined') ? unsafeWindow : window;
  const DOC = W.document;
  const HOST = location.hostname;
  const IS_TOP = (() => { try { return W.top === W.self; } catch (e) { return false; } })();
  const VERSION = '1.2.0';

  // ---- 原生引用抢救（在任何覆盖之前） ----
  const NAT = {};
  try { NAT.console = {}; ['log', 'info', 'warn', 'error', 'debug', 'dir', 'table', 'trace'].forEach(m => NAT.console[m] = console[m]); } catch (e) {}
  try {
    const ET = W.EventTarget && W.EventTarget.prototype;
    NAT.add = ET && ET.addEventListener;
    NAT.remove = ET && ET.removeEventListener;
  } catch (e) {}
  if (!NAT.add) NAT.add = W.addEventListener && Function.prototype.call.bind(W.addEventListener);
  NAT.matchMedia = W.matchMedia;
  NAT.eval = W.eval;
  NAT.Function = W.Function;
  NAT.evPreventDefault = W.Event && W.Event.prototype && W.Event.prototype.preventDefault;

  function protoGetter(obj, name) { // 沿原型链找访问器
    let o = obj;
    while (o) {
      const d = Object.getOwnPropertyDescriptor(o, name);
      if (d && d.get) return d.get;
      o = Object.getPrototypeOf(o);
    }
    return null;
  }
  NAT.innerW = protoGetter(W, 'innerWidth');
  NAT.innerH = protoGetter(W, 'innerHeight');
  NAT.outerW = protoGetter(W, 'outerWidth');
  NAT.outerH = protoGetter(W, 'outerHeight');
  NAT.fsEl = protoGetter(DOC, 'fullscreenElement');

  const dbg = (...a) => { if (eff('debug')) { try { (NAT.console.debug || NAT.console.log)('[WUF]', ...a); } catch (e) {} } };

  /* ================= 1. 配置 ================= */

  const DEFAULTS = {
    visibility: true,     // 防切屏：页面可见性伪装
    focus: true,          // 窗口失焦伪装（blur/focus）
    fullscreen: true,     // 全屏状态伪装（网站认为一直处于全屏）
    blockFullscreen: true,// 拦截强制全屏调用（静默"成功"但不真的全屏）
    sizeCloak: true,      // outerWidth/outerHeight 伪装为屏幕尺寸
    resizeLie: false,     // resize 回调期间 innerWidth/innerHeight 也伪装（更强，可能影响响应式布局）
    copy: true,           // 恢复复制/剪切
    contextmenu: true,    // 恢复右键菜单
    selection: true,      // 恢复文字选中/拖拽
    hotkey: true,         // 恢复浏览器快捷键（Ctrl+C/F12/F5/Ctrl+F…）
    unload: true,         // 解除 beforeunload 离开拦截
    exitIntent: true,     // 阻止"鼠标移出页面就弹窗"
    antiDebug: true,      // 反反调试（console 恢复、动态 debugger 剥离）
    zoom: true,           // 解除页面缩放限制（viewport meta）
    scroll: false,        // 解锁页面滚动（默认关，被弹窗锁死时再开）
    alerts: true,         // 静音违规类 alert/confirm 弹窗
    pasteFreedom: false,  // 粘贴自由：解除"禁贴外部内容"诚信守卫与粘贴行为追踪（全局默认关，nowcoder 默认开）
    debug: false,         // 调试日志
  };

  // 站点档案：nowcoder 走激进模式；交互密集型 Web 应用默认关闭会误伤的功能
  const DOMAIN_RULES = [
    {
      match: /(^|\.)nowcoder\.com$/i,
      vals: { aggressiveCopy: true, pasteFreedom: true }, // 直接阻断 document 级 copy/cut 等；默认开启粘贴自由
    },
    ...[
      /(^|\.)(docs|sheets|slides|drive)\.google\.com$/i, /(^|\.)figma\.com$/i,
      /(^|\.)(notion\.so|notion\.site)$/i, /(^|\.)canva\.com$/i,
      /(^|\.)(office\.com|office365\.com|sharepoint\.com|live\.com)$/i,
      /(^|\.)(miro\.com|lucid\.app|processon\.com)$/i,
      /(^|\.)(diagrams\.net|app\.diagrams\.net|excalidraw\.com)$/i,
      /(^|\.)wolframalpha\.com$/i, /(^|\.)xmind\.app$/i,
      /(^|\.)(replit\.com|stackblitz\.com|codesandbox\.io)$/i,
    ].map(re => ({ match: re, vals: { copy: false, contextmenu: false, selection: false, blockFullscreen: false } })),
  ];

  let PROFILE = {};
  for (const r of DOMAIN_RULES) if (r.match.test(HOST)) { PROFILE = r.vals; break; }

  // ---- 存储（优先 GM_*，退化到 localStorage） ----
  const store = {
    get(k) {
      try { if (typeof GM_getValue === 'function') return GM_getValue(k); } catch (e) {}
      try { const v = W.localStorage.getItem('wuf:' + k); return v == null ? undefined : JSON.parse(v); } catch (e) {}
      return undefined;
    },
    set(k, v) {
      try { if (typeof GM_setValue === 'function') { GM_setValue(k, v); return; } } catch (e) {}
      try { W.localStorage.setItem('wuf:' + k, JSON.stringify(v)); } catch (e) {}
    },
    del(k) {
      try { if (typeof GM_deleteValue === 'function') GM_deleteValue(k); } catch (e) {}
      try { W.localStorage.removeItem('wuf:' + k); } catch (e) {}
    },
  };
  const OVR_KEY = 'ovr:' + HOST;
  let OVR = store.get(OVR_KEY);
  if (!OVR || typeof OVR !== 'object') OVR = {};

  function eff(key) {
    if (key in OVR) return !!OVR[key];
    if (key in PROFILE) return !!PROFILE[key];
    return DEFAULTS[key] !== false;
  }

  // ---- 站点白名单：本站整体禁用（在安装任何 hook 之前判定） ----
  const OFF_KEY = 'site-off:' + HOST;
  if (store.get(OFF_KEY) === true) {
    // 完全不碰页面，只留一个恢复入口
    if (IS_TOP && typeof GM_registerMenuCommand === 'function') {
      GM_registerMenuCommand('▶️ 移出白名单 —— 本站启用脚本', () => {
        store.del(OFF_KEY);
        location.reload();
      });
    }
    return;
  }

  /* ================= 2. 统计（window.__WUF__） ================= */

  const STATS = { dropped: {}, intent: {}, stopped: {}, hotkey: {}, suppressed: 0 };
  const bump = (o, k) => { o[k] = (o[k] || 0) + 1; };
  try {
    W.__WUF__ = { version: VERSION, host: HOST, profile: PROFILE, stats: STATS, eff, defaults: DEFAULTS };
  } catch (e) {}

  /* ================= 3. 事件监听器注册拦截 ================= */

  const VIS_TYPES = ['visibilitychange', 'webkitvisibilitychange', 'mozvisibilitychange', 'msvisibilitychange', 'pagevisibilitychange'];
  const FS_TYPES = ['fullscreenchange', 'webkitfullscreenchange', 'mozfullscreenchange', 'MSFullscreenChange', 'msfullscreenchange',
                    'fullscreenerror', 'webkitfullscreenerror', 'mozfullscreenerror', 'MSFullscreenError'];
  const COPY_FAMILY = ['copy', 'cut', 'contextmenu', 'selectstart', 'dragstart'];
  const copyKey = t => (t === 'copy' || t === 'cut') ? 'copy' : (t === 'contextmenu' ? 'contextmenu' : 'selection');

  function isWin(t) { return t === W; }
  function isDocScope(t) { return t === DOC || t === DOC.documentElement || t === DOC.body; }

  // 拦截决策：返回 true=放行原样注册；false=丢弃；函数=换包装后注册
  function decide(type, target) {
    const t = String(type);

    if (VIS_TYPES.includes(t) && isDocScope(target) && eff('visibility')) { bump(STATS.dropped, t); dbg('drop', t, '@', target); return false; }

    if (FS_TYPES.includes(t) && (isWin(target) || isDocScope(target)) && eff('fullscreen')) { bump(STATS.dropped, t); return false; }

    if ((t === 'blur' || t === 'focus') && isWin(target) && eff('focus')) { bump(STATS.dropped, 'window.' + t); return false; }

    if (t === 'beforeunload' && (isWin(target) || isDocScope(target)) && eff('unload')) { bump(STATS.dropped, t); return false; }

    if ((t === 'mouseleave' || t === 'mouseenter') && isDocScope(target) && eff('exitIntent')) { bump(STATS.dropped, 'document.' + t); return false; }

    if (COPY_FAMILY.includes(t) && (isWin(target) || isDocScope(target))) {
      const key = copyKey(t);
      if (eff(key)) {
        if (PROFILE.aggressiveCopy) { bump(STATS.dropped, t); bump(STATS.intent, t); return false; }
        // 观察模式：先放行；一旦该监听器真的 preventDefault 过，之后捕获期直接拦截
        return function (ev) {
          const fn = arguments.length && arguments[1]; // 占位，实际在 installHook 里生成
          void fn;
        };
      }
    }

    // ---- 粘贴自由：掐断守卫的注册（事件级伪装见 8b 节） ----
    if (eff('pasteFreedom') && t === 'paste') {
      const docScope = isWin(target) || isDocScope(target);
      // 链路③：顶层 document/window 捕获级粘贴追踪（codeBeforePasted 快照）。
      // 仅限顶层窗口——iframe 内（kindeditor 编辑器等）的 document 级 paste 不动。
      if (docScope && IS_TOP) { bump(STATS.dropped, 'paste@doc'); dbg('drop paste@doc'); return false; }
      // 链路②：挂在"非 contenteditable、非输入框"包裹元素上的 paste 监听（守卫位置）。
      // 编辑器自身逻辑在 contenteditable 或其输入区上，不受影响。
      if (!docScope && target && target.nodeType === 1 &&
          !target.isContentEditable && target.tagName !== 'INPUT' && target.tagName !== 'TEXTAREA') {
        bump(STATS.dropped, 'paste@wrapper'); dbg('drop paste@wrapper:', target.tagName, target.className); return false;
      }
      // input/textarea（守卫直接绑定）与 contenteditable：放行注册，由事件期伪装对付
    }

    if (t === 'resize' && isWin(target) && eff('resizeLie')) return 'RESIZE_LIE';

    return true;
  }

  function makeObserver(type, orig) {
    return function (ev) {
      const r = orig.apply(this, arguments);
      try { if (ev && ev.defaultPrevented) { bump(STATS.intent, type); dbg('intent detected:', type); } } catch (e) {}
      return r;
    };
  }

  // resize 回调执行期间，把 innerWidth/innerHeight 伪装成屏幕尺寸（对付
  // “innerHeight === screen.height 才算全屏”这类窗口尺寸兜底检测）
  function makeResizeLie(orig) {
    return function (ev) {
      if (!eff('resizeLie') || !NAT.innerW || !NAT.innerH) return orig.apply(this, arguments);
      const sw = W.screen.width, sh = W.screen.height;
      const fakeW = { get() { return sw; }, configurable: true };
      const fakeH = { get() { return sh; }, configurable: true };
      let lied = false;
      try { Object.defineProperty(W, 'innerWidth', fakeW); Object.defineProperty(W, 'innerHeight', fakeH); lied = true; } catch (e) {}
      try { return orig.apply(this, arguments); }
      finally {
        if (lied) {
          try { Object.defineProperty(W, 'innerWidth', { get: NAT.innerW, configurable: true }); } catch (e) {}
          try { Object.defineProperty(W, 'innerHeight', { get: NAT.innerH, configurable: true }); } catch (e) {}
        }
      }
    };
  }

  function installAddEventListenerHook() {
    const ET = W.EventTarget && W.EventTarget.prototype;
    if (!ET || !NAT.add) return false;

    const hooked = function (type, listener, options) {
      if (listener == null) return NAT.add.call(this, type, listener, options);
      let action = true;
      try { action = decide(type, this); } catch (e) { action = true; }

      if (action === false) return undefined;
      if (action === 'RESIZE_LIE') {
        const wrapped = makeResizeLie(listener);
        return NAT.add.call(this, type, wrapped, options);
      }
      if (typeof action === 'function' && COPY_FAMILY.includes(String(type))) {
        const wrapped = makeObserver(String(type), listener);
        return NAT.add.call(this, type, wrapped, options);
      }
      return NAT.add.call(this, type, listener, options);
    };
    try {
      Object.defineProperty(ET, 'addEventListener', { value: hooked, writable: true, configurable: true });
      if (ET.addEventListener !== hooked) return false;
      dbg('prototype hook installed');
      return true;
    } catch (e) { return false; }
  }

  // 原型打不上（个别浏览器/管理器）时退化为 window/document 实例级补丁，覆盖 95% 场景
  function installInstanceFallback() {
    const hooked = function (type, listener, options) {
      if (listener == null) return NAT.add.call(this, type, listener, options);
      let action = true;
      try { action = decide(type, this); } catch (e) { action = true; }
      if (action === false) return undefined;
      if (action === 'RESIZE_LIE') return NAT.add.call(this, type, makeResizeLie(listener), options);
      if (typeof action === 'function' && COPY_FAMILY.includes(String(type)))
        return NAT.add.call(this, type, makeObserver(String(type), listener), options);
      return NAT.add.call(this, type, listener, options);
    };
    try { Object.defineProperty(W, 'addEventListener', { value: hooked, configurable: true }); } catch (e) {}
    try { Object.defineProperty(DOC, 'addEventListener', { value: hooked, configurable: true }); } catch (e) {}
    dbg('instance fallback installed');
  }

  if (!installAddEventListenerHook()) installInstanceFallback();

  /* ================= 4. onXxx 属性陷阱 ================= */

  function trapOn(obj, name) {
    try {
      Object.defineProperty(obj, name, {
        configurable: true,
        get() { return null; },
        set(v) { if (v) { bump(STATS.dropped, 'on' + name); dbg('trap on' + name); } },
      });
    } catch (e) {}
  }
  if (eff('visibility')) trapOn(DOC, 'onvisibilitychange');
  if (eff('fullscreen')) ['onfullscreenchange', 'onwebkitfullscreenchange'].forEach(n => trapOn(DOC, n));
  if (eff('focus')) ['onblur', 'onfocus'].forEach(n => trapOn(W, n));
  if (eff('unload')) trapOn(W, 'onbeforeunload');
  if (eff('exitIntent')) ['onmouseleave', 'onmouseenter'].forEach(n => trapOn(DOC, n));
  if (eff('copy') || eff('contextmenu') || eff('selection')) {
    ['oncopy', 'oncut', 'oncontextmenu', 'onselectstart', 'ondragstart'].forEach(n => trapOn(DOC, n));
  }
  // body 要等 DOM 就绪
  NAT.add && NAT.add.call(W, 'DOMContentLoaded', function onReady() {
    try {
      if (eff('exitIntent')) ['onmouseleave', 'onmouseenter'].forEach(n => trapOn(DOC.body, n));
      if (eff('copy') || eff('contextmenu') || eff('selection'))
        ['oncopy', 'oncut', 'oncontextmenu', 'onselectstart', 'ondragstart'].forEach(n => trapOn(DOC.body, n));
      if (eff('unload')) trapOn(DOC.body, 'onbeforeunload');
    } catch (e) {}
    refreshCSS(); fixViewport();
  });

  /* ================= 5. 页面状态伪装 ================= */

  function lockGetter(obj, name, fn) {
    try { Object.defineProperty(obj, name, { get: fn, configurable: false, enumerable: true }); return true; } catch (e) { return false; }
  }

  if (eff('visibility')) {
    lockGetter(DOC, 'hidden', () => false);
    lockGetter(DOC, 'visibilityState', () => 'visible');
    ['webkitHidden', 'mozHidden', 'msHidden'].forEach(n => lockGetter(DOC, n, () => false));
    ['webkitVisibilityState', 'mozVisibilityState', 'msVisibilityState'].forEach(n => lockGetter(DOC, n, () => 'visible'));
    try { Object.defineProperty(DOC, 'hasFocus', { value: () => true, writable: false, configurable: false }); } catch (e) {}
  }

  if (eff('fullscreen')) {
    const fakeFsEl = () => {
      try { const real = NAT.fsEl && NAT.fsEl.call(DOC); if (real) return real; } catch (e) {}
      return DOC.documentElement || null;
    };
    lockGetter(DOC, 'fullscreenElement', fakeFsEl);
    ['webkitFullscreenElement', 'mozFullScreenElement', 'msFullscreenElement', 'webkitCurrentFullScreenElement'].forEach(n => lockGetter(DOC, n, fakeFsEl));
    try { Object.defineProperty(DOC, 'fullscreen', { get: () => true, configurable: false }); } catch (e) {}
    try { Object.defineProperty(W, 'fullScreen', { get: () => true, configurable: true }); } catch (e) {}

    // PWA display-mode 探测
    if (NAT.matchMedia) {
      try {
        Object.defineProperty(W, 'matchMedia', {
          value: function (q) {
            const s = String(q);
            if (/display-mode/.test(s) && /fullscreen/.test(s)) {
              return { matches: false, media: s, onchange: null,
                addListener() {}, removeListener() {}, addEventListener() {}, removeEventListener() {},
                dispatchEvent() { return false; } };
            }
            return NAT.matchMedia.call(W, q);
          }, writable: true, configurable: true,
        });
      } catch (e) {}
    }
  }

  /* ================= 6. 窗口尺寸伪装 ================= */
  // 目标：同时骗过
  //   ke(): outerHeight < screen.height - 20 || outerWidth < screen.width - 20（窗口化判定）
  //   pn(): outerHeight - innerHeight*(outerWidth/innerWidth) ∈ [40,250]（"浏览器工具栏高度"，兼做 devtools 探测）
  //   oi(): outerWidth === screen.width && outerHeight === screen.height（部分全屏判定）
  if (eff('sizeCloak') && NAT.innerW && NAT.innerH) {
    const sw = () => (W.screen && W.screen.width) || 1280;
    const sh = () => (W.screen && W.screen.height) || 800;
    try {
      Object.defineProperty(W, 'outerWidth', { get: () => sw(), configurable: true });
      Object.defineProperty(W, 'outerHeight', {
        get: () => {
          const iw = NAT.innerW.call(W), ih = NAT.innerH.call(W), S = sh(), Wd = sw();
          if (!iw || !ih) return S;
          // 让 pn() 的"工具栏高度"落在 ~100（40~250 之间），且 outer ≥ screen-20 满足 ke()。
          // 极端窄高窗口下值会偏大，但探测类检查只看区间，不看绝对值。
          const t = ih * (Wd / iw);
          return Math.round(Math.max(S - 19, t + 100));
        },
        configurable: true,
      });
    } catch (e) {}
  }

  /* ================= 7. 全屏 API 拦截 ================= */

  (function wrapFullscreenApi() {
    if (!eff('blockFullscreen') && !eff('fullscreen')) return;
    const ElP = W.Element && W.Element.prototype;
    const rqNames = ['requestFullscreen', 'webkitRequestFullscreen', 'webkitRequestFullScreen', 'mozRequestFullScreen', 'msRequestFullscreen'];

    const shouldFake = function (el) {
      if (!eff('blockFullscreen')) return false;
      // 视频播放器（元素自身或内部含 <video>）的全屏是用户真实需要，放行
      try {
        if (el && (el.tagName === 'VIDEO' || (el.querySelector && el.querySelector('video')))) return false;
      } catch (e) {}
      return true; // documentElement/body/覆盖层等一律"假成功"
    };

    if (ElP) rqNames.forEach(name => {
      const orig = ElP[name];
      if (typeof orig !== 'function') return;
      try {
        Object.defineProperty(ElP, name, {
          value: function () {
            if (shouldFake(this)) { dbg('fake requestFullscreen on', this && this.tagName); return W.Promise ? W.Promise.resolve() : undefined; }
            return orig.apply(this, arguments);
          }, writable: true, configurable: true,
        });
      } catch (e) {}
    });

    // exitFullscreen：并未真的全屏时直接 resolve，避免站点拿到 TypeError
    const DocP = W.Document && W.Document.prototype;
    ['exitFullscreen', 'webkitExitFullscreen', 'mozCancelFullScreen', 'msExitFullscreen'].forEach(name => {
      const orig = DocP && DocP[name];
      if (typeof orig !== 'function') return;
      try {
        Object.defineProperty(DocP, name, {
          value: function () {
            let real = null;
            try { real = NAT.fsEl && NAT.fsEl.call(this); } catch (e) {}
            if (!real) return W.Promise ? W.Promise.resolve() : undefined;
            return orig.apply(this, arguments);
          }, writable: true, configurable: true,
        });
      } catch (e) {}
    });
  })();

  /* ================= 8. 捕获期兜底守卫 ================= */

  // copy 家族：window 捕获阶段最先执行，可拦住内联属性 oncopy="return false"、
  // Vue/jQuery 绑定、以及观察模式下"确认作恶"过的监听器
  function hasInlineHandler(ev, type) {
    try {
      const path = ev.composedPath ? ev.composedPath() : [];
      for (const n of path) {
        if (n && typeof n.getAttribute === 'function') {
          const a = n.getAttribute('on' + type);
          if (a != null && a !== '') return true;
        }
        if (n == null) break;
      }
    } catch (e) {}
    return false;
  }

  COPY_FAMILY.forEach(type => {
    [W, DOC].forEach(tgt => {
      if (!NAT.add) return;
      NAT.add.call(tgt, type, function (ev) {
        const key = copyKey(type);
        if (!eff(key)) return;
        if (!(PROFILE.aggressiveCopy || STATS.intent[type] || hasInlineHandler(ev, type))) return;
        try { ev.stopImmediatePropagation(); } catch (e) {}
        bump(STATS.stopped, type);
        dbg('stopped', type);
      }, true);
    });
  });

  // 快捷键守卫：命中"浏览器默认行为"类组合时，让页面监听器的 preventDefault 变空操作。
  // 注意不拦 stopPropagation、不动普通按键，站点自己的快捷键（如 Ctrl+Enter 提交）不受影响。
  const PROTECT_KEYS = new Set(['c', 'x', 'v', 'a', 's', 'p', 'u', 'r', 'f']);
  function isProtectedKey(e) {
    const k = String(e.key || '').toLowerCase();
    const ctrl = e.ctrlKey || e.metaKey;
    if (k === 'f12' || k === 'f5' || k === 'f3') return true;
    if (ctrl && !e.altKey && !e.shiftKey && PROTECT_KEYS.has(k)) return true;      // Ctrl/Cmd+C/X/V/A/S/P/U/R/F
    if (ctrl && e.shiftKey && (k === 'i' || k === 'j' || k === 'c')) return true;  // DevTools
    if (e.metaKey && e.altKey && (k === 'i' || k === 'j' || k === 'c' || k === 'u')) return true; // macOS Cmd+Opt+…
    return false;
  }
  if (NAT.add) NAT.add.call(W, 'keydown', function (e) {
    if (!eff('hotkey') || !isProtectedKey(e) || !NAT.evPreventDefault) return;
    try {
      let shielded = true;
      Object.defineProperty(e, 'preventDefault', {
        configurable: true,
        value: function () {
          if (shielded && isProtectedKey(e)) { bump(STATS.hotkey, e.key || '?'); dbg('hotkey shielded:', e.key); return; }
          return NAT.evPreventDefault.call(e);
        },
      });
      // 页面也可能读 defaultPrevented 做后续逻辑，顺手一起伪装
      let dp = false;
      Object.defineProperty(e, 'defaultPrevented', {
        configurable: true,
        get() { return dp || false; },
      });
      const origDP = e.defaultPrevented;
      void origDP;
    } catch (err) {}
  }, true);

  /* ================= 8b. 粘贴自由：事件期伪装 + 残余弹窗清理 ================= */

  // 粘贴/拖放到"用户可见的" input/textarea 时：把 clipboardData.getData 伪装成恒返回空串、
  // preventDefault 变空操作。守卫以为什么都没粘 → 不拦截、不弹窗、不上报指纹；
  // 原生粘贴/拖放不走 getData，照常插入。
  // 判定"用户可见"以跳过 Monaco/CodeMirror 的隐藏输入区（尺寸极小/移出屏幕），
  // contenteditable 富文本编辑器需要读真实剪贴板，也不在此处理（其守卫已在注册期丢弃）。
  function isUserFacingField(el) {
    try {
      const r = el.getBoundingClientRect();
      if (r.width < 30 || r.height < 12 || r.left < -900) return false;
      const st = W.getComputedStyle(el);
      return st.visibility !== 'hidden' && st.display !== 'none' && st.opacity !== '0';
    } catch (e) { return false; }
  }

  if (NAT.add) ['paste', 'drop'].forEach(type => {
    NAT.add.call(W, type, function (ev) {
      if (!eff('pasteFreedom')) return;
      const tgt = ev.target;
      if (!tgt || tgt.nodeType !== 1) return;
      const tag = tgt.tagName;
      if ((tag !== 'INPUT' && tag !== 'TEXTAREA') || tgt.isContentEditable) return;
      if (!isUserFacingField(tgt)) return;
      try {
        const cd = ev.clipboardData || ev.dataTransfer;
        if (cd && typeof cd.getData === 'function') cd.getData = function () { return ''; };
      } catch (e) {}
      try {
        Object.defineProperty(ev, 'preventDefault', { configurable: true, value: function () {} });
      } catch (e) {}
      bump(STATS.stopped, type + '-shield');
      dbg(type, 'shielded @', tag);
    }, true);
  });

  // 残余的"请勿粘贴外部内容"弹窗自动关闭。element-ui 的 Dialog 用 v-show 切换
  // display（无 childList 变更），因此用可见性轮询兜底 + childList 观察动态挂载的弹窗。
  function isNodeVisible(el) {
    try { const r = el.getBoundingClientRect(); return r.width > 0 && r.height > 0; } catch (e) { return false; }
  }
  function closePasteDialog() {
    const el = DOC.querySelector && DOC.querySelector('.ai-paste-block');
    if (!el || !isNodeVisible(el)) return;
    bump(STATS.stopped, 'paste-dialog');
    const wrap = el.closest && (el.closest('.el-dialog__wrapper') || el.closest('.el-overlay') || el.parentElement);
    const btn = wrap && (wrap.querySelector('.el-dialog__headerbtn') || wrap.querySelector('[class*="close" i], [class*="Close"]'));
    if (btn) { try { btn.click(); return; } catch (e) {} }
    if (wrap) { try { wrap.style.display = 'none'; } catch (e) {} }
  }
  function armPasteDialogSweeper() {
    if (!eff('pasteFreedom') || !DOC.body) return;
    try {
      const mo = new W.MutationObserver((muts) => {
        for (const m of muts) {
          for (const n of m.addedNodes) {
            if (!n || n.nodeType !== 1) continue;
            if ((n.classList && n.classList.contains('ai-paste-block')) ||
                (n.querySelector && n.querySelector('.ai-paste-block'))) {
              W.setTimeout(closePasteDialog, 50);
              return;
            }
          }
        }
      });
      mo.observe(DOC.body, { childList: true, subtree: true });
    } catch (e) {}
    W.setInterval(() => { if (eff('pasteFreedom')) closePasteDialog(); }, 300);
  }
  if (DOC.body) armPasteDialogSweeper();
  else NAT.add.call(W, 'DOMContentLoaded', armPasteDialogSweeper);

  /* ================= 9. 反反调试 ================= */

  (function antiAntiDebug() {
    if (!eff('antiDebug')) return;

    // 9.1 锁定 console：页面禁用 console.log / 刷 console.clear 都失效
    try {
      for (const m of ['log', 'info', 'warn', 'error', 'debug', 'dir', 'table', 'trace']) {
        const native = NAT.console[m];
        if (!native) continue;
        Object.defineProperty(W.console, m, {
          configurable: true,
          get() { return native; },
          set() { bump(STATS.dropped, 'console.' + m); },
        });
      }
      Object.defineProperty(W.console, 'clear', { configurable: true, get() { return function () {}; }, set() {} });
    } catch (e) {}

    // 9.2 剥离动态构造的 debugger（new Function('debugger') / Function('debugger') / eval('debugger')）
    //     注：打包进 bundle 的静态 debugger 语句无法从 JS 层移除，请用 DevTools 的
    //     "Never pause here" 或停用断点按钮应对。
    try {
      const strip = s => (/\bdebugger\b/.test(s) ? String(s).replace(/\bdebugger\b/g, 'void 0') : s);
      const NativeFunction = NAT.Function;
      const Fn = function Function() {
        const a = Array.prototype.slice.call(arguments);
        a[a.length - 1] = strip(a[a.length - 1]);
        return NativeFunction.apply(this, a);
      };
      try { Fn.prototype = NativeFunction.prototype; } catch (e) {}
      try { Fn.toString = function () { return 'function Function() { [native code] }'; }; } catch (e) {}
      W.Function = Fn;
      try { NativeFunction.prototype.constructor = Fn; } catch (e) {}
      W.eval = function (src) { return NAT.eval.call(W, strip(src)); };
      try { Object.defineProperty(W.eval, 'toString', { value: function () { return 'function eval() { [native code] }'; } }); } catch (e) {}
    } catch (e) {}
  })();

  /* ================= 10. CSS：选中/复制/滚动 ================= */

  const CSS_ID = 'wuf-style';
  function ensureStyle() {
    let el = null;
    try { el = DOC.getElementById(CSS_ID); } catch (e) {}
    if (!el) {
      el = DOC.createElement('style');
      el.id = CSS_ID;
      (DOC.head || DOC.documentElement).appendChild(el);
    }
    return el;
  }
  function refreshCSS() {
    if (!DOC.createElement) return;
    let css = '';
    if (eff('copy') || eff('selection')) {
      // 三重 :not(#id) 拉满特异性，压过站点带 !important 的 user-select:none；
      // user-select 可继承，shadow DOM 内容也会跟随宿主元素恢复
      css += '*, *::before, *::after { -webkit-user-select: text !important; -moz-user-select: text !important; user-select: text !important; -webkit-touch-callout: default !important; }'
           + '::selection { background-color: #338fff !important; color: #fff !important; }';
    }
    if (eff('scroll')) {
      css += 'html, body { overflow: auto !important; overscroll-behavior: auto !important; }';
    }
    try { ensureStyle().textContent = css; } catch (e) {}
  }
  if (DOC.documentElement) refreshCSS();
  else NAT.add.call(W, 'readystatechange', () => { if (DOC.documentElement) refreshCSS(); });

  /* ================= 11. viewport 缩放限制 ================= */

  function fixViewport() {
    if (!eff('zoom') || !DOC.querySelectorAll) return;
    try {
      DOC.querySelectorAll('meta[name="viewport" i], meta[name="Viewport" i]').forEach(m => {
        let c = m.getAttribute('content') || '';
        if (!c) return;
        let changed = false;
        c = c.replace(/(user-scalable)\s*=\s*no/i, () => { changed = true; return 'user-scalable=yes'; });
        c = c.replace(/(maximum-scale)\s*=\s*[\d.]+/i, () => { changed = true; return 'maximum-scale=10'; });
        c = c.replace(/(minimum-scale)\s*=\s*[\d.]+/i, () => { changed = true; return 'minimum-scale=0.1'; });
        if (changed) { try { m.setAttribute('content', c); dbg('viewport fixed:', c); } catch (e) {} }
      });
    } catch (e) {}
  }
  fixViewport();
  try {
    let mo = null;
    const stop = () => { if (mo) mo.disconnect(); };
    mo = new W.MutationObserver(() => fixViewport());
    mo.observe(DOC.documentElement, { childList: true, subtree: true });
    NAT.add.call(W, 'load', stop);
  } catch (e) {}

  /* ================= 12. 违规弹窗静音 ================= */

  (function wrapDialogs() {
    if (!eff('alerts')) return;
    const VIOLATION = /切屏|跳出|切换|离开|退出|全屏|监考|监控|检测|作弊|违规|违纪|复制|禁止|警告|考试|交卷|防作弊|snapshot|proctor|violat/i;
    ['alert', 'confirm', 'prompt'].forEach(fnName => {
      const orig = W[fnName];
      if (typeof orig !== 'function') return;
      try {
        W[fnName] = function (msg) {
          const text = String(msg == null ? '' : msg);
          if (!VIOLATION.test(text)) return orig.apply(W, arguments);
          STATS.suppressed++;
          dbg('suppressed ' + fnName + ':', text.slice(0, 60));
          if (fnName === 'confirm') return false;   // 违规类确认框默认选"取消"，避免误触强制交卷等流程
          if (fnName === 'prompt') return null;
          return undefined;
        };
      } catch (e) {}
    });
  })();

  /* ================= 13. 油猴菜单 ================= */

  function toast(msg) {
    if (!IS_TOP || !DOC.createElement) return;
    try {
      const d = DOC.createElement('div');
      d.textContent = msg;
      d.style.cssText = 'position:fixed;right:16px;bottom:16px;z-index:2147483647;background:rgba(20,20,28,.92);color:#fff;padding:10px 14px;border-radius:8px;font:13px/1.5 system-ui,sans-serif;max-width:340px;box-shadow:0 4px 16px rgba(0,0,0,.3);transition:opacity .3s';
      (DOC.body || DOC.documentElement).appendChild(d);
      W.setTimeout(() => { d.style.opacity = '0'; }, 2200);
      W.setTimeout(() => { try { d.remove(); } catch (e) {} }, 2600);
    } catch (e) {}
  }

  const FEATURES = [
    { k: 'visibility',      n: '防切屏（页面可见性伪装）' },
    { k: 'focus',           n: '窗口失焦伪装' },
    { k: 'fullscreen',      n: '全屏状态伪装' },
    { k: 'blockFullscreen', n: '拦截强制全屏' },
    { k: 'sizeCloak',       n: '窗口尺寸伪装(outer)' },
    { k: 'resizeLie',       n: 'resize尺寸伪装(强力)' },
    { k: 'copy',            n: '恢复复制/剪切' },
    { k: 'contextmenu',     n: '恢复右键菜单' },
    { k: 'selection',       n: '恢复文字选中/拖拽' },
    { k: 'pasteFreedom',    n: '粘贴自由（解除诚信粘贴守卫/追踪）' },
    { k: 'hotkey',          n: '恢复浏览器快捷键' },
    { k: 'unload',          n: '解除离开页面拦截' },
    { k: 'exitIntent',      n: '阻止鼠标移出弹窗' },
    { k: 'antiDebug',       n: '反反调试' },
    { k: 'zoom',            n: '解除页面缩放限制' },
    { k: 'scroll',          n: '解锁页面滚动' },
    { k: 'alerts',          n: '静音违规警告弹窗' },
    { k: 'debug',           n: '调试日志' },
  ];

  const menuIds = [];
  function refreshMenu() {
    if (typeof GM_unregisterMenuCommand === 'function') { menuIds.forEach(id => { try { GM_unregisterMenuCommand(id); } catch (e) {} }); }
    menuIds.length = 0;
    if (typeof GM_registerMenuCommand !== 'function') return;
    menuIds.push(GM_registerMenuCommand('🚫 加入白名单 —— 本站禁用脚本', () => {
      store.set(OFF_KEY, true);
      toast('已加入白名单，即将刷新生效…');
      W.setTimeout(() => { try { location.reload(); } catch (e) {} }, 600);
    }));
    FEATURES.forEach(f => {
      const on = eff(f.k);
      const label = (on ? '✅ ' : '⬜ ') + f.n + (on ? '：开' : '：关');
      menuIds.push(GM_registerMenuCommand(label, () => {
        OVR[f.k] = !on;
        store.set(OVR_KEY, OVR);
        refreshCSS();
        toast(`【${f.n}】已${OVR[f.k] ? '开启' : '关闭'}（已注册的拦截需刷新页面后完全生效）`);
        refreshMenu();
      }));
    });
    menuIds.push(GM_registerMenuCommand('🔄 重置本站设置（恢复默认）', () => {
      OVR = {};
      store.del(OVR_KEY);
      refreshCSS();
      toast('已恢复默认设置，刷新页面后完全生效');
      refreshMenu();
    }));
  }
  if (IS_TOP) refreshMenu();

  dbg('initialized on', HOST, 'profile=', PROFILE);
})();
