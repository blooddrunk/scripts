// web-ux-freedom.user.js 自动验证
// 两次运行同一 demo 页：CONTROL（不注入，确认限制生效）/ TREATMENT（注入 userscript + GM shim）
// 用法: node demo/verify.mjs
import { chromium } from 'playwright';
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import path from 'node:path';

const root = path.dirname(path.dirname(fileURLToPath(import.meta.url)));
const demoUrl = 'file://' + path.join(root, 'demo', 'restrictions-demo.html');
const userjs = readFileSync(path.join(root, 'web-ux-freedom.user.js'), 'utf8');

// 模拟 Tampermonkey 提供的 GM_* API（unsafeWindow 在 addInitScript 中即页面主世界）
const gmShim = `
window.unsafeWindow = window;
window.GM_getValue = (k, d) => {
  try { const v = localStorage.getItem('gm:' + k); return v == null ? d : JSON.parse(v); } catch (e) { return d; }
};
window.GM_setValue = (k, v) => { try { localStorage.setItem('gm:' + k, JSON.stringify(v)); } catch (e) {} };
window.GM_deleteValue = (k) => { try { localStorage.removeItem('gm:' + k); } catch (e) {} };
window.GM_registerMenuCommand = (label) => 'cmd:' + label;
window.GM_unregisterMenuCommand = () => {};
`;

const results = [];
function check(name, cond, detail = '') {
  results.push({ name, pass: !!cond, detail });
  console.log(`${cond ? '  ✅' : '  ❌'} ${name}${detail ? '  — ' + detail : ''}`);
}

