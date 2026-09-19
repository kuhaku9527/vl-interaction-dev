# 全屏（Fullscreen）实现审计 — 2026-09-19

> 端点身份：**前端** ｜ 方法：Chrome headless + CDP **实测几何 + 命中测试**（非纯读码）
> 复现脚本：`scripts/inspect-fullscreen.mjs`（结构剖析）、`scripts/inspect-fullscreen-hit.mjs`（层叠/命中）、`scripts/inspect-fullscreen-fix.mjs`（候选修复验证）

## 一、它到底是什么（读懂了）

**不是新页面，不是浏览器 Fullscreen API。** 全屏 = 给 `#videoCard` 加一个 class：

```js
// index.html:2213
function toggleFullscreen() {
    videoCard.classList.toggle('fullscreen');
    if (videoCard.classList.contains('fullscreen')) {
        fullscreenIcon.setAttribute('data-lucide', 'minimize');
        fullscreenPromptOverlay.appendChild(promptEditor);   // ★ 把输入栏「搬家」到这里
        syncVlmToFullscreen();
    } else { ... restorePromptEditorHome(); }
    lucide.createIcons();
}
```

`#videoCard.fullscreen` → `position:fixed; inset:0; z-index:200`。

### 参与全屏的 5 个 DOM 单元

| 元素 | 常规态 | 全屏态 | 作用 |
|---|---|---|---|
| `.video-card` | `order:1`，栅格左列 | `position:fixed;inset:0;z-index:200` | 容器，被变成浮层 |
| `.video-tools` | 右上角 | 同位置 | 装 `#quickCameraBtn` + `.fullscreen-btn` |
| `#fullscreenPromptOverlay` | `display:none` | `display:block` bottom-left **640×72** | 输入栏的**新家** |
| `#fullscreenVlmOverlay` | `display:none` | `display:block` top-left **480×41** | 全屏下的 VLM 输出面 |
| `#promptEditor` | 在 `.prompt-editor-inline` 内 | **物理搬到** `#fullscreenPromptOverlay` | 输入栏本体 |

### 进入/退出

- 进入：点 `.fullscreen-btn`（`onclick="toggleFullscreen()"`）
- 退出：**只有两条路** —— ① 同一个按钮（图标变 minimize）；② `Escape`（`index.html:2288`）

## 二、你问的「很多功能应该不在这里，应该在设置界面」

### 实测结论：**没有任何一个搬到设置页。**

设置页 9 个面板（`about/advanced/agent/appearance/input/memory/services/voice/wiki`）内，
这 6 个 id **出现 0 次**：

| id | 功能 | 设置页 |
|---|---|---|
| `btListenBtn` | Jarvis 唤醒词监听 | ❌ 不存在 |
| `liveEnrollBtn` | 注册声音（Addressee 过滤） | ❌ 不存在 |
| `liveVideoSource` | 画面来源（屏幕/摄像头） | ❌ 不存在 |
| `liveProactiveToggle` | 主动搭话 | ❌ 不存在 |
| `promptPresetBtn` | 快速预设 | ❌ 不存在 |
| `promptPresetMenu` | 预设菜单 | ❌ 不存在 |

`styles.css:4848-4850` 的注释写着「黄框放后台（**设置页**）」，**但从未实施** ——
这些控件只是被 CSS 在常规态隐藏，从未在设置页建立等价入口。

> ⚠️ 于是 `btListenBtn` 的处境最尴尬：CSS 注释说它「与顶栏 jarvis chip 功能重复」，
> 但 `#jarvisStatus`（index.html:167）是**只读状态角标**，不是开关。它没有等价入口。

### 你要的功能清单（这就是"我忘了有什么功能"的答案）

1. **Jarvis 唤醒词监听** `#btListenBtn` — 与 Live 常驻模式二选一的 radio
2. **实时（Live 常驻模式）** `#liveModeBtn`
3. **注册声音** `#liveEnrollBtn` — 声纹注册，只响应你
4. **开画面** `#liveVideoBtn` + **来源选择** `#liveVideoSource`（屏幕/摄像头）
5. **主动搭话** `#liveProactiveToggle` — 无语音时周期性看画面
6. **快速预设** `#promptPresetBtn` → 4 条预设文案
7. **视频 / 音频输入** `#camBtn`（打开 captureOverlay）
8. **按住说话** `#speechBtn`、**发送** `#promptSendBtn`

