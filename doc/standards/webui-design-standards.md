# WebUI 设计规范（UI Design Standards）

> 生命周期：**稳态型**（类型 A）—— 新增/修改 WebUI 界面时**必须**遵循；规范本身变更时同步本文件。
> 建立：2026-09-18 ｜ 适用：`services/webui/src/joy_interaction_webui/static/`
> 关联：`doc/adr/0020-webui-local-cloud-selector.md`（槽位选择器 idiom）、`doc/specs/unified-api-config-ui.md`（数据契约）、`doc/frontend/idesign-wiring.md`（Studio 接线）

## 0. 为什么需要这份规范

2026-08 至 09 期间，WebUI 出现过多次**同类问题反复返工**：按钮大小不一、卡片有无不一致、
同一功能两处实现、浅色主题下元素隐形、中文汉化后按钮竖排等。根因不是单点疏忽，而是
**缺少一份统一的界面契约** —— 每个改动各自决定形态，于是不断产生新的不一致。

本文件把已踩过的坑固化为**强制约束 + 现成契约**，新增界面前先查这里。

---

## 1. 样式变更的落地位置（最重要的一条）

**所有 UI 修复/新增样式，一律追加到 `scripts/webui-css-patch.css`，通过
`node scripts/webui-css-patch.mjs` 写入 `styles.css` 末尾。**

原因（实测）：
- `styles.css` 里 `.icon-btn` 有 **3 处定义**（约 820 / 4615 / 5099 行）、
  `.panel` / `.settings-section` 也是多代规则并存；
- CSS **同特异性下后者胜**，把规则插在文件中部**不可靠**（曾因此让"测试"按钮竖排修不掉）；
- 追加式补丁**不改动上方任何既有规则** —— 保留可回溯性，也避免误删。

> ⚠️ **反面教训**：2026-09-18 曾用「移动代码块」脚本编辑 `styles.css`，
> 因 `indexOf` 定位失败（`start > end`）且脚本未校验边界，**切掉了文件开头、
> 破坏了大括号配平**，只能 `git checkout` 回滚，代价是本轮 CSS 改动全部丢失。
> 脚本必须做**结构自检**（见 §6）。

---

## 2. 卡片契约

设置弹窗内**所有区块**（`.settings-section` 与 `.panel`）统一为卡片：

```css
background: var(--bg-elev-2);
border: 1px solid var(--border);
border-radius: 16px;        /* 取自 iDesign 预设 data-report 实测值 */
padding: 20px;
box-shadow: var(--shadow);  /* 用本仓库自带变量，两主题各自解析 */
```

- **嵌套卡圆角 = 外层 × 0.65 ≈ 10px**（官方手法，避免"卡中卡"视觉冲突）
- 区块间距 `12px`
- hover 仅变边框色，**不做位移**（弹窗内大位移会晃）
- **浅色主题**：不照抄预设的浅色阴影（`rgba(15,23,42,.1)` 在 `#141416` 上完全不可见），
  必须用 `--shadow` / `--shadow-lift`（已在 `styles.css` 按主题分别定义）

---

## 3. 主题与颜色

- **颜色一律走 token**，禁止硬编码。反面案例：`.settings-btn` 曾写死
  `rgba(255,255,255,.14)` + 白字 → **浅色下白底白字，按钮整体隐形**。
- 两套 token 并存（历史遗留，均可用）：
  - 样板 token：`--bg` / `--bg-elev` / `--bg-elev-2` / `--border` / `--text` / `--text-2` / `--text-3`
  - legacy token：`--bg-primary` / `--card-bg` / `--border-color` / `--text-primary`
- 主题切换类是 **`body.light-theme`**（不是 `data-theme`）
- **图标反色**：功能性图标用 `stroke: var(--text)` ——
  深色下 `#EDEDED`（白）、浅色下 `#1A1A1C`（黑），两边都醒目。
  不要用 `--text-secondary`（浅色下对比不足，用户实测反馈"看不清"）

---

## 4. 按钮规范

### 4.1 尺寸与形态

| 用途 | 契约 |
|---|---|
| 行内操作按钮（测试 / 保存 / 获取模型） | `.action-row .icon-btn`：`min-width: 72px` `height: 32px` `padding: 0 12px` `border-radius: 8px` |
| 顶栏图标钮 | `.icon-btn`：`38×38` |
| 模式/状态胶囊 | `.chip` / `.status-badge`：`border-radius: 999px` |

### 4.2 硬约束

- **`white-space: nowrap` 必须显式声明**。中文（全角）在窄容器里会**逐字竖排**——
  实测："测试"（2 字）在 `width: 38px` 的 `.icon-btn` 里被挤成竖排。
- **⚠️ 带文字的按钮必须放进 `.action-row` 容器**。这是**最容易漏的一条**：

  `.action-row .icon-btn` 的 `nowrap` / `min-width` / 内边距契约**只对 `.action-row` 的直接子元素生效**。
  若按钮写在一个内联 `style="display: flex; gap: 8px;"` 的 div 里，**不会继承该契约** →
  中文文字照样竖排。

  2026-09-18 实测漏网清单（**全部**是"内联 flex 容器 + 带文字的 icon-btn"）：
  `memoryStoreSaveBtn` / `wikiPasteBtn` / `wikiSyncBtn` / `silenceSaveBtn` / `svcSaveBtn` / `svcProbeBtn`。

  > 为什么容易漏：这些按钮分散在不同面板，且内联样式看起来"已经横排了"。
  > 实际上内联 `display:flex` 只保证**容器**是横排，**按钮内部**的图标+文字仍会因
  > 宽度约束而逐字换行。

- **纯图标按钮（无文字）不需要 `.action-row`**。它们不存在换行风险，
  保持内联 flex 即可。例：`webcamStartBtn`/`webcamStopBtn`/`testRtspBtn`/
  `rtspStartBtn`/`rtspStopBtn`/`screenStartBtn`/`screenStopBtn`。

  **判据**：按钮内有 `<span>` 文字节点 → 必须 `.action-row`；只有 `<i>` 图标 → 随意。