async function runScenario(label, { inject }) {
  console.log(`\n========== ${label} ==========`);
  const browser = await chromium.launch();
  const ctx = await browser.newContext({
    viewport: { width: 900, height: 640 },   // 明显小于屏幕，制造"窗口化"状态
    screen: { width: 1920, height: 1080 },
  });
  if (inject) {
    // file:// 主机名为空、无 nowcoder 档案 → 通过存储覆盖开启 pasteFreedom/keyFreedom
    //（同时验证菜单开关的持久化路径，也证明两项默认关、需显式开启）
    await ctx.addInitScript(gmShim + "\nlocalStorage.setItem('gm:ovr:' + location.hostname, JSON.stringify({ pasteFreedom: true, keyFreedom: true }));\n" + userjs);
  }
  const page = await ctx.newPage();
  const dialogs = [];
  page.on('dialog', async (d) => { dialogs.push(d.message()); await d.accept().catch(() => {}); });
  await page.goto(demoUrl);
  await page.waitForTimeout(400);

  const d = (expr) => page.evaluate(expr);

  /* ---- 1. 切屏/可见性 ----
   * headless 无窗口焦点系统，bringToFront 不产生真实 hidden 状态；
   * 改为：对照组直接改写 document.hidden/visibilityState 并派发事件（验证 demo 逻辑生效）；
   * 实验组做同样操作应当抛异常（getter 已锁定为 non-configurable），事件派发后计数为 0。 */
  const bg = await d(`(() => {
    const out = {};
    try {
      Object.defineProperty(document, 'hidden', { get: () => true, configurable: true });
      Object.defineProperty(document, 'visibilityState', { get: () => 'hidden', configurable: true });
      out.patched = true;
    } catch (e) { out.patched = false; }
    document.dispatchEvent(new Event('visibilitychange'));
    out.state = document.visibilityState;
    out.hidden = document.hidden;
    return out;
  })()`);
  await page.waitForTimeout(300);
  if (inject) {
    check('document.hidden/visibilityState 已被锁定（页面无法改写）', bg.patched === false);
    check('visibilityState 仍为 visible', bg.state === 'visible');
    check('visibilitychange 监听被吞（visEvents=0）', await d(`window.__demo.visEvents`) === 0);
    check('跳出/切屏计数为 0', await d(`window.__demo.hiddenSeen`) === 0);
    check('切屏时无违规 alert 弹窗', !dialogs.some((m) => m.includes('切屏')), dialogs.join('|'));
    check('blur 监听与 onblur 均被吞（blurEvents=0）', await d(`window.__demo.blurEvents`) === 0);
  } else {
    check('[对照] visibilityState 可被改写为 hidden（原生 configurable）', bg.patched === true && bg.state === 'hidden');
    check('[对照] 切屏计数触发（hiddenSeen≥1）', await d(`window.__demo.hiddenSeen`) >= 1);
    check('[对照] 违规 alert 弹出', dialogs.some((m) => m.includes('切屏')));
  }

  /* ---- 2. blur 事件 ---- */
  await d(`window.dispatchEvent(new Event('blur'))`);
  await page.waitForTimeout(100);
  if (inject) check('派发 blur 事件不触发页面逻辑', await d(`window.__demo.blurEvents`) === 0);
  else check('[对照] 派发 blur 触发计数', (await d(`window.__demo.blurEvents`)) >= 1);

  /* ---- 3. 复制/右键/选中 ---- */
  if (inject) {
    // 非 nowcoder 域走"观察模式"：第一次确认其确实 preventDefault，之后拦截
    await d(`document.dispatchEvent(new ClipboardEvent('copy', { bubbles: true, cancelable: true }))`);
    const second = await d(`(() => {
      const e = new ClipboardEvent('copy', { bubbles: true, cancelable: true });
      document.dispatchEvent(e);
      return { prevented: e.defaultPrevented, blocked: window.__demo.copyBlocked };
    })()`);
    check('第二次 copy 事件不再被 preventDefault', second.prevented === false);
    check('观察模式识别出拦截意图（intent.copy≥1）', await d(`window.__WUF__.stats.intent.copy`) >= 1);
  } else {
    const once = await d(`(() => {
      const e = new ClipboardEvent('copy', { bubbles: true, cancelable: true });
      document.dispatchEvent(e);
      return { prevented: e.defaultPrevented, blocked: window.__demo.copyBlocked };
    })()`);
    check('[对照] copy 被 preventDefault', once.prevented === true && once.blocked >= 1);
  }

  const ctxEvt = await d(`(() => {
    const el = document.getElementById('attrTest');
    const e = new MouseEvent('contextmenu', { bubbles: true, cancelable: true });
    el.dispatchEvent(e);
    return { prevented: e.defaultPrevented, inline: window.__demo.inlineCtx, doc: window.__demo.ctxBlocked };
  })()`);
  if (inject) {
    check('内联 oncontextmenu 拦截被阻止（defaultPrevented=false）', ctxEvt.prevented === false);
    check('右键计数不增长', ctxEvt.doc === 0 && ctxEvt.inline === 0, JSON.stringify(ctxEvt));
  } else {
    check('[对照] 右键被拦截（defaultPrevented=true）', ctxEvt.prevented === true);
  }

  const selEvt = await d(`(() => {
    const e = new Event('selectstart', { bubbles: true, cancelable: true });
    document.body.dispatchEvent(e);
    return { prevented: e.defaultPrevented, sel: window.__demo.selBlocked };
  })()`);
  if (inject) {
    // 第一次为观察（被 preventDefault），此后被拦截
    await d(`document.body.dispatchEvent(new Event('selectstart', { bubbles: true, cancelable: true }))`);
    const again = await d(`(() => {
      const e = new Event('selectstart', { bubbles: true, cancelable: true });
      document.body.dispatchEvent(e);
      return e.defaultPrevented;
    })()`);
    check('selectstart 第二次起恢复默认行为', again === false);
  } else {
    check('[对照] selectstart 被拦截', selEvt.prevented === true);
  }

  const userSel = await d(`getComputedStyle(document.getElementById('richText')).userSelect`);
  if (inject) check('CSS user-select 恢复为 text', userSel === 'text', userSel);
  else check('[对照] CSS user-select 为 none', userSel === 'none', userSel);

  /* ---- 4. 快捷键 ---- */
  const keyEvt = await d(`(() => {
    const e = new KeyboardEvent('keydown', { key: 'c', ctrlKey: true, bubbles: true, cancelable: true });
    document.body.dispatchEvent(e);
    return { prevented: e.defaultPrevented, blocked: window.__demo.keyBlocked };
  })()`);
  if (inject) {
    check('Ctrl+C 恢复浏览器默认行为（defaultPrevented=false）', keyEvt.prevented === false);
    // 注：页面监听器仍会"看到"按键（计数+1 属预期），关键是无法再 preventDefault
  } else {
    check('[对照] Ctrl+C 被页面拦截', keyEvt.prevented === true && keyEvt.blocked >= 1);
  }
  if (inject) {
    const f12 = await d(`(() => {
      const e = new KeyboardEvent('keydown', { key: 'F12', bubbles: true, cancelable: true });
      document.body.dispatchEvent(e);
      return e.defaultPrevented;
    })()`);
    check('F12 恢复浏览器默认行为', f12 === false);
  }

  /* ---- 5. 全屏 ---- */
  if (inject) {
    await page.click('#fsBtn');
    await page.waitForTimeout(300);
    const fs = await d(`({
      el: !!document.fullscreenElement,
      outerW: window.outerWidth, outerH: window.outerHeight,
      innerW: window.innerWidth, screenW: window.screen.width, screenH: window.screen.height,
      req: window.__demo.fsRequested, change: window.__demo.fsRealChange, warn: window.__demo.fsWarnShown,
      mm: window.matchMedia('(display-mode: fullscreen)').matches,
      toolbar: Math.round(window.outerHeight - window.innerHeight * (window.outerWidth / window.innerWidth)),
      msg: document.getElementById('fsMsg').textContent,
    })`);
    check('fullscreenElement 伪装为非空', fs.el === true);
    check('requestFullscreen 被静默拦截（未真实全屏）', fs.innerW < fs.screenW, `inner=${fs.innerW} screen=${fs.screenW}`);
    check('outerWidth 伪装为屏幕宽度', fs.outerW === fs.screenW);
    check('outerHeight ≥ screen.height-20（窗口化判定通过）', fs.outerH >= fs.screenH - 20);
    check('工具栏高度探测落在 40~250（pn() 通过）', fs.toolbar >= 40 && fs.toolbar <= 250, String(fs.toolbar));
    check('matchMedia display-mode:fullscreen=false', fs.mm === false);
    check('页面认为全屏正常（无警告）', fs.warn === 0 && fs.msg.includes('全屏正常'), fs.msg);
  } else {
    const fs0 = await d(`({ el: !!document.fullscreenElement })`);
    check('[对照] 初始未全屏', fs0.el === false);
  }

  /* ---- 6. alert 静音 ---- */
  dialogs.length = 0;
  if (inject) {
    await d(`window.alert('检测到切屏行为，本轮练习已被标记！')`);
    check('违规类 alert 被静音（无弹窗）', dialogs.length === 0);
    await d(`window.alert('普通通知')`);
    check('普通 alert 正常弹出', dialogs.length === 1 && dialogs[0] === '普通通知');
  } else {
    await d(`window.alert('检测到切屏行为')`);
    check('[对照] 违规 alert 正常弹出', dialogs.length >= 1);
  }

  /* ---- 7. console ---- */
  const cons = await d(`({
    disabled: window.__demo.consoleDisabled,
    isNoop: console.log === window.__demo.noopRef,
  })`);
  if (inject) {
    check('页面禁用 console.log 失败（仍为原生）', cons.disabled === false && cons.isNoop === false);
    const logs = [];
    const onLog = (m) => logs.push(m.text());
    page.on('console', onLog);
    await d(`console.log('WUF-CONSOLE-OK')`);
    await page.waitForTimeout(200);
    page.off('console', onLog);
    check('console.log 实际输出正常', logs.includes('WUF-CONSOLE-OK'));
  } else {
    check('[对照] console.log 被页面禁用', cons.disabled === true && cons.isNoop === true);
  }

  /* ---- 8. 退出意图 / beforeunload / debugger 剥离 ---- */
  if (inject) {
    await d(`document.dispatchEvent(new MouseEvent('mouseleave', { bubbles: false }))`);
    check('document mouseleave（退出意图）被吞', await d(`window.__demo.exitIntent`) === 0);
    check('beforeunload 注册被吞', await d(`(window.__WUF__.stats.dropped['beforeunload'] || 0)`) >= 1);
    check('visibilitychange 注册被吞', await d(`(window.__WUF__.stats.dropped['visibilitychange'] || 0)`) >= 1);
    // 字面量拆开写，避免被脚本的 eval 包装器先剥掉 'debugger'
    const dbgFree = await d(`(() => {
      try {
        const body = 'var a=1; deb' + 'ugger; return a+1;';
        const f = new Function(body);
        const src = f.toString();
        return { ok: f() === 2, srcHas: src.includes('deb' + 'ugger') };
      } catch (e) { return { ok: false, srcHas: String(e) }; }
    })()`);
    check("new Function('debugger') 被剥离", dbgFree.ok === true && dbgFree.srcHas === false, JSON.stringify(dbgFree));
    check('document.hasFocus() 恒真', await d(`document.hasFocus()`) === true);
  } else {
    await d(`document.dispatchEvent(new MouseEvent('mouseleave'))`);
    check('[对照] 退出意图弹窗触发', await d(`window.__demo.exitIntent`) >= 1);
  }

  /* ---- 9. 诚信粘贴守卫（链路②/③） ---- */
  const paste = await d(`(() => {
    const dt = new DataTransfer();
    dt.setData('text/plain', 'EXTERNAL_ANSWER');
    const e = new ClipboardEvent('paste', { clipboardData: dt, bubbles: true, cancelable: true });
    document.getElementById('answer').dispatchEvent(e);
    return { prevented: e.defaultPrevented, read: window.__demo.pasteGuardRead, blocked: window.__demo.pasteBlocked };
  })()`);
  if (inject) {
    check('守卫读到的粘贴内容为空串（getData 伪装）', paste.read === '', JSON.stringify(paste));
    check('粘贴未被拦截（defaultPrevented=false）', paste.prevented === false);
    check('无拦截计数、无指纹上报载荷', paste.blocked === 0 && await d(`window.__demo.lastFingerprint`) === null);
    check('包裹层守卫监听被丢弃（wrapperGuardFired=0）', await d(`window.__demo.wrapperGuardFired`) === 0);
    check('链路③ document 捕获级粘贴追踪被丢弃', await d(`window.__demo.codePastedTracked`) === false);
    check('守卫监听丢弃计入统计', await d(`(window.__WUF__.stats.dropped['paste@wrapper']||0)`) >= 2
      && await d(`(window.__WUF__.stats.dropped['paste@doc']||0)`) >= 1);
  } else {
    check('[对照] 守卫读到粘贴原文', paste.read === 'EXTERNAL_ANSWER');
    check('[对照] 粘贴被拦截并计算指纹', paste.prevented === true && paste.blocked >= 1
      && (await d(`window.__demo.lastFingerprint`)) === '15:L_ANSWER');
    check('[对照] 包裹层守卫触发', await d(`window.__demo.wrapperGuardFired`) >= 1);
    check('[对照] 链路③追踪触发', await d(`window.__demo.codePastedTracked`) === true);
  }

  const rich = await d(`(() => {
    const dt = new DataTransfer();
    dt.setData('text/plain', 'RICH_OK');
    const e = new ClipboardEvent('paste', { clipboardData: dt, bubbles: true, cancelable: true });
    document.getElementById('richEditor').dispatchEvent(e);
    return { insert: window.__demo.richInsert, wrapGuard: window.__demo.richWrapperGuardFired };
  })()`);
  if (inject) {
    check('contenteditable 编辑器仍读到真实剪贴板并插入', rich.insert === 'RICH_OK');
    check('富文本包裹层守卫被丢弃（不弹窗不上报）', rich.wrapGuard === 0);
  } else {
    check('[对照] 富文本编辑器正常插入', rich.insert === 'RICH_OK');
    check('[对照] 富文本包裹层守卫触发', rich.wrapGuard >= 1);
  }

  const monaco = await d(`(() => {
    const dt = new DataTransfer();
    dt.setData('text/plain', 'MONACO_OK');
    const e = new ClipboardEvent('paste', { clipboardData: dt, bubbles: true, cancelable: true });
    document.getElementById('hiddenInput').dispatchEvent(e);
    return window.__demo.monacoRead;
  })()`);
  if (inject) check('隐藏输入区（Monaco 式）粘贴监听不受影响', monaco === 'MONACO_OK', monaco);
  else check('[对照] 隐藏输入区粘贴正常', monaco === 'MONACO_OK');

  const drop = await d(`(() => {
    const dt = new DataTransfer();
    dt.setData('text/plain', 'DROP_TEXT');
    const e = new DragEvent('drop', { dataTransfer: dt, bubbles: true, cancelable: true });
    document.getElementById('answer').dispatchEvent(e);
    return { prevented: e.defaultPrevented, read: window.__demo.dropRead, blocked: window.__demo.dropBlocked };
  })()`);
  if (inject) {
    check('拖放守卫读到的内容为空串', drop.read === '' || drop.read === null, JSON.stringify(drop));
    check('拖放未被拦截', drop.prevented === false && drop.blocked === 0);
  } else {
    check('[对照] 拖放被守卫拦截', drop.prevented === true && drop.blocked >= 1 && drop.read === 'DROP_TEXT');
  }

  if (inject) {
    await d(`showPasteDialog()`);
    await page.waitForTimeout(800);
    const dlg = await d(`document.getElementById('pasteDlgWrap').style.display`);
    check('残余"请勿粘贴外部内容"弹窗被自动关闭', dlg === 'none', String(dlg));
  }

  /* ---- 10. PrintScreen 截图拦截（截图自由） ----
   * 合成 keydown+keyup(key='PrintScreen', keyCode=44) 于 document.body：
   * 站点在 window捕获/document冒泡/body.onkeyup 三条路径上清空剪贴板+弹违规层。
   * 对照组应三条路径全部触发；实验组应完全看不见事件，剪贴板写入兜底也应被吞。 */
  const ps = await d(`(() => {
    simPS();
    return { seen: window.__demo.psSeen, wipe: window.__demo.psWipeCalls,
             warn: window.__demo.psWarnShown, popup: document.getElementById('psPopup').style.display };
  })()`);
  if (inject) {
    check('PrintScreen 事件对页面完全不可见（三条监听路径 psSeen=0）', ps.seen === 0, JSON.stringify(ps));
    check('剪贴板清空未发生（psWipeCalls=0）', ps.wipe === 0);
    check('无截图违规警告弹层', ps.warn === 0 && ps.popup !== 'block');
    check('事件阻断计入统计（keydown+keyup）', await d(`(window.__WUF__.stats.stopped['printscreen']||0)`) >= 2);
    // 兜底层：模拟"绕过事件系统/先于脚本注册"的清空 —— PrintScreen 后短窗口内直接调用剪贴板写入
    const wipeGuard = await d(`(async () => {
      const e = new KeyboardEvent('keyup', { key: 'PrintScreen', bubbles: true });
      document.body.dispatchEvent(e);
      const ta = document.createElement('textarea'); document.body.appendChild(ta); ta.select();
      let execRet = null;
      try { execRet = document.execCommand('copy'); } catch (err) { execRet = 'err'; }
      ta.remove();
      let apiRet = 'none';
      try { await navigator.clipboard.writeText(''); apiRet = 'resolved'; } catch (err) { apiRet = 'rejected:' + err.name; }
      return { execRet, apiRet, stat: (window.__WUF__.stats.stopped['clipboard-wipe'] || 0) };
    })()`);
    check('兜底：PrintScreen 后 execCommand(copy) 与 clipboard.writeText 均被吞', wipeGuard.stat >= 2, JSON.stringify(wipeGuard));
    check('兜底：execCommand 伪报成功（true）且 writeText 不触碰原生（无权限错误）',
      wipeGuard.execRet === true && wipeGuard.apiRet === 'resolved', JSON.stringify(wipeGuard));
    // 时间窗过期后恢复透传，不影响页面正常复制功能
    await page.waitForTimeout(900);
    const after = await d(`(() => {
      const ta = document.createElement('textarea'); ta.value = 'X'; document.body.appendChild(ta); ta.select();
      try { document.execCommand('copy'); } catch (err) {}
      ta.remove();
      return window.__WUF__.stats.stopped['clipboard-wipe'] || 0;
    })()`);
    check('PrintScreen 时间窗过后剪贴板写入恢复透传', after === wipeGuard.stat, `${wipeGuard.stat} -> ${after}`);
  } else {
    check('[对照] PrintScreen 三条监听路径全部触发（psSeen≥3）', ps.seen >= 3, JSON.stringify(ps));
    check('[对照] 剪贴板清空被执行（psWipeCalls≥3）', ps.wipe >= 3);
    check('[对照] 截图违规警告弹出', ps.warn >= 1 && ps.popup === 'block');
  }

  /* ---- 11. 键盘行为记录器（按键自由） ----
   * demo 在 window 上记录一切击键（牛客真实实现过滤单个字母数字，demo 为上限复刻）。
   * 编辑区外派发 Ctrl+Shift+S / Alt+Shift+T / a：对照组全被记录；
   * 实验组（keyFreedom=on）应完全隐身；在输入框内派发则必须放行（打字/编辑器不受影响）。 */
  const keys = await d(`(() => {
    const mk = (opts) => new KeyboardEvent('keydown', Object.assign({ bubbles: true, cancelable: true }, opts));
    const before = window.__demo.keyLog.length;
    document.body.dispatchEvent(mk({ key: 's', code: 'KeyS', ctrlKey: true, shiftKey: true }));
    document.body.dispatchEvent(mk({ key: 't', code: 'KeyT', altKey: true, shiftKey: true }));
    document.body.dispatchEvent(mk({ key: 'a', code: 'KeyA' }));
    const outside = window.__demo.keyLog.slice(before);
    document.getElementById('recInput').dispatchEvent(mk({ key: 'a', code: 'KeyA' }));
    document.getElementById('recInput').dispatchEvent(mk({ key: 's', code: 'KeyS', ctrlKey: true }));
    const insideCount = window.__demo.keyLog.length - before - outside.length;
    return { outside, insideCount };
  })()`);
  if (inject) {
    check('编辑区外击键对记录器完全隐身（组合键+单键 0 条）', keys.outside.length === 0, JSON.stringify(keys.outside));
    check('输入框内击键照常放行（含站点自身 Ctrl 快捷键）', keys.insideCount === 2, JSON.stringify(keys));
    check('击键隐身计入统计', await d(`(window.__WUF__.stats.stopped['keystroke']||0)`) >= 3);
    // PrintScreen 已由"截图自由"更早阻断（8c 在 8d 之前注册），不应重复计数
    check('PrintScreen 走截图自由路径（keystroke 不含 PS 重复计数）',
      await d(`(window.__WUF__.stats.stopped['printscreen']||0)`) >= 2);
  } else {
    check('[对照] 组合键被记录器捕获', keys.outside.some((k) => k.includes('Ctrl+') && k.includes('Shift+')), JSON.stringify(keys.outside));
    check('[对照] Alt 组合键被记录器捕获', keys.outside.some((k) => k.includes('Alt+')), JSON.stringify(keys.outside));
    check('[对照] 单键也被记录（上限复刻）', keys.outside.includes('a'), JSON.stringify(keys.outside));
    check('[对照] 输入框内击键同样被记录', keys.insideCount === 2, JSON.stringify(keys));
  }

  /* ---- 11b. VK_PACKET 注入字符（SendInput 程序代打） ----
   * 复刻实测指纹：keydown key='图' code='' keyCode=231（非物理键盘所能产生）。
   * 对照组：编辑区内也会被记录器明文捕获（绕过"单字母数字"过滤）；
   * 实验组：编辑区内也隐身，且不拦默认行为（注入文本仍经 input 事件插入）。 */
  const inj = await d(`(() => {
    const e = new KeyboardEvent('keydown', { key: '图', code: '', bubbles: true, cancelable: true });
    try { Object.defineProperty(e, 'keyCode', { value: 231 }); } catch (err) {}
    const before = window.__demo.keyLog.length;
    document.getElementById('recInput').dispatchEvent(e);
    return { prevented: e.defaultPrevented, added: window.__demo.keyLog.length - before };
  })()`);
  if (inject) {
    check('VK_PACKET 注入字符在编辑区内也被隐身', inj.added === 0, JSON.stringify(inj));
    check('注入隐身不拦默认行为（文本仍可插入）', inj.prevented === false, JSON.stringify(inj));
    check('注入隐身计入统计', await d(`(window.__WUF__.stats.stopped['keystroke-injected']||0)`) >= 1);
  } else {
    check('[对照] 注入字符被记录器明文捕获', inj.added === 1, JSON.stringify(inj));
  }

  await browser.close();
}

