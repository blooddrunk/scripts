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
| 截图自由 | PrintScreen / Alt+PrintScreen 截图不再被"截图后清空剪贴板"式拦截抹掉，也不触发违规上报 | document-start 抢先在 window/document 捕获期对 PrintScreen 事件（key/code=`PrintScreen`、keyCode=44）`stopImmediatePropagation`，站点监听器（含 `onkeyup` 属性式）根本收不到事件；兜底：PrintScreen 后 800ms 内吞掉 `Clipboard.write/writeText` 与 `execCommand('copy'/'cut')` |
| 按键自由 | 编辑区外的击键（组合键/功能键）不再进键盘行为记录台账、不再被上报，编辑区外的快捷键拦截器也一并失明；VK_PACKET 注入字符（SendInput 程序代打的非 ASCII 文本）在编辑区内也隐身，不会绕过过滤被明文记录 | document-start 抢先在 window/document 捕获期对"目标不是 input/textarea/select/contenteditable"的键盘事件 `stopImmediatePropagation`；VK_PACKET（keyCode 231 / 无 code 的单字符 key，物理键盘不可能产生）在编辑区内也一并隐身——只 stopPropagation 不 preventDefault，注入文本仍经 input 事件正常插入。编辑区内其余击键照常放行，站点绑在输入框上的自身快捷键（Ctrl+Enter 提交、Monaco/CodeMirror 按键映射）不受影响。会连带屏蔽站点在编辑区外的正常快捷键（如视频站空格暂停），故全局默认关、nowcoder 默认开 |

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

### Windows 剪贴板历史（Win+V）：能力边界（分析结论）

问题：页面能干扰 Win+V 的历史列表吗？在输入框按 Win+V 选一条历史点填入会怎样？

结论：**动不了历史列表本身，但"点选填入"会以普通粘贴的身份撞上全部粘贴守卫。**
（证据普查详见分析报告第 11 章）

- **历史列表对网页完全不可达**：剪贴板历史由系统的 Clipboard User Service
  （`cbdhsvc_*`）在用户会话层管理，网页没有任何 API 能枚举/删除/清空它。连
  原生 Win32 `EmptyClipboard()` 都只清"当前剪贴板"槽位、清不掉历史（Windows
  也没给第三方程序提供删除单条历史的公开 API，只有设置里"清除数据"或面板内
  删除）；历史默认上限 25 条（不含固定项）。
- **页面做的"清空剪贴板"（`writeText('')` / 隐藏空 textarea +
  `execCommand('copy')`）只改写当前槽位**；这次写入本身反而会被历史记成一条
  新条目（空串通常不显示）。因此从 Win+V 点选恰好是"清当前剪贴板"式拦截的
  天然克星：点击条目时 OS 把内容**重新写回**剪贴板再粘贴，之前槽位被清空
  无关紧要——真正决定成败的只有 paste 事件层守卫。
- **点选历史 = 一次与 Ctrl+V 完全等价的粘贴**：点击条目时 OS 写回剪贴板并向
  焦点控件合成粘贴（默认"自动粘贴"；关闭该开关或应用不支持则仅复制回剪贴板）。
  浏览器视角是一个普通 `isTrusted` paste 事件（clipboardData 在分发时从 OS
  剪贴板读取），**事件层无来源标志，与手动 Ctrl+V 不可区分**。于是上文三条
  链路照常反应：守卫②读 `getData` 与 5 分钟台账比对（历史内容几乎必然判
  "外部"）→ `preventDefault` + 弹窗 + 指纹上报，文本插不进；追踪③照记；
  编程题编辑器（Monaco/CodeMirror）不在守卫②挂载范围，点选填入畅通。
- **点选图片条目**：paste 事件无 `text/plain`，守卫只读纯文本——既不拦也不报
  （同上方"边界说明"），但纯文本编辑器也接不住图片，无实际意义。
- **页面唯一"感觉得到"的间接信号**：Win+V 面板是系统窗口，会取得键盘焦点 →
  页面 `window.blur` 触发 → 计入失焦/切屏检测（停留超过 `blurDelaySecond`
  记一次，但页面不知道你开的是 Win+V）；以及事后从 paste 事件拿到的内容
  指纹（去空白长度+末 8 字符）。
- **理论骚扰上限**：恶意页可高频写**非空**内容把你需要的条目挤出 25 条窗口
  ——牛客没有此行为（见下节，其清空仅挂在 PrintScreen 按键上）。
- 开启"粘贴自由"后，Win+V 点选与 Ctrl+V 无差别，均可正常填入。
- **勘误**：此前"编程题输入时清空剪贴板"的说法未在静态快照中得到证实——主包
  20 处 `writeText` 调用点逐一普查，确认的清空触发点是 PrintScreen 按键
  （见下节）；若线上确有"输入即清空"，应来自快照之外的动态注入脚本。