- **按钮文字不要直接放在 `<i>` 图标之后**。`applyUiI18n` 用 `textContent` 作 key，
  会把图标内容一起取到，导致永远命不中词表。必须包成
  `<button><i ...></i> <span data-i18n>文字</span></button>`。
- 按钮必须成组放在 `.action-row` 容器内（提供统一的分隔线与间距），
  不要用内联 `style="display:flex;..."` —— 那样无法被统一契约覆盖。

### 4.4 自查命令（加按钮后跑一次）

**推荐：用现成脚本**（起静态服务后跑，覆盖全部设置面板）：

```bash
# 1) 起静态服务（若未起）
services/.venv/Scripts/python.exe -m http.server 8123 \
  --directory services/webui/src/joy_interaction_webui/static --bind 127.0.0.1

# 2) 扫描竖排按钮（exit 0 = 全部正常；exit 1 = 有竖排，并列出 id）
node scripts/check-button-wrap.mjs
```

该脚本用**真实渲染测量**判定（`height > 40px` 或 `width < 60px 且含中文` → 竖排），
不依赖 grep —— 因为内联 `display:flex` 的容器"看起来"已经横排，只有实测高度才暴露问题。

**纯 grep 版**（不依赖浏览器，仅找结构可疑处）：

```bash
node -e "
const fs=require('fs');
const lines=fs.readFileSync('services/webui/src/joy_interaction_webui/static/index.html','utf8').split('\n');
let n=0;
lines.forEach((l,i)=>{
  if(!/style=\"display: flex/.test(l)) return;
  const seg=lines.slice(i,i+4).join('\n');
  const m=seg.match(/<button[^>]*icon-btn[^>]*>[\s\S]*?<span[^>]*>[^<]+<\/span>/);
  if(m){ console.log('⚠️ L'+(i+1)+': 内联 flex 里带文字的按钮，应迁到 .action-row'); n++; }
});
console.log(n===0?'✅ 无漏网':'共 '+n+' 处');
"
```


### 4.3 功能提示（无障碍）

**每个可交互元素都应有 `title` + `data-i18n-title`**（两者值相同），说明"这能做什么"。
导航项、区块标题、关键按钮是重点。

---

## 5. i18n（汉化）规范

机制：`i18n_device_label.js` 的 `UI_STRING_MAP`（有序 `[RegExp, 中文]`）
→ `localizeUiString()`
→ `applyUiI18n()` **只遍历 `[data-i18n]` 元素**，用 `textContent` 作 key。

### 硬约束

1. **词条有了不等于生效** —— 元素必须带 `data-i18n` 才会被翻译。
   （2026-09-18 排查发现 26 条 MAP-ONLY：词表有、元素无属性，因此仍是英文）
2. **长短语必须排在短词之前**，否则被短词先吃掉。
   例：`Save this set` / `Save Memory Store` 必须排在 `\bSave\b` 之前。
3. **当心全局替换规则**：`[/, / → '，']` 与 `[/: / → '：']` 会**无差别替换所有英文逗号/冒号**。
   需要保留英文标点的整句词条，**必须插到这两条之前**。
   （曾产出 `issues，roadmap` 这种半吊子结果）
4. **不要翻译**专有名词：`WebRTC` / `ASR` / `LLM` / `TTS` / `VLM` / `API` / `ADR-xxxx` /
   provider 名（`openrouter` / `minimax` / `codex` / `hermes` / `siliconflow` / `nvidia`）。
5. **JS 动态写入的文案**不会自动翻译，必须显式调用：
   `window.JoyI18n.localizeUiString(str)`。
6. `data-i18n` 用 `textContent` 作 key，所以**不要写「中文 英文」混排**——
   会被二次转换成「中文 中文」（实测 `模型 Model` → `模型 模型`）。

---

## 6. 批量改 HTML/CSS 的自检要求（强制）

用脚本批量改动界面文件时，**写盘前必须通过结构守恒自检**：

```js
const c = (re, s) => (s.match(re) || []).length;
const inv = {
  lt:      [c(/</g, before), c(/</g, after)],
  gt:      [c(/>/g, before), c(/>/g, after)],
  openTag: [c(/<[a-zA-Z][a-zA-Z0-9-]*\b/g, before), c(/<[a-zA-Z][a-zA-Z0-9-]*\b/g, after)],
  closeTag:[c(/<\/[a-zA-Z][a-zA-Z0-9-]*>/g, before), c(/<\/[a-zA-Z][a-zA-Z0-9-]*>/g, after)],
  idAttr:  [c(/\bid="/g, before), c(/\bid="/g, after)],
};
```
- 有意的增删（如新增一个面板）**必须逐项声明预期 delta**，不得笼统放过
- 删除/移动代码块前先校验 **`start >= 0 && start < end`**，否则拒绝执行
- 批量插属性用「捕获**整个起始标签**」的正则；只捕获属性部分会导致替换时
  **吃掉标签名**，产出 `type="button" class="...">` 这类非法 HTML（页面上直接显示属性文本）
- 改完**立即跑语法检查**，并**渲染截图确认**（不要只看 grep 命中数）

---

## 7. 布局与容器

- 设置弹窗内区块由 `.settings-body` 管滚动。**子项必须 `flex-shrink: 0`** ——
  否则被压扁 + 父级 `overflow:hidden` 裁切，表现为"面板只显示一半"
  （实测：内容 `scrollHeight=3167px`，容器只给 `696px`，5 行只可见 1 行）。
- 主区两列：`.main-content` 用 `grid-template-columns: 1.9fr 1fr`
  （视频侧重、聊天卡承载输入框）。窄屏 `≤1280px` 放宽到 `1.6fr 1fr`。
- 输入栏已**嵌入聊天卡**（`#vlmOutputCard > .prompt-editor-inline`，`order:10` 吸底）。
  全屏时 JS 仍会把 `#promptEditor` 搬进 `#fullscreenPromptOverlay`，改动此处注意别破坏该路径。

### 7.1 全屏（Fullscreen）契约 — 2026-09-19 定稿

> 详见 `doc/frontend/fullscreen-audit-2026-09-19.md`（含实测取证与修复验证矩阵）。
> 验收命令：`node scripts/check-fullscreen-parity.mjs`（16 项断言）。