// 白名单场景：预置 site-off 标记后注入，脚本应在安装任何 hook 前整体退出
async function runWhitelistScenario() {
  console.log(`\n========== WHITELIST：站点已加入白名单（脚本应整体不生效） ==========`);
  const browser = await chromium.launch();
  const ctx = await browser.newContext({
    viewport: { width: 900, height: 640 },
    screen: { width: 1920, height: 1080 },
  });
  const seed = `localStorage.setItem('gm:site-off:' + location.hostname, 'true');`;
  await ctx.addInitScript(gmShim + '\n' + seed + '\n' + userjs);
  const page = await ctx.newPage();
  await page.goto(demoUrl);
  await page.waitForTimeout(400);

  const d = (expr) => page.evaluate(expr);
  check('白名单站点：脚本完全未生效（无 __WUF__）', await d(`typeof window.__WUF__`) === 'undefined');
  check('白名单站点：user-select 仍为 none', await d(`getComputedStyle(document.getElementById('richText')).userSelect`) === 'none');
  const copy = await d(`(() => {
    const e = new ClipboardEvent('copy', { bubbles: true, cancelable: true });
    document.dispatchEvent(e);
    return e.defaultPrevented;
  })()`);
  check('白名单站点：copy 仍被页面拦截（限制原生保留）', copy === true);
  const patched = await d(`(() => {
    try {
      Object.defineProperty(document, 'hidden', { get: () => true, configurable: true });
      return true;
    } catch (e) { return false; }
  })()`);
  check('白名单站点：document.hidden 未被锁定', patched === true);

  await browser.close();
}

await runScenario('CONTROL：不注入脚本（确认限制原生生效）', { inject: false });
await runScenario('TREATMENT：注入 web-ux-freedom.user.js', { inject: true });
await runWhitelistScenario();

const failed = results.filter((r) => !r.pass);
console.log(`\n===== 汇总: ${results.length - failed.length}/${results.length} 通过 =====`);
if (failed.length) {
  console.log('失败项:');
  failed.forEach((f) => console.log('  ❌ ' + f.name + (f.detail ? ' — ' + f.detail : '')));
  process.exit(1);
}
