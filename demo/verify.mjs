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
  if (inject) await ctx.addInitScript(gmShim + '\n' + userjs);
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

  await browser.close();
}

await runScenario('CONTROL：不注入脚本（确认限制原生生效）', { inject: false });
await runScenario('TREATMENT：注入 web-ux-freedom.user.js', { inject: true });

const failed = results.filter((r) => !r.pass);
console.log(`\n===== 汇总: ${results.length - failed.length}/${results.length} 通过 =====`);
if (failed.length) {
  console.log('失败项:');
  failed.forEach((f) => console.log('  ❌ ' + f.name + (f.detail ? ' — ' + f.detail : '')));
  process.exit(1);
}