**机制**：全屏 = `#videoCard.classList.toggle('fullscreen')`（`position:fixed;inset:0`），
且 `toggleFullscreen()` 会把 `#promptEditor` **物理搬到** `#fullscreenPromptOverlay`。

**四条硬约束**（违反任何一条都会复现历史缺陷）：

1. **两态按钮集必须完全一致。**
   常规态与全屏态输入栏只允许 `#camBtn` / `#promptText` / `#speechBtn` / `#promptSendBtn`；
   工具条只允许 `#quickCameraBtn` / `.fullscreen-btn`。
   → **禁止把功能控件塞进输入栏再用 CSS 隐藏**：全屏搬家后作用域失效，控件会集体复活。
   需要收纳的功能**必须真删出输入栏、改建到设置页**（见 §7.2）。

2. **`.main-content` 不得带 `z-index`。**
   它若创建层叠上下文，会把 `#videoCard.fullscreen{z-index:200}` **囚禁**其中，
   导致 `.header{z-index:50}` 永远压在上面、退出按钮被顶栏按钮盖住（点击命中的是别人）。
   现有补丁用 `:has()` 在全屏时降为 `auto`；新增任何祖先定位/层叠规则都要重新验命中。

3. **全屏 = 画面独占。** 顶栏与侧栏在全屏时收起（`body:has(.video-card.fullscreen)`）。
   退出必须有**可见**入口：右上角按钮（图标 maximize↔minimize）+ `Esc`。

4. **字幕（`#fullscreenVlmOverlay`）只显示 AI 回复。**
   参考 GPT Live / 手机端 AI Agent 视频聊天模式：
   - **不推用户输入**（不显示「输入：…」）——那是聊天历史语义，不是字幕
   - 回复统一过 `getVlmDisplayText()`（剥离 `</response>` 等决策 token；`</silence>` 返回空 → 该轮不出字幕）
   - **无内容时整块隐藏**（`.is-empty`），不得出现"空盒子写着占位文案"
   - 位置：输入栏正上方、同一视觉中轴

**布局**：画面在上铺满；输入栏底部水平居中（`min(760px, 100vw-48px)`）；
字幕在输入栏上方居中。**禁止 `max-height` 写死像素**截断输入栏
（历史事故：`max-height:72px` 把 139px 的输入栏压到 `#promptText` 仅剩 8px 宽）。

### 7.2 功能控件的归属（输入栏 vs 设置页）

- **输入栏只放高频对话控件**：视频源、输入框、按住说话、发送。
  外加**「实时」**（Live 常驻模式开关）—— 用户 2026-09-19 明确要求它留在输入栏
  （"方便直接开启"），尽管它也是模式开关。
- **配置类 / 低频模式类控件一律进设置页**，不得"藏在输入栏里靠 CSS 隐藏"。
- 2026-09-19 落地：Live 常驻模式的**其余**控件（`btListenBtn` / `liveEnrollRow` /
  `liveVideoRow` / `promptPresetBtn`）已迁入 **设置 → 高级 → Live 常驻模式**
  （`#liveModeSection`），id 与 JS 绑定零改动。
- 迁入设置页的控件需补足**可见文字标签**：原输入栏语境下
  `.listen-label{display:none}` 等把文字藏了，设置页里必须显示
  （否则 `#btListenBtn` 只剩一个电台图标，看不出是「Jarvis 唤醒」）。

### 7.3 设置卡片的面板归属：按**语义**，不按现状

2026-09-19 用户指出："有些卡片的位置摆放不对，觉得这两个功能应该不是外观功能吧"。
复核确认 `Live 常驻模式` 与 `Wake / ASR` 被放在**外观**（视觉设置）面板里，属历史遗留。

| 面板 | 该放什么 | 判据 |
|---|---|---|
| **外观** | 纯视觉：布局顺序、动画、配色、图标风格 | 改了只影响"看起来怎样" |
| **高级** | 运行时行为 / 唤醒链路 / 实验性开关 | 改了影响"怎么工作" |
| 模型 / 委派 / 语音 / 记忆 / 知识库 | 各自的后端连接与资源 | —— |

**已归位**：`#liveModeSection`、`#wakeAsrSection` → 高级。

> 新增卡片时必须先问"它属于哪一类"，而不是"放在哪里顺眼"。
> 判据是**功能语义**，与 §10「需要的才统一」同源。

### 7.4 设置面板必须用包裹层（`#xxxPanel`）

**`PANEL_ROOTS` 的值必须是包裹整个面板的容器**，不能指向面板内的某一张卡。
`showSettingsPanel()` 只对 `PANEL_ROOTS` 的根元素切 `.cat-hidden` ——
若指向某张卡，**它与同级卡都不受控**（切到别面板时仍然可见）。

实测事故（2026-09-19）：`advanced: 'radioSilenceSection'`，而新迁入的两张卡是它的
**兄弟**节点 → 切到「外观」时它们照样显示。

两种正确写法：
- 普通包裹层：`<div class="panel settings-root-panel" id="xxxPanel">…</div>`
- **`display:contents` 包裹层**：`<div id="xxxPanel" style="display:contents">…</div>`
  （布局上子元素等同 `.settings-body` 直接子元素，卡片样式不变形；
  而 `.cat-hidden{display:none !important}` 能盖住 inline 的 `contents`）
  —— 高级面板现用此写法；`#appearanceSection` 同理。

### 7.5 勾选类控件统一用 `.toggle-switch`

**不要用原生 `<input type="checkbox">`**。本项目已有设计系统开关：

```html
<label class="toggle-switch">
  <input type="checkbox" id="...">
  <span class="toggle-slider"></span>
</label>
```

- 语义仍是 checkbox（a11y 正确），视觉是滑块，全站 15+ 处在用。
- 反面案例：「主动搭话」原用裸 checkbox（`.live-proactive-toggle`），
  用户看出"目前没有勾选类设计，都是按钮组合" → 已改用 `.toggle-switch`。
- 禁用态由 `.toggle-switch input:disabled + .toggle-slider` 统一弱化。