## 三、根因：混乱是「DOM 搬家」造成的连锁故障

### 🔴 问题 1 —— 父节点一变，隐藏契约集体失效（混乱主因）

常规态的隐藏规则是**后代选择器**，作用域锚在 `.prompt-editor-inline`：

```css
/* styles.css:4853-4857 */
.prompt-editor-inline #btListenBtn,
.prompt-editor-inline #liveEnrollRow,
.prompt-editor-inline #liveVideoRow,
.prompt-editor-inline #promptPresetBtn,
.prompt-editor-inline #promptPresetMenu { display: none !important; }
```

`appendChild` 之后 `#promptEditor` 的父节点从 `.prompt-editor-inline` 变成
`#fullscreenPromptOverlay` —— **选择器不再匹配，5 个控件全部复活**：

```
实测【C】: ❌ btListenBtn   display=flex 42x42
          ❌ liveEnrollRow display=flex 48x50
          ❌ liveVideoRow  display=flex 206x109
          ❌ promptPresetBtn display=flex 42x42
          ✅ promptPresetMenu（自带 display:none，幸存）
```

这正是你截图里左下角那坨「视频/实时/注册声音/开画面/主动搭话/屏幕」的来源。

### 🔴 问题 2 —— `max-height:72px` 把输入框挤成 8px

```css
/* styles.css:1325 */
.fullscreen-prompt-overlay { max-height: 72px; }
```

实测 `#promptEditor` 高 **139px**，被 72px 上限截断后 flex 压缩，
`#promptText` 宽度塌到 **8px**（占位符只剩「和 BT-7274 对话…」被砍成「和 BT-7274 对话」）。

### 🔴 问题 3 —— 退出按钮被顶栏盖住（这就是"我找不到"）

**层叠上下文囚禁**（实测链）：

```
#videoCard        position:fixed  z-index:200  ★ 新建层叠上下文
  .main-content    position:relative z-index:1  ★ 新建层叠上下文
    .container      position:static  z-index:auto
      body          position:relative z-index:1  ★ 新建层叠上下文
```

`.main-content` 的 `z-index:1` 创建了层叠上下文，把 `#videoCard` 的 `z-index:200`
**关在笼子里** —— 它在自己这一层里最大，但对**根层叠上下文**而言整笼只值 `1`。
而 `.header` 是 `z-index:50`（在根层），永远压在上面。

命中测试取证：

```
❌ 被遮 全屏/退出按钮  @1569,31  最上层 = #themeToggle
❌ 被遮 前后置切换    @1510,31  最上层 = #themeToggle
```

**退出按钮明明在 DOM 里、可聚焦、有 aria-label，但点下去命中的是「深色」主题按钮。**
`Escape` 是通的 —— 但没有任何视觉提示告诉你这件事。

### 🟠 问题 4 —— 两套 VLM 显示面职责重叠

- `#videoOverlay`：由设置页 `#overlayPosition`（None/顶部/底部）控制，常态叠在画面上
- `#fullscreenVlmOverlay`：全屏专属，写死 top:76px left:18px，**不受设置影响**

全屏时若 `overlayPosition ≠ none`，两者会同屏显示同一内容。

## 四、候选修复验证（逐条 + 全量，实测）

用 `inspect-fullscreen-fix.mjs` 注入真实页面验证，非纸面推演：

| 修复 | 退出按钮 | 顶栏 | `#promptText` | 5 个控件 |
|---|---|---|---|---|
| 基线 | ❌ blocked | 在 | 8px | 全复活 |
| ① `.main-content{z-index:auto}` | ✅ | ❌ 在 | 8px | ❌ 复活 |
| ② 全屏收起 `.header` | ✅ | ✅ 收起 | 8px | ❌ 复活 |
| ③ 补 `#fullscreenPromptOverlay` 作用域 | ❌ | ❌ 在 | ✅ 394px | ✅ 隐藏 |
| ④ 去掉 72px 上限 | ❌ | ❌ 在 | 8px | ❌ 复活 |
| **①+②+③+④ 全量** | **✅** | **✅** | **✅ 394px** | **✅ 全隐藏** |

**结论：4 条必须一起上。** 单修任何一条都留下缺口 —— 这也解释了为什么历史上零敲碎打没修好。

全量修复后的截图：`scripts/.fs-inspect/C-fixed.png`
（顶栏收起、只有一个退出图标、左下角输入栏干净、text 宽 394px）

## 五、收口结果（用户拍板：路线 B，已落地）