### 截图快捷键：网页能拦截什么、拦不了什么（分析结论）

先说结论：**网页拦不住操作系统截图这个动作本身，但能把截图"事后作废"**——
这正是在线考试/监考类前端"拦截 PrintScreen"的真实手法：

- **PrintScreen / Alt+PrintScreen**：Windows 在 OS 层截图并放入剪贴板，浏览器
  只是事后收到 `keydown/keyup`（key=`PrintScreen`，keyCode=44），页面对其
  `preventDefault` 毫无作用。但页面可以在 keyup 里立刻**清空剪贴板**
  （`navigator.clipboard.writeText('')` 或隐藏空 textarea + `execCommand('copy')`），
  把刚截好的图片抹掉，并弹违规警告、上报——体感上就是"截图被拦截了"。
  → **截图自由**两层瓦解：事件期抢先 `stopImmediatePropagation` 让站点监听器
  （window 捕获/document 冒泡/`onkeyup` 属性式）根本收不到按键；兜底在按键后
  800ms 窗口内吞掉一切剪贴板写入，连"清空"这个动作本身都不会发生。
- **Win+PrintScreen**：Windows 直接把文件写入 `图片\屏幕截图`，网页无法阻止、
  也无法删除文件；本功能能做到的只是让站点"看不见"这次按键（不触发违规上报）。
- **Win+Shift+S（截图工具）/第三方截图**：全程 OS 层，页面最多通过
  blur/visibilitychange 察觉（已由"防切屏 + 窗口失焦伪装"覆盖）。

### 键盘行为记录链路（vm 监控）与对抗

bundle 中的 vm 组件（`actionKeydown`）在 window 和每个动态加载的 iframe 上监听
`keydown`，把击键攒进 `keyboardDataList`，**每 5 秒**批量 `sendVirtualData` 上报
（与鼠标轨迹 `mouseDataList`、粘贴记录、分辨率、CPU 核数合并成一个请求）：

- **记**：所有带 Ctrl/Alt/Win(Meta) 的组合键（含再叠 Shift，如 `"Shift + Control + S"`）、
  Shift+非字母数字、无修饰键但非单字母数字的键（Enter/Tab/空格/标点/方向键/F 键/
  **PrintScreen**/中文输入法的 `Process`），每条附 `TimeDeltaMs` 击键间隔；长按不去重。
- **刻意不记**（内容脱敏）：单个字母/数字（正常打字内容）、Shift+字母（大写）——
  实现上先记裸 `Shift` 再在组成大写时弹出合并。
- 也就是说：**打进去的字不上报，但用了哪些组合键/功能键、以什么节奏按键全量上报**。
  在页面聚焦时触发外部工具热键（Ctrl+Shift+S、Alt+Shift+T 等）虽然工具正常工作，
  但组合键会进监控台账。

**对抗（按键自由）**：与截图自由同模式，document-start 抢先在捕获期对
"目标不是输入框/可编辑区"的键盘事件 `stopImmediatePropagation`——记录器与
编辑区外的快捷键拦截器统统收不到事件；编辑区内（打字、IME、站点绑在输入框
上的自身快捷键）照常放行。

**VK_PACKET 侧门与封堵（v1.4.1）**：实测发现程序代打（SendInput 注入字符，
如截图 OCR 回填、短语工具）产生 `keydown key='图' code='' keyCode=231`——
物理键总有 code、IME 走 229/"Process"，这类事件非物理键盘所能产生；而"图"
不匹配"单字母数字"过滤，会被**明文**写进台账（内容脱敏对注入字符失效）。
现已在编辑区内对这类 keydown 一并隐身（只 stopPropagation 不 preventDefault，
注入文本仍经 input 事件正常插入，工具功能不受影响）。另：全局热键工具若用
RegisterHotKey 注册，主键 keydown 被系统截走，记录器只能看到形如
`"Alt + Shift"` 的修饰键残影（与输入法切换无异），且它不监听 keyup，连残缺
指纹都拼不全。**网页无法干扰其他 App 的全局热键**（RegisterHotKey
被系统直接截走、低级键盘钩子在浏览器之前收到原始按键），此功能解决的是
"被看见并上报"，不是"被拦截"——后者在系统层面就不可能发生。

## 验证

```bash
npm install playwright && npx playwright install chromium
node web-ux-freedom/demo/verify.mjs
```

`demo/restrictions-demo.html` 复刻了上述全部限制（含诚信粘贴守卫的三种挂载
结构、PrintScreen 三条监听路径上的清空剪贴板拦截、键盘行为记录器与 VK_PACKET
注入字符），`demo/verify.mjs` 以对照组（不注入，确认限制原生生效）+ 实验组
（注入脚本，粘贴自由与按键自由通过存储覆盖开启，同时验证菜单开关的持久化路径）
+ 白名单组（站点已禁用，确认脚本整体不生效）三跑自动断言，当前 90/90 通过。

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