### 7.6 历史遗留的命名陷阱：`Wake / ASR` ≠ 唤醒词设置

`Wake / ASR` 卡片**不是**唤醒词配置，只做「KWS 漏检时用本地 paraformer ASR 兜底唤醒」。

**唤醒词在后端写死为 `"bt"`**：
- `services/webui/src/joy_interaction_webui/jarvis_config.py:162` → `wake_word: str = "bt"`
- `services/asr/jarvis/kws.py:29,40` → `wake_word` 注释明写 **(display only, 实际从 keywords_file 读)**
- `services/kws-training/README.md:4` → 「唤醒词已拍板为 "bt"（2 token B+T），自训 v4 模型已部署」

**前端没有任何唤醒词输入框**。换词 = 重新训练 KWS 模型 + 重采语料，**不是加个输入框能做的**。
这是历史取舍，不要误以为"设置里有自定义唤醒词入口"。

---

## 8. 删除元素的纪律（用户拍板）

- **用户删除的元素不要恢复**。只检查其**实现**（JS 绑定 / 数据管道 / CSS 规则）是否跟着清理。
- 需要「保留实现、仅隐藏显示」时，**用户会特别标注**；未标注即按**真删**处理。
- 反面案例：用户要求"删除设置页接口状态面板"，被改成「隐藏保留」并回填 → 属于与用户操作打架。
- 判据是**用户原话**，不是我们认为怎样更好。

---

## 9. 检查表（提交前逐项确认）

- [ ] 样式追加到 `webui-css-patch.css` 并跑了 `webui-css-patch.mjs`（自检通过）
- [ ] 颜色全部走 token，**浅色 + 深色都实际看过**
- [ ] 按钮在 `.action-row` 内，有 `white-space: nowrap`，文字包在 `<span data-i18n>`
- [ ] **跑了 `node scripts/check-button-wrap.mjs`**（带文字的按钮无竖排）
- [ ] 新增文案：已加 `data-i18n`（或 JS 侧走 `localizeUiString`），且词条顺序正确
- [ ] 可交互元素有 `title` + `data-i18n-title`
- [ ] 批量脚本改动过了结构守恒自检
- [ ] **跑了 `node scripts/webui-invariants.mjs` 且零「未声明变化」**
      （含**逐 id 集合比对** —— 净差会掩盖「删一个 + 加一个」，见 §9.1）
- [ ] 改动涉及输入栏 / 全屏 → **跑了 `node scripts/check-fullscreen-parity.mjs`**（17 项断言）
- [ ] 改动涉及设置面板归属 → **跑了 `node scripts/check-advanced-relocation.mjs`**（12 项）
- [ ] 移动/提取 DOM 块时：锚点用**语义注释**而非"最近的标签"，且验证**块自身** `<div>` 配平（§9.2）
- [ ] 跑过语法检查（JS + 内联脚本 + Python）
- [ ] 渲染截图确认（尤其折叠态、浅色态、窄屏）
- [ ] 删除/隐藏行为符合 §8

---

## 9.1 结构性改动的纪律：净差会骗人（2026-09-19 实测教训）

**事故**：搬迁 Live 控件时，我用「整块替换」改了输入栏 HTML，
该块**内含 `<textarea id="promptText">`**，被一并替换掉了 —— **聊天输入框整个消失**。

**为什么自检没拦住**：我当时只比对 **`id` 总数**，而该次改动是
「新增 2 个 id（`liveModeSection` 等）+ 删除 1 个（`promptText`）」= **净 +1**，
计数检查完全看不出。直到渲染验收发现 `#promptText` 不可见才暴露。

**纪律**：
1. **逐 id 集合比对**，不是比总数。`webui-invariants.mjs` 现已强制输出
   「新增 / 删除」两个集合，删除项必须用 `--id-removed=a,b` **逐项声明**才放行。
2. **整块替换前先确认块内含哪些 id**，尤其是 `#promptText` 这类"不在编辑意图里、
   但恰好位于被替换区间"的元素。
3. **计数变化必须逐项声明**（`--expect=k=delta`），笼统放过等于没有检查。
4. 每次结构性改动后**必须重新渲染验收**（`check-fullscreen-parity.mjs` 会实测
   两态输入栏控件集），因为静态检查无法证明"控件还在且可见"。

**判据**：`webui-invariants.mjs` 输出「删除 0」+「未声明变化 0」+ 渲染断言全绿，
三者齐备才算通过。

## 9.2 改 DOM 的定位纪律：锚点错一寸，块就截断（2026-09-19 实测）

同一天内因此连踩两坑，都是"看起来很合理的定位写法"：

**坑 1：用 `lastIndexOf('<div', 目标索引)` 找卡片父节点。**
当索引落在卡片**标题**（`.settings-section-title`）上时，最近的 `<div>` 是标题自己，
于是"从该 div 起配平"会在标题处就归零 → 提取出的块被**截断**。
实测 Wake 卡只剩 349 B，正文与闭合 `</div>` 全丢，**吞掉了紧随其后的整个「外观」面板**。
更隐蔽的是：`<div>` 全局计数仍然配平（截断处少一个 `</div>`，正好被吞掉的内容补上），
所以结构守恒检查**看不出来**，直到浏览器渲染才发现。

**坑 2：改成"找包含该索引的最外层 `<div>`"** —— 结果拿到 `#settingsModal` 根节点，
同样错。

**正确做法**：**用语义锚点，不用"最近的标签"**。
卡片一律以「其专属前导注释」为锚，取注释之后第一个 `<div class="settings-section ...">`：

```js
const commentStart = h.lastIndexOf('<!-- [ASR promotion]');
const cardOpen = h.indexOf('<div class="settings-section', commentStart);
```

配套必须加**块级完整性断言**（本次事故的直接教训）：
- 提取出的块体积下限（如 `> 600B`）
- 块内 `<div>` 开闭**自身配平**（`open === close`）
- 块内不应含下一张卡的标题

**要点**：全局配平 ≠ 块配平。改动前先验证"我提出的这一块，自己闭合吗"。