用户 2026-09-19 复核本审计后选 **路线 B（彻底）**，并追加三点要求：
1. 删掉字幕占位「就绪」（影响观感）
2. 全屏与常规态**按钮统一**、输入栏**摆到底部**（画面在上、底部聊天框）
3. 字幕浮动**只显示 AI 回复**（参考 GPT Live / 手机端 AI Agent）

### 实际改动

**A. 功能控件真删出输入栏 → 迁入设置页**（路线 B 核心）
- 新建设置区块 **外观 → Live 常驻模式**（`#liveModeSection`），承载
  `btListenBtn` / `liveModeBtn` / `liveEnrollRow` / `liveVideoRow` / `promptPresetBtn` / `promptPresetMenu`
- **id 与 JS 绑定零改动**（`live_ui.js` / `speech_input.js` / `status_poll.js` 未动一行）
- 删除 `styles.css` 里 5 条已无对象的旧隐藏规则（`.prompt-editor-inline #btListenBtn` 等）
- 补 `.listen-label` 等**显示**规则：原输入栏语境把它们 `display:none`，
  迁入设置页后必须显示文字（否则 `#btListenBtn` 只剩一个电台图标）

> ⚠️ 落点修正：审计初稿建议搬进「输入」面板，但该面板是用户 2026-09-18
> **亲自删除**的（理由：空壳 + 与视频按钮重复）。按「不恢复用户删除之物」的硬约束，
> 改为放进「外观」新增区块 —— 语义也更贴合（外观已承载 Layout / WebRTC / Audio Output）。

**B. 三项 bug 修复**（①②④，全部实测通过）
- ① `.main-content{z-index:auto}`（`:has()` 限定全屏态）解除层叠囚禁
- ② `body:has(.video-card.fullscreen)` 收起顶栏与侧栏 → 真正全屏
- ④ 去掉 `.fullscreen-prompt-overlay{max-height:72px}`

**C. 布局（2(b)）**
- 输入栏：`left:50% + translateX(-50%)`，`min(760px, 100vw-48px)`，贴底居中
- 字幕：输入栏正上方、同轴居中，字号 16px，最多 6 行

**D. 字幕（3(c)）**
- `getFullscreenVlmText()` 改为**只输出模型回复**，并统一过 `getVlmDisplayText()`
  （剥离 `</response>`；`</silence>` 返回空 → 该轮不出字幕）
- 无内容时给 `#fullscreenVlmOverlay` 加 `.is-empty` → **整块隐藏**
- DOM 里删掉占位 `<div class="vlm-content" data-i18n>Ready</div>` 的 "Ready" 文本

### 验收（`node scripts/check-fullscreen-parity.mjs` → 16/16 通过）

| 断言 | 结果 |
|---|---|
| 2(a) 两态输入栏按钮集一致 | ✅ `[camBtn, promptText, speechBtn, promptSendBtn]` |
| 2(a) 两态工具条一致 | ✅ `[quickCameraBtn, fullscreenIcon]` |
| 5 个 Live 控件已不在输入栏 | ✅ |
| 2(b) 输入栏贴底 | ✅ 底部 880px / 视口 900 |
| 2(b) 输入栏水平居中 | ✅ 中心 800 = 视口中心 |
| 退出按钮可点 | ✅ 命中 ok（修复前命中的是 `#themeToggle`） |
| 全屏顶栏收起 | ✅ |
| 输入框宽度正常 | ✅ 576px（修复前 8px） |
| 3(c) 无内容时字幕整块隐藏 | ✅ |
| 3(c) 有回复时显示 | ✅ |
| 3(c) 不含用户输入 | ✅ |
| 3(c) 决策 token 已剥离 | ✅ |
| 3(c) 含模型回复正文 | ✅ |
| 3(c) `</silence>` 不出字幕 | ✅ |

截图：`scripts/.fs-inspect/{D-normal,E-fullscreen,F-subtitle,G-settings-live}.png`

### ⚠️ 本轮事故（已修复，教训已固化）

搬迁时用整块替换改输入栏 HTML，**块内含 `<textarea id="promptText">` 被一并删掉**
—— 聊天输入框整个消失。而当时的自检只比 `id` 总数，该次是「+2 新增 −1 删除 = 净 +1」，
**净差完全掩盖了这次删除**，直到渲染验收才发现。

