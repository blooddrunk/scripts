# 网页体验自由 (Web UX Freedom)

一个通用 Tampermonkey 脚本，解除网站前端对用户体验的限制。基于对
nowcoder.com 考试前端（`references/test.html`）的逆向分析编写，
默认适配所有网站，每个功能可按站点独立开关。

## 安装

1. 浏览器安装 [Tampermonkey](https://www.tampermonkey.net/) 扩展
2. Tampermonkey → 管理面板 → 「+」新建脚本，粘贴 `web-ux-freedom.user.js`
   全部内容并保存（Ctrl+S）
3. 刷新目标网站即可生效；点击 Tampermonkey 图标可见全部功能开关

## 功能（油猴菜单可逐项开关，设置按域名保存）

| 开关 | 作用 | 原理 |
|---|---|---|
| 防切屏 | 切换标签页/最小化不再被记录 | `document.hidden`/`visibilityState` 恒定伪装 + 拦截 `visibilitychange` 监听注册 |
| 窗口失焦伪装 | 点击其他软件/副屏不触发上报 | 拦截 `window` 的 `blur`/`focus` 监听与 `onblur` 赋值，`hasFocus()` 恒真 |
| 全屏状态伪装 | 网站认为你一直处于全屏 | `fullscreenElement` 恒返回非空（含 4 种浏览器前缀）+ 拦截 `fullscreenchange` |
| 拦截强制全屏 | 「开始考试」不再真的全屏 | `requestFullscreen` 对 documentElement/body 静默返回成功（视频播放器全屏不受影响） |
| 窗口尺寸伪装 | 骗过"窗口化/devtools"探测 | `outerWidth/outerHeight` 智能伪装，同时满足 outer≥screen-20 与"工具栏高度∈[40,250]"两类探测 |
| 恢复复制/剪切 | 题目内容可选中复制 | 三重 `:not(#id)` 拉满 CSS 特异性压过 `user-select:none` + 拦截 document 级 `copy/cut` |
| 恢复右键菜单 | 右键可用 | 捕获期 `stopImmediatePropagation`，可拦内联 `oncontextmenu="return false"` |
| 恢复文字选中 | 鼠标划词/拖拽可用 | 拦截 `selectstart`/`dragstart` 阻断 |
| 恢复浏览器快捷键 | Ctrl+C/U/S/F、F12、F5、Ctrl+F 可用 | 命中受保护组合时把页面的 `preventDefault` 变空操作（不影响网站自身快捷键如 Ctrl+Enter 提交） |
| 解除离开拦截 | 关闭/刷新页面不再弹确认 | 拦截 `beforeunload` 注册与 `onbeforeunload` 赋值 |
| 阻止鼠标移出弹窗 | "退出意图"营销弹窗失效 | 拦截 document 级 `mouseleave/mouseenter` |
| 反反调试 | console 可用、动态 debugger 失效 | 锁定 console 属性；`Function`/`eval` 构造时剥离 `debugger` 语句 |
| 解除页面缩放限制 | 移动端 viewport 可缩放 | 重写 `meta[name=viewport]` 的 `user-scalable/maximum-scale` |
| 解锁页面滚动 | 被弹窗锁死时恢复滚动 | `html,body{overflow:auto!important}`（默认关） |
| 静音违规警告弹窗 | 「检测到切屏」类 alert 不再出现 | 关键词匹配的 alert/confirm 拦截（confirm 默认选"取消"避免误触交卷流程） |
| 粘贴自由 | "禁贴外部内容"诚信守卫失效、粘贴/拖放行为不再被追踪 | 注册期丢弃包裹层守卫与顶层 document 捕获级 paste 追踪；事件期对可见输入框把 `getData` 伪装为空串 + `preventDefault` 空操作（Monaco/CodeMirror 隐藏输入区与富文本编辑器自动跳过，不受影响；全局默认关，nowcoder 默认开） |

**站点白名单（整站禁用）**：菜单第一项「🚫 加入白名单——本站禁用脚本」可将当前
站点整体禁用——脚本在安装任何 hook 之前退出，页面上不留任何痕迹；此后本站菜单
只剩「▶️ 移出白名单——本站启用脚本」，点击即刷新恢复。白名单按域名保存，与各
功能开关、"重置本站设置"互不影响（重置不会移出白名单）。

## 针对牛客的具体对抗（分析结论）

牛客考试前端（cts/testV3）的实际检测栈与本脚本的对应关系：

- **跳出检测**：`document` 上监听 `visibilitychange` + `window` 上监听
  `blur/focus`，离开 `jumpOutTimeLimit` 秒记一次，超过 `jumpOutCountLimit`
  次强制收卷 → 监听注册被拦截，永远不会触发
- **强制全屏**：`documentElement.requestFullscreen()` + 四种前缀的
  `fullscreenchange` 监听 + "全屏状态失步修复"循环（判定条件：
  `fullscreenElement` 存在 && `outerWidth/outerHeight ≥ screen-20` &&
  `matchMedia('(display-mode: fullscreen)')` 不为 true &&
  `outerHeight - innerHeight*(outerWidth/innerWidth) ∈ [40,250]`）
  → 四项探测全部被伪装满足，修复循环不会启动
- **禁复制**：document 级 `copy/cut`、`selectstart/dragstart` 拦截、
  内联 `style.user-select:none`（题目富文本容器）、kindeditor 禁右键
  → CSS + 注册拦截 + 捕获期兜底三层瓦解
- **反调试**：console 禁用 + outer/inner 尺寸差探测 → console 锁定 + 尺寸伪装

### 剪贴板监视链路（AI 实战题"诚信作答"体系）与对抗

逆向发现的三条链路（详见 bundle 中 `handleExternalCopy`/`M0e`/`codeBeforePasted`）：

1. **复制内容捕获**：`document` 级 `copy/cut` 读 `getSelection().toString()`，
   把你复制的原文 postMessage 转发进答题 iframe（维持"站内复制台账"），
   并以 qid/paperId 记日志 → 已被"恢复复制"掐断（监听注册即丢弃）。
2. **粘贴守卫**：`forbidExternalPaste` 开启时，挂在答题 textarea（直接绑定）与
   非 contenteditable 包裹层（Vue `on:{paste}` 落点）上的守卫读取
   `getData("text/plain")`，与 5 分钟内站内复制台账（去空白子串比对）不匹配
   即判定"外部内容"：`preventDefault` 阻止粘贴 + 弹"请勿粘贴外部内容，诚信作答"
   + 上报指纹（去空白长度+末 8 字符）。**对抗（粘贴自由）**：注册期丢弃包裹层
   守卫；事件期对"用户可见的" input/textarea 把 `getData` 伪装成恒返回空串、
   `preventDefault` 变空操作——守卫以为什么都没粘，原生粘贴照常插入；
   残余弹窗自动点关闭。
3. **代码编辑器粘贴追踪**：顶层 `document` 捕获级 `paste` 监听打标记
   `codePasted` 并快照粘贴前全文（`codeBeforePasted`，diff 即得粘贴内容）
   → 注册期丢弃（仅顶层窗口；iframe 内编辑器如 kindeditor 不受影响）。

**边界说明**：这些守卫只读 `text/plain`——剪贴板里的**图片**对整条链路不可见
（`getData("text/plain")` 返回空串），粘贴图片既不会被拦截也不会被读走。
另有一条 `writeText` 后 `readText` 读回比对的复制自检，仅在用户授予过
`clipboard-read` 权限时运行。

## 验证

```bash
npm install playwright && npx playwright install chromium
node demo/verify.mjs
```

`demo/restrictions-demo.html` 复刻了上述全部限制（含诚信粘贴守卫的三种挂载
结构），`demo/verify.mjs` 以对照组（不注入，确认限制原生生效）+ 实验组
（注入脚本，粘贴自由通过存储覆盖开启，同时验证菜单开关的持久化路径）
+ 白名单组（站点已禁用，确认脚本整体不生效）三跑自动断言，
当前 68/68 通过。

## 已知限制（诚实声明）

- **只解除前端体验限制**。服务端行为（判题、计时、心跳、成绩、AI 监考
  对摄像头画面的分析）不受影响也无法被本脚本影响。
- 打包进网站 bundle 的**静态** `debugger` 语句无法从 JS 层移除（动态
  构造的可以）。应对：DevTools 断点处右键 "Never pause here"，或点击
  停用断点按钮。
- 谷歌文档 / Figma / Notion 等依赖自定义右键与剪贴板的 Web 应用已在
  内置名单中自动关闭相关功能；其他网站如遇误伤，菜单里单独关闭即可。
- 非 nowcoder 站点的复制拦截采用"观察模式"：首次复制若确认网站真的在
  阻止，从第二次起恢复；nowcoder 等已知站点直接恢复。
- 主要在 Chrome/Edge + Tampermonkey 上测试；Firefox/Violentmonkey 下
  原型补丁可能部分失效（脚本会自动退化为实例级补丁，核心功能保留）。

## 使用场景

为你自己的练习、自学、模拟场景恢复浏览器本来的行为。正式监考考试
请遵守相应规则——脚本挡不住服务端记录，也不该用于对抗真实考试纪律。