## 9.3 幂等脚本的失效信号：`removedOldPatch: false`（2026-09-19 实测）

`webui-css-patch.mjs` 的补丁标记用 `\n` 书写，而 `styles.css` 是 **CRLF** 行尾
（且历史上两种行尾混用）。`indexOf` 只能命中"恰好是 LF"的那份 →
**幂等保护静默失灵，每次运行都追加一份新补丁**，累积到 **3 份**（504/771/779 行）。

**它其实一直在报警**：首次追加后 `removedOldPatch` 就该是 `true`，
但输出里一直是 `false`/`1`，没人看。

**纪律**：
1. 幂等脚本必须**每次运行都打印"移除了几份旧内容"**，并且该值在稳态下应为 `1`。
2. 写盘前自检"结果中标记恰好 1 份"，多于 1 即拒绝写入（现已加入）。
3. 跨行尾匹配一律先归一：`const CRLF = raw.includes('\r\n'); raw.replace(/\r\n/g,'\n')`，
   写盘时再还原。
4. 去重后必须做**规则级**损失审计（`scripts/audit-css-loss.mjs`），不能只看行数。

**行数变化的解读**：`css_lines` 从 6257 → 5740（−517）**不是丢内容**，
而是删除 2 份重复补丁 + 本轮合并规则的净效果；
`audit-css-loss.mjs` 证明"消失的 6 条选择器"全部是本轮**有意删除**的旧规则。

## 9.4 验收契约的越界缺陷：让任务为别人的改动负责（2026-09-19 实测）

多任务并发改同一仓库时，**验收命令的管辖范围必须与任务的 inScope 一致**，
否则会制造「必然失败且无法消除」的假红灯。

**实例**：任务 t3 只改 `wiki_frontend.js` / `joy_ws.js`，但它的验收里含
`node scripts/webui-invariants.mjs` 必须通过。而该脚本**只读 `index.html` + `styles.css`**
（`webui-invariants.mjs:9-10,22-23`），**从不读任何 `.js`** —— 于是：
- t3 的改动**根本不会被该脚本观测到**（它可以归零，但脚本看不见）
- 与此同时 t1 正在并发修改 `index.html`，其 `html_lines` delta（实测 −79~−89）落在同一个指标上
- `index.html` **不在 t3 的 inScope** → t3 无权也无法消除

→ t3 诚实地判了自己 `failed`，**这是正确行为**（不谎报 passed）。
**真正的缺陷在契约**：把「别人的在飞改动」放进了我的验收。

**纪律**：
1. **验收命令的读取范围必须 ⊆ 任务 inScope**。跨任务共享的指标（如全仓行数、全仓 id 计数）
   只有在**没有并发写入者**时才能作为验收项。
2. **给共享指标必须写明归属规则**：哪个 delta 是谁的贡献、如何声明。
   反面教材：`html_lines` 只说"必须通过"，没说"t1 的 delta 归属 t1"。
3. **优先用「不变量」而非「总量」做判据**。总量会被任何人改变；不变量不会。
   例如真正该判的是「**删除的 id = 0**」（谁都改不了它，除非真删了元素），
   而不是「行数变化为 0」。
4. **基线要收敛，不要到处传 `--expect`**。让后续任务逐个记住
   `--expect=html_lines=-89` 是脆性做法（数字还在变）。
   正确做法：并发改动**收工后重新 `--save` 基线**，让裸跑恢复为绿。
5. 遇到"必然失败"的验收项，先问**契约是否越界**，再问实现是否违约 ——
   不要用重试掩盖契约错误（重试只会再失败一次，属空转）。

**并发协作的推论**：`webui-invariants.mjs --save` 这类**写基线**的操作，
在多任务并发期间是**危险**的（会把他人的中间态固化成基线）。
应在所有并发写者收工后由集成方统一执行。

---


## 9.5 终态任务会死锁依赖方：一次契约缺陷引发两次失败（2026-09-19 实测）

§9.4 的越界缺陷在本轮**实际触发**，并连锁出第二个更严重的问题。

**经过**：任务 t3 只改 `wiki_frontend.js` / `joy_ws.js`，但 verify 含
`node scripts/webui-invariants.mjs` 必须通过 —— 该脚本**只读 `index.html` + `styles.css`**，
**从不读 `.js`**，于是这条 verify 对 t3 **在原理上不可满足**（rc 由并发的 t2 决定）。
security-guard **两次**诚实拒绝伪报 `passed`，两次判 `failed`。**它的行为是对的，缺陷在契约。**

**连锁问题（更严重）**：任务一旦进入 `failed` 终态，**契约不可再修改**
（`amend_task` 报 `task is failed; terminal contracts are immutable`）。
而 `t4` 的依赖是 `[t1, t2, t3]` → **t3 永不 completed → t4 永久不可 claim
→ t5、t6 连带死锁，整条交付链停摆。**

**纪律**：

1. **verify 命令的读取范围必须 ⊆ 任务 inScope**（§9.4）。这是根因，先修根因。
2. **终态任务不可改契约，所以要改依赖图，不是改它。** 遇到"已 failed 但交付其实完成"的任务：
   - ✅ 对**下游 pending 任务**用 `edit_plan update_task` 移除该依赖边
   - ✅ 用 `amend_task` 修改下游验收，把"上游必须 completed"换成
     "**由本任务独立确认上游的真实交付目标**"（本例：t6 自己跑 shield scan 确认 findings=0）
   - ❌ 不要 `reassign_task` 重试同一个越界契约（只会再失败一次，属空转）
   - ❌ 不要把原本的质量门一起删掉（用「下游自行确认」替代「上游状态」，门还在）
3. **依赖边只能指向"必然可达"的状态。** 若某任务可能因契约缺陷而终态失败，
   下游就不该硬依赖它 —— 要么先修契约（趁它非终态），要么改为「下游自验」。
4. **早修胜过晚修**：t3 在 attempt 1 就已暴露该缺陷，当时它**非终态、可 amend**。
   我改用 `reassign_task` 期望"换个说法收口"，结果它在 attempt 2 因**同一条 verify**
   再次 failed 并进入终态 → 失去了修正契约的窗口。**教训：当失败原因是契约时，
   第一反应必须是 amend 契约（趁可改），而不是重试任务。**