已修复：`webui-invariants.mjs` 改为**逐 id 集合比对**，任何 id 消失必须
`--id-removed=a,b` 逐项声明才放行；教训写入
`doc/standards/webui-design-standards.md` §9.1。

## 六、二次修订（用户复核后，同日）

用户复核第一版收口后提出 4 点，全部已落地：

| 用户反馈 | 处置 |
|---|---|
| 「实时」移错了，恢复到聊天框，**方便直接开启** | ✅ 回到输入栏（`#camBtn` 之后、`textarea` 之前），恢复双行按钮 54×52 |
| 「唤醒」我记得是单独开关、可自定义唤醒词 | ⚠️ **事实澄清见下**：不存在自定义唤醒词；已有的是「静默态 KWS」 |
| 「主动搭话」的勾选要不要重新设计 | ✅ 原用裸 `<input type=checkbox>` → 改用设计系统既有 `.toggle-switch`（**零新设计**） |
| 两张卡不该在「外观」，应弄到「高级」 | ✅ `#liveModeSection` + `#wakeAsrSection` 迁入高级 |

### ⚠️ 事实澄清：「自定义唤醒词」不存在

用户记忆中的"可自定义唤醒词"，经全仓库核实**并未实现**：

- `jarvis_config.py:162` → `wake_word: str = "bt"`（**写死常量**）
- `services/asr/jarvis/kws.py:29,40` → `wake_word` 注释明写 **(display only, 实际从 keywords_file 读)**
- `services/kws-training/README.md:4` → 「**唤醒词已拍板为 "bt"**（2 token B+T），自训 v4 模型已部署」
- 前端**无任何**唤醒词输入框（全仓库无 `wakeWord` UI 绑定）

**根本原因**：KWS 是**自训模型**（sherpa-onnx，v4 在 `D:\AI\models\...\kws\bt-en\`）。
换唤醒词 = **重新训练模型 + 重采语料**，不是加输入框能解决的。
用户记忆中的"取舍"应该就是指**取舍掉了可自定义、定死为 "bt"**。

用户说的"唤醒开关已单独有了"是**另一个东西**：
**静默态 KWS**（高级 → 无线电静默 → `silenceKwsToggle`「开：喊名字可唤醒」）。

因此 `Wake / ASR` 卡的定位已澄清并写入代码注释与设计规范 §7.6：
它**只做一件事** —— KWS 漏检时用本地 paraformer ASR 兜底唤醒。

### 落点修正

- 卡片落点：**高级**（不是审计初稿建议的「输入」面板 —— 那是用户 2026-09-18 亲自删的）
- 高级面板结构：新增 `#advancedPanel`（`display:contents` 包裹层），
  修正 `PANEL_ROOTS.advanced` 从 `radioSilenceSection` 改为 `advancedPanel`
  （原指向单张卡 → 兄弟节点不受 `.cat-hidden` 控制，切到「外观」时两张新卡照样可见）

### 验收

`node scripts/check-advanced-relocation.mjs` → **12/12 通过**：
高级面板三卡可见且展开、外观面板不含它们、主动搭话用 `.toggle-switch`、
「实时」回到输入栏且为双行按钮。

`node scripts/check-fullscreen-parity.mjs` → **17/17 通过**（新增「实时在输入栏」防回归断言）。

## 七、同日两次事故（已修复，教训已写入规范）

1. **块提取截断**：`lastIndexOf('<div', idx)` 定位卡父节点时命中了标题的 `<div>`，
   导致 Wake 卡被截成 349 B（丢正文与闭合标签），**吞掉整个外观面板**；
   而全局 `<div>` 计数仍配平，结构检查看不出来。→ 改用**语义注释锚点** + 块级完整性断言。
2. **CSS 补丁三重复**：`webui-css-patch.mjs` 的标记用 `\n` 而文件是 CRLF，
   幂等保护静默失灵，每次运行都追加一份（累积 3 份）。修复后加了
   "标记恰好 1 份"自检与"已存在旧块却未移除"拒绝写入。

详见 `doc/standards/webui-design-standards.md` §9.1 / §9.2 / §9.3。

## 八、遗留

- ~~**`.fullscreen-vlm-overlay` 与 `#videoOverlay` 的重叠策略未定**~~ →
  ✅ **已定性并修复**，见 §九（此前"会显示同一内容"是读码推测，实测已修正）
- ~~**移动端 ≤768px 全屏未验**~~ → ✅ **已补验**（`scripts/check-mobile-fullscreen.mjs`，
  4 档视口 **32/32** 通过），并借此发现并修掉 360px 常规态输入框仅 64px 的问题。