5. **两个死锁点都要查**：本例 t4 依赖 t3 是一条，t6 验收里「t1/t2/t3 均须 completed」
   是**第二条**（容易漏查 —— 它藏在文字验收里，不在 dependency 数组里）。
   排查死锁时要同时扫 `dependencies` 与 `acceptance` 文本。

## 9.6 门禁盲区：deepsec 只扫 `.js`，不扫 `.html`（2026-09-19 实测）

**发现**：`index.html` 的内联脚本里有 **8 处 `innerHTML`**，而 deepsec 提交门禁
长期显示 `findings=0` —— 因为 **L1/L2 只扫 `.js` 文件，不扫 `.html`**。
这些风险代码**藏在 HTML 内联脚本里，从未被门禁看见**。

**危险点**：一旦把内联脚本**外移为 `.js`**（正是"改善工程结构"的常规操作），
这批代码**立刻变成被扫描的对象**。实测（`index.html` 4 块内联脚本抽成 `.js` 后扫描）：

```
HOOK_EXIT=2     ← 提交被阻断
总 findings: 8   high/critical: 8
```

**即：一次"纯结构优化"会突然引爆 8 个 high** —— 而改动者往往以为外移是零风险的搬家。

**8 处的分布与定性**（当前 index.html 行号）：

| 行 | 表达式 | 定性 |
|---|---|---|
| 1547 | `cameraSelect.innerHTML = ''` | 零插值，误报级 |
| 1550 / 1567 | `'<option…>' + localizeUiString(…)` | 仅插 i18n 静态串 |
| 1903 | `` `✅ Connected!<br>${info.codec} …` `` | ★ **真插值**（`info` 来自 RTSP 探测响应，外部数据） |
| 1963 / 1967 / 1977 | `'<i data-lucide="sun\|moon\|monitor"></i>'` | 纯静态 |
| 2315 | `` `<span>Latency: ${latency}ms</span>…` `` | ★ 有插值（源自 DOM 文本） |

**纪律**：

1. **做"内联脚本外移"这类结构改动时，必须同时跑门禁扫描** ——
   不能假定"只是搬家"。搬家会改变**代码被哪些检查器看见**。
2. **门禁绿灯 ≠ 无风险**。要问"这段代码在不在检查器的扫描范围内"。
   `.html` 里的内联 JS 是**系统性盲区**（同理还有 `.css` 里的 `url()`、模板字符串等）。
3. 外移时**顺带加固**：`innerHTML = ''` → `replaceChildren()`；
   静态串 → `createElement` + `textContent`/`setAttribute`；
   真插值 → 逐节点 `createElement` 拼装（`<br>` 用 `createElement('br')`）。
4. 加固后自检：`grep -nE 'innerHTML' <file> | grep -vE ':\s*(//|\*|/\*)'` 应为空
   （**注意排除注释** —— 见坑1：deepsec 规则不剥注释，注释里逐字复现会自己命中）。

## 9.7 搬家协会破坏「静态契约测试」：SPLIT_JS 读取范围（2026-09-19 实测）

**第三类盲区**：deepsec 不看 `.html`（§9.6），而 **pytest 静态契约测试**用的是
「`index.html` + 硬编码文件列表」拼出的**合并源码** —— 代码一搬家，测试就读不到它。

**机制**（`services/webui/tests/` 下 12 个文件含该列表；11 个叫 `SPLIT_JS`、
1 个叫 `_SPLIT_JS`，如 `test_addressee_qa_edges.py:537`）：

```python
SPLIT_JS = ("vlm_history.js", "llm_reply_ui.js", ... )   # ← 硬编码
def _index_html() -> str:
    parts = [INDEX_HTML.read_text(encoding="utf-8")]
    for name in SPLIT_JS:
        parts.append((INDEX_HTML.parent / name).read_text(encoding="utf-8"))
    return "\n".join(parts)
```

断言是**正向存在性**检查（`assert "let liveModeActive = false" in html`）。
把内联脚本外移成 `app_*.js` 后，这些符号**在页面里仍生效**，
但**不在测试的读取范围里** → 断言失配 → **假失败**。

**实测**：外移后新增 3 个失败（`test_live_mode_button_exists_with_independent_state`、
`test_live_proactive_switch_exists_default_off`、`test_placeholder_model_names_are_not_applied`）。
判据可用纯 Python 复现，无需跑 pytest：

```
旧列表： 'let liveModeActive = false' in merged  -> False
补列表： 同上                                    -> True
```

**纪律**：

1. **这是"读取范围"问题，不是"断言错误"** —— 修法是**补全文件列表**，
   **绝不允许改断言本体、删测试、或加 skip/xfail 迁就**。
   判据：断言是 `in`（存在性）而非 `assertNotIn`（禁止性）时才可补列表。
2. **搬迁类改动必须同时跑三层检查**：
   - 门禁扫描（§9.6：搬家会改变代码被哪些检查器看见）
   - **pytest 静态契约测试**（本节：会改变测试的读取范围）
   - 运行时可达性（classic script 共享全局词法环境）
   只跑其中一层，就会漏掉另外两层的假失败/假成功。
3. **留意命名不一致**：`SPLIT_JS` vs `_SPLIT_JS` —— 批量改前先 `grep -rln`。
4. **既有失败与新增失败要分离**：本例基线本身有 10 个失败（与本次无关的历史遗留），
   终态要求是「**新增失败 = 0** 且与基线 diff 逐条相同」，**不是"全部通过"**。
   不要顺手去修那 10 个（属另一个任务，会污染本次改动范围）。

## 9.8 后台轮询 vs 固定等待：写测试时最容易自伤的竞争（2026-09-19 实测）

**事故**：我为「mode-chip 红框仅在使用时出现」写了个验证脚本 `check-topbar-fixes.mjs`，
它「设 `setState('live','ok')` 后**固定等 300ms** 就断言 chip 变红」。同时我又在产品代码
`status_poll.js:216` 加了一行 `setModeChipState('live','off')`（未连接时灰点）。

**后果**：`pollLiveStatus` 每 **1000ms** 跑一次，在 `liveModeActive=false` 时走
`renderLiveStatus(null)` → **把 chip 打回 off**。测试的 300ms 窗口只要跨过一次 1s tick
就被打回，断言失败。**实测失败率约 30%（10 次里 3 次）。**

**修复**（不动断言、不放宽判据）：断言前先 `window.stopLiveStatusPoll()`
→ 使测试只测「映射逻辑」本身，不与后台轮询抢相位。
**修复前 7/10 → 修复后 10/10。**

**纪律**：

1. **写"状态映射"测试前，先问"还有谁在写这个状态"**。若页面有周期性轮询回写，
   固定等待就是**相位赌博**：`wait(300)` 对 `interval(1000)` 的失败率 ≈ 300/1000。
2. **修法是消除竞争源，不是加长等待**。加长到 1100ms 只是把失败率降低、并不消除；
   正确做法是**停掉干扰源**（或把断言改为"轮询停止后才验"）。
3. **探针要包对的函数**。`renderLiveStatus(null)` 是**闭包内直接调用**
   `setModeChipState`，所以包 `window.JoyModeChip.setState` 的探针**抓不到它** ——
   排查时不要因为"探针没报"就排除该路径。
4. **归因要对照"真正的变量"**。本轮一个成员用「改动前/后各跑 16 次」得出"既有 flaky"，
   但真正的变量不是他的改动，而是**我加的产品代码 + 我写的测试**。
   对照实验必须锁定**唯一变量**（本例：停/不停轮询），而不是"改动前后"这种粗对照。

## 9.9 inScope 写窄了会卡死完成动作：`changedPaths ⊆ inScope`（2026-09-19 实测）

**第五种契约缺陷形态**：任务运行器强制 **`changedPaths` 必须 ⊆ 任务 inScope**，
否则**拒绝置为 completed**。若验收要求产生的产物**多于** inScope 所列，任务就**无法正常收口**。

**实例**：t2 的验收要求「内联脚本 1895 → 0 行、**4 块各自在原位置引入**」——
这在结构上**必须**产生 4 个新文件。但它的 inScope 只列了其中 1 个
（`app_main.js`），另 3 个（`app_boot.js` / `sidebar_toggle.js` / `incremental_wiring.js`）
**不在内**。结果：标记 completed 被校验器**连拒 7 次**，
报告 `.../app_boot.js is undeclared`。最终只能**只申报 inScope 内的 5 条**，
把其余路径写进 output 文字里。

**危害**：下游（t4/t5/t6）若**按 inScope 比对改动面**，
会把这 3 个文件误判为「**越界写入**」—— 而它们其实是**验收必需**的产物。

**纪律**：

1. **inScope 必须覆盖"验收要求产生的全部产物"**，而不是"我打算手改的文件"。
   下发前自问一句：**这些验收项落地后，磁盘上会多出/改变哪些路径？**
   全部要写进 inScope。
2. **inScope 写窄的两种典型**：
   - 验收要求「拆成 N 个文件」却只授权 1 个（本例）
   - 验收要求「补全测试读取范围」却没授权 `tests/`（本例 t2 的 12 个 `SPLIT_JS` 文件，
     需 captain 事后单独授权）
3. **下游不要盲信 inScope 字段**。若某路径的改动**在语义上属于上游验收的必然结果**，
   应视为已授权；把"是否越界"的判断交给**语义**，而不是字段。
   （本例 captain 在 t6 契约里显式登记了完整 8 类路径清单，正是为了防这个误判。）
4. **终态不可改**：与 §9.5 同源 —— 一旦任务 completed/failed，
   `amend_task` 会拒绝（`task is completed; terminal contracts are immutable`）。
   所以 **inScope 必须在任务开工前就想全**，事后补救只能靠下游契约"打补丁"。

## 9.10 指标失联：搬家会悄悄掏空"按文件统计"的度量（2026-09-19 实测）

**第六种盲区，也是最隐蔽的一种**：某个校验指标**只读某个文件**，
当被统计的代码**搬去别的文件**后，指标**照样返回 0，却不报错** ——
它会显示成"代码消失了"，而实际只是**度量看不见了**。

**实例**：`webui-invariants.mjs` 原先只 `readFileSync(index.html)`（`:22`），
不读任何 `.js`。内联脚本外移到 `app_*.js` 后：

```
index.html 里 fullscreenPromptOverlay.appendChild : 0   ← 度量在此
app_main.js:1019 同一语句                          : 1   ← 代码其实在这
```

`prompt_editor_append` 由 1 → 0，**看似删了代码，实则只是搬了家**。

**最危险的一步**：用 `--expect=prompt_editor_append=-1` 把它"声明掉"让 rc=0 ——
**等于用一个已经失联的指标为交付背书**。绿灯是真的，约束力是假的。

**纪律**：

1. **搬迁类改动后，逐个检查"哪些指标会因此失联"**。凡是"按文件统计"的指标
   （行数、出现次数、id 计数…），被统计对象换文件后就会**静默归零**。
2. **扩大扫描范围时要动态发现，不要硬编码文件列表**。修法示范：
   从 `index.html` 的 `<script src="./*.js">` **动态提取**文件清单
   （本例发现 25 个），并**在文件缺失时 `exit 2` 拒绝继续** ——
   防止"列表过期 → 扫描范围悄悄缩小 → 假绿"。
   （硬编码列表正是 §9.7 的病根：`SPLIT_JS` 漏了 4 个新文件。）
3. **给"覆盖面"本身加指标**。本例新增 `split_js_files` / `split_js_lines`，
   一旦扫描范围变化就会显式报警，而不是无声地少看几个文件。
4. **不要用"声明 delta"掩盖失联**。`--expect` 是为"已确认的真实变化"准备的通道，
   不是为"指标失效"准备的。发现指标归零时，先问**"它是消失了，还是搬到别处了？"**

## 9.11 纯正则门禁会误伤注释与 fixture：读规则源码再定性（2026-09-19 实测）