## 九、★ 「overlay 与字幕重叠」的准确定性（实测修正，用户拍板 A + B）

此前记为「职责重叠 / 会显示同一内容」——**那是读码推测，不准确**。
用真实交互流程实测后修正如下。
复现脚本：`scripts/inspect-overlay-overlap.mjs`、`inspect-subtitle-semantics.mjs`、
`check-overlay-ab.mjs`。

### 9.1 设置页那一项到底控制什么（用户质疑「是不是重复造轮子」）

设置 → 外观 的 `#overlayPosition` **不是「字幕开关」**，而是
**「VLM 输出放哪」的二选一**（`app_main.js:821 applyOverlayPosition`）：

| 取值 | `#videoOverlay`（画面叠字） | `#resultText`（聊天框） |
|---|---|---|
| `none`（默认） | 隐藏 | **可见** |
| `top` | **可见**（y≈77） | **隐藏** |
| `bottom` | **可见**（y≈700） | **隐藏** |

```js
if (position !== 'none') {
    videoOverlay.classList.add('show', position);
    resultText.style.display = 'none';   // ★ 互斥：把聊天框整个藏掉
} else { resultText.style.display = 'flex'; }
```

**结论：不是重复造轮子，是命名欺骗。** 旧文案
「VLM Output on Camera View / 在视频画面上直接叠加文字」只描述了"叠字"这一半，
**隐去了"聊天框被隐藏"这一半**。

### 9.2 三个显示面的真实分工

| 显示面 | 控制者 | 内容 |
|---|---|---|
| `#resultText` 聊天框 | **同一个** `overlayPosition` | 完整历史（多轮 prompt+response） |
| `#videoOverlay` 画面叠字 | **同一个** `overlayPosition` | 单条最新输出，贴画面 |
| `#fullscreenVlmOverlay` 全屏字幕 | **全屏专属，不受设置影响** | 只显示 AI 回复 |

### 9.3 实测到的真实撞车（两种，**都不是**"浮层互相重叠"）

实测三个取值下两浮层**几何上都不重叠**；真问题是：

1. **`top` 时内容重复**：顶部叠字条 + 底部字幕框**同时显示同一轮推理** → 屏幕上出现两遍
2. **`bottom` 时文字被遮挡**：叠字条（`y=844,h=56`）与输入栏（`y=806,h=74`）
   **纵向叠 36px** → 文字被输入栏压住并截断（`overlay-bottom-bottom.png` 可见）

**这也解释了为什么默认值是 `none`** —— 默认下不撞车，故问题长期未被发现。

### 9.4 修复（A + B）

- **A**：全屏时禁用画面叠字，**全屏字幕为唯一显示面**。
  用 **CSS 单点收口**（`.video-card.fullscreen .video-overlay{display:none!important}`）
  —— 因为 `applyOverlayPosition` 与 `ws_dispatcher` **两处都写这个 class**，
  CSS 收口比改两处 JS 更不易漏、也不会各自漂移。**常态行为完全不变。**
- **B**：设置项**改名**以匹配真实语义：
  - 标签：`VLM Output on Camera View` → **`VLM output location`（VLM 输出位置）**
  - 说明：→「显示在画面上时；聊天框会被隐藏」（明示互斥）
  - 选项：`None / At the top / At the bottom` → **`In the chat panel / On the video (top) / On the video (bottom)`**

**验收**：`scripts/check-overlay-ab.mjs` → **18/18**
（常规态行为不变、全屏叠字禁用、无内容重复、字幕不被输入栏遮挡、四条文案断言）

### 9.5 顺带修掉的两个 i18n 匹配陷阱（实测踩到）

1. **含括号的文案不能以 `\b` 收尾** —— `/\bOn the video \(top\)\b/` **永不匹配**：
   `)` 与串尾都是非词字符，二者之间不存在词边界。→ 这些词条省略尾部 `\b`。
2. **文案里不能用英文逗号** —— 表中 `/,/ → '，'` 是**全局**规则，会先把 `,`
   换成全角「，」，导致整串词条再也匹配不上（实测：说明文案汉化失败）。
   → 说明文案改用**分号**收束。

> 判别口诀：**改 i18n 词条前，先跑一遍 `localizeUiString(新串)` 看它是否真命中** ——
> 词条"看起来对"不等于正则真能匹配。