**第七种形态**：**纯正则**型安全检查器**不剥注释、不区分字符串字面量**，
于是「注释里提到旧写法」「探针里故意复现旧写法做对照」都会被判为 high。

**实例**：把 t4/t5 的取证脚本入库时，门禁从 `HOOK_EXIT=0` 变为 **`EXIT=1 / 10 条 high`**。
逐条核实后：**10 条里无一条是可执行赋值** ——
全是「注释文本」或「CDP 探针模板字符串里的旧代码 fixture（OLD 侧对照）」。
而后者**存在的意义恰恰是证明旧写法已被消除**。

**判据来自规则源码**（`deepsec/shield/rules/sast.py:36`）：

```regex
\.(?:innerHTML|outerHTML)\s*=\s*(?!DOMPurify|sanitizeHtml|sanitize|['"`]\s*['"`])[^;\n]+
```

三个必须看懂的细节：
1. **只匹配点号属性访问**（`.innerHTML =`）→ `el["innerHTML"] = …` 天然不在匹配内
   （**两者语义完全等价**，不是"绕过"，是正则本就只针对点号形态）
2. **已内置豁免**：`DOMPurify` / `sanitizeHtml` / `sanitize` / 空串赋值
3. **纯正则**：不剥注释、不区分字符串 → 注释与 fixture 必被误伤

**纪律**：

1. **看到 high finding 先读规则源码，再定性**。规则名（`sast_xss_inner_html`）
   不等于真实语义；本例中"HTML is assigned directly to the DOM"这条描述
   对**读取**（`a.innerHTML === b.innerHTML`）和**字符串里的**写法同样成立 —— 都是误报。
2. **改写优先于抑制，且标准对所有人一致**。虽然 `.deepsecignore` 支持
   `files: {<target>: [rule_ids]}` 的精准抑制（`ignore.py:17,26,39`），
   但若契约写了"禁止新增 ignore 规则"，就**不能因为自己碍事而破例** ——
   同一个标准要能约束所有人（t3 把 7 条全部改写消除、零新增 ignore）。
3. **改写手法要精确且可验证**：注释改措辞（不逐字复现触发形态）；
   fixture 改 `el["innerHTML"]` 方括号形态。**但必须保持 fixture 仍能重现旧行为**，
   否则 A/B 对照失效、变成"两边都过"的假阳性。
4. **入库"证据脚本"会改变门禁状态**。取证脚本里常**故意包含**危险形态（做对照），
   把它们的提交会引爆门禁 —— 这属**预期行为**，不要用 `--no-verify` 绕过。

---

## 10. 「统一」的边界：需要的才统一

**不要为了形式一致而强行统一语义不同的控件。** 判据是**语义**，不是外观。

实例（2026-09-18）：6 个服务槽位中，5 个改造为连接列表，
**`agent` 明确不改造** —— 它是「后台委派求解器」（provider 二选一 + gateway 地址），
与「模型推理服务」（URL + Key + Model）语义不同，统一形式反而误导用户。

同理，主模型（LLM）已确定为本地，**不需要**云端/本地切换 —— 保留它才是问题。

**做法**：改造前列一张表，**逐项说明改与不改的理由**，交由用户确认。

---

## 附录：现成契约速查

| 类 / 变量 | 用途 | 位置 |
|---|---|---|
| `.settings-section` / `.panel` | 设置卡片 | `webui-css-patch.css` |
| `.action-row` | 按钮行容器 | 同上 |
| `.service-row`（+`.collapsed`） | 可折叠服务行 | 同上 |
| `.mode-chip .cdot.ok/.warn/.err/.off` | 状态灯三态 | 同上 |
| `.sdot.ok/.err/.pending` | 健康菜单状态点 | 同上 |
| `.latency-pill` / `.latency-menu` | 顶栏链路胶囊与浮层 | 同上 |
| `.model-field` | 模型输入框 + 获取模型按钮 | 同上 |
| `.conn-mgr` / `.conn-item` / `.conn-act` | 连接列表（方案 X） | 同上 |
| `.result-header-tools-hidden` | 结果卡头部工具区隐藏 | 同上 |
| `#liveModeSection` / `.live-mode-stack` | 设置页 Live 常驻模式区块（高级） | 同上 |
| `#wakeAsrSection` | 设置页 Wake / ASR 兜底唤醒卡（高级） | 同上 |
| `#advancedPanel`（`display:contents`） | 高级面板包裹层 | `index.html` |
| `.toggle-switch` + `.toggle-slider` | 全站勾选类控件唯一写法 | `styles.css` |
| `#fullscreenVlmOverlay`（+`.is-empty`） | 全屏字幕浮层（只显示 AI 回复；空则隐藏） | `index.html` + 补丁 |
| `--shadow` / `--shadow-lift` | 双主题阴影（唯一正确来源） | `styles.css` 主题块 |
| `window.JoyConfig` | 服务配置 / 连接列表 / 测试 / 模型列表 | `config_services.js` |
| `window.JoyI18n.localizeUiString` | 动态文案翻译 | `i18n_device_label.js` |

### 自测脚本清单

| 脚本 | 判据 | 何时跑 |
|---|---|---|
| `scripts/webui-invariants.mjs` | 结构守恒 + **逐 id 集合比对** | 任何 HTML/CSS 结构性改动 |
| `scripts/check-button-wrap.mjs` | 全部面板带文字按钮无竖排（实测几何） | 任何按钮/布局改动 |
| `scripts/check-fullscreen-parity.mjs` | 全屏两态统一 + 布局 + 字幕（17 项） | 涉及输入栏 / 全屏 / 字幕 |
| `scripts/check-advanced-relocation.mjs` | 卡片面板归属 + 实时按钮 + 开关形态（12 项） | 涉及设置面板归属 |
| `scripts/webui-css-patch.mjs` | 补丁追加 + 大括号净差 + **标记恰好 1 份** | 改 `webui-css-patch.css` 后 |
| `scripts/audit-css-loss.mjs` | **规则级**损失审计（比行数可靠） | 任何 CSS 去重/删除后 |
| `scripts/inspect-fullscreen*.mjs` | 全屏结构 / 层叠命中 / 修复验证（取证用） | 排障 |

---
