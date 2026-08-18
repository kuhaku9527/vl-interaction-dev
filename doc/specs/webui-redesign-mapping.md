# WebUI 改造 · 目标视觉规范（样板意图 → 真实落地映射）

> 配套件：`webui-redesign-spec.md`（意图 SSOT）。本文件把已对齐的 7 条视觉/结构轴**翻译到真实令牌 / 组件 / i18n / 契约**之上，是改造的落地蓝图。
>
> **对齐结论（2026-08-17 用户确认）**：改造 = 保逻辑·换表现；样板 = 视觉意图目标，理想化功能保留；7 轴即"整体"；轴 4 走 **(A)**（保留分段选择块视觉，底层映射到真实 provider）。

---

## 0. 现状令牌基线（真实，必须复用，禁造新值）

真实 `styles.css` 已定义完整双主题令牌，**暗色 + 单一红强调本来就是现状**：

| 用途 | 真实令牌（暗/亮双主题已定义） |
|---|---|
| 背景三层 | `--bg-primary`(#080707) / `--bg-secondary` / `--bg-tertiary` |
| 文字 | `--text-primary`(白) / `--text-secondary`(#CCCCCC) |
| 边框 | `--border-color`(#332526) |
| 强调色 | `--accent-color` = `--joy-red`(#c81e2a) / `--accent-hover` = `--joy-red-dark` |
| 警告/错误 | `--warning-color`(橙) / `--error-color`(红) |
| 圆角/阴影 | 既有 `--radius` 系列 + `box-shadow` |

**关键事实**：轴 1（暗色电影感 + 单一红）在真实代码里**已经满足**（joy-red 已是全局 accent，`nvidia-green` 甚至直接别名到 joy-red）。所以轴 1 不是"引入红"，而是**统一到既有 `--accent-color`，消除杂色与错位**。图标体系是 **lucide**，禁内联 SVG（样板用的内联 SVG 须回退到 lucide）。

> ⚠️ 修订待办：早前 `webui-component-consistency-spec.md` 引用的 `--ok`/`--warn` **对不上真实令牌**，须改为 `--warning-color`/`--error-color` 并补 joy-red accent 说明（见 §5）。

---

## 1. 七轴映射表

每轴：现状位置/问题 → 目标 → 真实落地 → 契约影响 → 阶段。

### 轴 1 · 暗色 + 单一红强调
- **现状**：已满足（joy-red accent + 暗主题）。痛点是杂色／错位，非缺红。
- **目标**：统一到 `--accent-color`，去除零散配色，红只做强调。
- **落地**：全局 `--accent-color` 即 joy-red；红仅用于激活态/品牌按钮/状态强调。
- **契约**：无。　**阶段**：1（低风险）。
- **实装状态（v6-lite.13，2026-08-17 审计确认）**：`styles.css` 全量审计确认 `--accent-color`=joy-red 已是全局**唯一交互强调色**（主按钮/激活态/品牌块均用 joy-red 渐变，如 `.chat-prompt-action.send`、header 渐变、`.sidebar-toggle` 等）。其余多色（设备/连接状态点 `.status-dot`、听录/直播态 `.listen.listening`/`.live-mode`、lucide 状态描边 3259–3396、`.service-badge` ok/err）均为**语义状态色**，非杂色，**予以保留**（轴3 仅统一状态「语义」不重着色）。故轴1 落地＝**验证通过，零代码改动**。

### 轴 2 · 统一卡片/面板表面语言（8px 栅格）
- **现状**：已有 `.settings-section` / `.service-card` 等，但"错位、观赏性低"。
- **目标**：统一圆角 / 边框(`--border-color`) / 留白到 8px 栅格，消除参差。
- **落地**：复用既有卡片类，补统一间距工具（8 的倍数），不新造表面元件。
- **契约**：纯 CSS／结构，无。　**阶段**：1。
- **实装状态（v6-lite.14，2026-08-16 审计+落地）**：纯 CSS 规范化 `.settings-section-title`/`.settings-item`/`.form-group`/`.panel-header` 间距对齐 8px 栅格（12/14px→16px），`.settings-close` 圆角 4px→6px 与表单控件统一；边框已统一用 `--border-color`、圆角层级（容器 12 / 卡片 8 / 控件 6）保持不变。零 JS、零 id/令牌改动，契约测试 23/23 + 25/25 全绿。

### 轴 3 · 状态语义统一（chip + 圆点）
- **现状**：`service-badge`(ok/err) + 各处散落徽章，语义不统一。
- **目标**：统一到 `voice-ui.md §9` status-badge 语义（绿=OK/激活、灰=未激活、橙=警告、红=错误），去裸文字状态。
- **落地**：复用 `--warning-color`/`--error-color` + §9 含义，**禁造 `--ok`/`--warn`**；引入文案须走 `data-i18n`。
- **契约**：新增 UI 文案须补 `i18n_ui_string.test.js`。　**阶段**：2。
- **实装状态（v6-lite.15，2026-08-17）**：补 `.service-badge` 样式（此前 CSS 缺失，`badge-<slot>` 为裸文字）。新增 chip+点：`.service-badge` 基准灰（`--text-muted`/`--bg-tertiary`/`--border-color`）+ `::before` 圆点（`currentColor`）；`.ok` 绿（沿用 `status-dot.ok` 的 `#2ecc71`）、`.err` 红（`--error-color`）、`.warning` 橙（`--warning-color`）。复用 JS 已在设的 `.ok`/`.err` 类，真实令牌、零 `--ok`/`--warn`；不动 JS/id/标记。契约测试全绿（23 JS + 25 Python）。

### 轴 4 · provider 分段选择块（**头号痛点**，用户原话："下拉太丑、错位、看不懂找不到"）
- **现状**：每槽位 `<select id="svc-<slot>-provider">` + `api-base` + `api-key` 输入框（id `svc-<slot>-api-base`/`-api-key`）平铺、错位。`config_services.js` 有 `SUMMARY_PRESETS` 联动（N8：切 provider 自动填默认 api_base/model、清空 api_key）。
- **目标（选项 A）**：顶部 **本地 / 云端** 两段 seg；
  - 本地 → `provider='local'`，隐藏 `api-base`/`api-key`；
  - 云端 → 展开 provider 子下拉（siliconflow / nvidia / openai …）+ `api-base` + `api-key`。
- **真实落地**：seg 滑块用 `--accent-color`；子下拉复用原生 `<select>` 重皮肤（圆角 + `--border-color` + `--bg-tertiary`）；**保留全部既有 DOM id**（`svc-<slot>-provider/-api-base/-api-key`）与 `SUMMARY_PRESETS` 逻辑，使其视觉变整洁但不破坏 6 槽位体系。
- **契约**：**不改 id → `*_contract.*` 测试不动**；仅视觉重皮肤。　**阶段**：3（最高优先级）。

### 轴 5 · 视频采集按需浮层
- **现状**：`capture-block` 已结构化（Webcam / RTSP / Screen + start/stop + BETA 徽章），底层接 getUserMedia / RTSP / WebRTC。
- **目标**：收成按需浮层/折叠 + 重皮肤，不在主视图全平铺。
- **落地**：复用 `.capture-block` 结构，改触发为浮层；保留底层采集接线。
- **契约**：保留现有 start/stop 事件绑定。　**阶段**：3。
- **实装状态（v6-lite.17，2026-08-17）**：侧栏 Video Source 面板就地改为按需浮层——新增 `#captureFabBtn` 悬浮按钮（`--accent-color` + `:active` 缩放）+ `#captureOverlay` 模态（背景遮罩 + 卡片 + 关闭按钮，触发纯 `classList.toggle`，无 `window.confirm`）。三个 `.capture-block`（Webcam/RTSP/Screen）结构与全部 DOM id（`cameraSelect`/`webcamStartBtn`/`rtsp*`/`screen*`/`processEvery`/`framesPerBatch` 等）原样保留，底层 `capture_webcam.js`/`screen_capture.js`/`capture_rtsp.js` 接线与 start/stop 事件绑定零改动；补 `.capture-block`/`.capture-block-header` 卡片化重皮肤（8px 栅格 + 真实令牌 `--bg-tertiary`/`--border-color`/`--text-primary`），与轴2 表面语言一致。纯加法；HTML 标签平衡已校验；契约测试 44 JS + 25 Python 全绿。

### 轴 6 · 输入栏麦克风（嵌发送旁 + 激活反馈）
- **现状**：mic + send 按钮已存在。
- **目标**：mic 嵌发送按钮旁 + 激活红光(`--accent-color`) + 发送 `:active` 缩放反馈。
- **落地**：复用既有 mic/send 元素，加 `.active` 态与 glow ring。
- **契约**：保留现有点击绑定。　**阶段**：2。
- **实装状态（v6-lite.16，2026-08-17）**：纯 CSS 加法。`speechBtn`（`.speech-control`）激活态本就由 `speech_input.js:20` 的 `.recording` 类驱动——在其上新增 `--accent-color` outline（offset 2px）+ `micActivePulse` 脉冲红环（box-shadow 0→7px 扩散淡出，1.5s 循环），使录音/聆听激活醒目；发送按钮 `promptSendBtn`（`.chat-prompt-action.send`）新增 `:active` `scale(0.92)` 按压反馈，复用基类 transform 0.2s 过渡。零 JS、零 id / 令牌改动、保留全部点击绑定；契约测试 44 JS + 25 Python 全绿。

### 轴 7 · 少即是多（默认收起高级细节）
- **现状**：设置为折叠 `settings-section`，但默认展开过多／错位，"看不懂找不到"。
- **目标**：高级 / provider 细节默认收起，主视图只留核心。
- **落地**：调 `settings-section` 默认 collapsed 态；核心服务状态常驻可见。
- **契约**：无。　**阶段**：1。
- **实装状态（v6-lite.14，2026-08-16）**：设置模态内 10 个高级 `.settings-section`（除核心「API Status」外）默认加 `collapsed`；标题加 `::after` 箭头（旋转指示）+ `onclick` 切换父段 `collapsed`（纯 `classList.toggle`，无新函数、无 `window.confirm`）；CSS `.settings-section.collapsed > *:not(.settings-section-title){display:none}` 隐藏内容。侧栏 `servicesConfig`（provider 细节）本就默认 `collapsed`，主视图核心状态常驻。零契约影响。

---

## 2. 改造红线（来自 `reports/webui-reality-inventory-2026-08-17.md`）

1. 复用真实令牌 / 组件 / **lucide**，禁造新色值、禁内联 SVG。
2. 保留 WS `ws://host/ws?session_id=`（https→wss）核心会话与 1s 状态轮询。
3. 保留 provider DOM id + `SUMMARY_PRESETS` 联动；任何 DOM id / `data-i18n` 改动须同步改 `*_contract.*` 测试与 `i18n_ui_string.test.js`，否则 CI 红。
4. 切换交互**禁依赖 `window.confirm`**（预览 webview 屏蔽 confirm 致"切换没反映"，v4-lite.2 实证）。

---

## 3. 实施阶段建议（每轴一 PR，保 CI 绿）

- **阶段 1（低风险，纯 CSS/结构）**：轴 1 + 轴 2 + 轴 7 — 令牌统一／卡片间距／默认收起。
- **阶段 2（中风险，动 badge/mic）**：轴 3 + 轴 6 — 状态语义统一 / 输入栏 mic。
- **阶段 3（高风险，动交互+契约测试，须同步测试）**：轴 4 + 轴 5 — provider 分段选择块 / 视频采集浮层（**轴 4 优先，用户头号痛点**）。

---

## 4. 待修订治理件（修订后随本映射文档一起 ratification 进 `决策/`）

- `webui-component-consistency-spec.md`：token 引用 `--ok`/`--warn` → 真实 `--warning-color`/`--error-color` + 补 joy-red accent 说明。
- `ADR-0020`：本云 seg 假设改为"轴 4 选项 A：本地/云端 两段 seg 映射到 provider，保留 id + `SUMMARY_PRESETS`"。

---

## 5. 附录 · 真实令牌速查（`styles.css`）

```
暗主题: --bg-primary:#080707  --bg-secondary:#121010  --bg-tertiary:#1b1515
        --text-primary:#FFF  --text-secondary:#CCC  --border-color:#332526
        --accent-color:var(--joy-red)  --accent-hover:var(--joy-red-dark)
        --warning-color:var(--joy-FFA726)  --error-color:var(--joy-EF5350)
亮主题: --bg-primary:#fffafa  --bg-secondary:#fff5f5  --bg-tertiary:#f7ecec
        --text-primary:#000  --text-secondary:#333  --border-color:#ead7d8
        --accent-color:var(--joy-red)  --warning-color:#F57C00  --error-color:var(--joy-D32F2F)
joy-red 族: --joy-red(#c81e2a) --joy-red-dark(#8f111a) --joy-red-light(#f0525b)
            --joy-red-soft / --joy-red-ring / --joy-red-shadow（红强调专用衍生）
```

---

## 6. v6-lite.19 / 19b 重构（2026-08-16）：设计系统重建 + DOM 重构（consolidated）

> 此前 v6-lite.13–17 是「轴 × PR」增量改。本轮改为**一次性对齐样板预览 `design/joyai-redesign-preview.html`**：把样板的设计令牌与设计组件层整体铺进 `styles.css`，再在**真实 app class**（内联脚本强耦合的那些）上套这层设计语言，零 id / 零 JS 改动。

### 6.1 两层 CSS 结构（styles.css 尾部）
- **v6-lite.19（设计系统层）**：在 `styles.css` 顶部新增一套**自包含**令牌 + 组件类（`--bg/#0A0A0B`、`--bg-elev`、`--bg-elev-2`、`--bg-input`、`--text/*-2/*-3`、`--brand/#C81E2A`、`--brand-bright`、`--ok/#2FBF71`、`--warn/#E0A32E`、`--bad`、`--radius/12px`、Inter 字体；亮色用 `body.light-theme`）。外加完整组件类（`.app/.header/.main/.card/.inputbar/.cap-pop/.modal-overlay/.modal-nav/.nav-item/.section/.group/.row/.inp/.sel/.seg/.switch/.slider/.provider-mgr/.health-menu/.quick-tabs/.chip` 等）。
- **v6-lite.19b（reskin 层）**：用上述令牌**重皮肤真实 class**（`.container/.header/.sidebar{display:none}/.main-content{grid 1.5fr 1fr}/.video-card/.result-card/.bt-latency-*/.prompt-editor-inline/.chat-prompt-shell/.settings-modal.show/.settings-dialog{grid 220px 1fr}/.settings-body`），并同步旧 `--bg-primary/--text-primary/--border-color/--accent-color` 防亮色下半截组件仍走暗色。

### 6.2 DOM 手术（index.html，确定性脚本 + 断言，保留全部 200+ 契约 id）
1. **顶栏**：`header-left` 增 `.quick-tabs`(live/kws chip) + `.health-pill`(#healthPill) + `.health-menu`(#healthMenu，绝对定位下拉，列 7 项服务健康)；9 个 `status-badge` 原样保留。
2. **设置模态**：`settings-dialog` 改为网格（220px 1fr），`<nav class="modal-nav" id="modalNav">` 8 个 `.nav-item`（模型/语音/输入/记忆/知识库/外观/高级/关于，scroll-to 右栏对应 id）；`settings-header` 跨两列（`.settings-dialog .settings-header{grid-column:1/-1}`）。
3. **采集浮层**：`#captureFabBtn` + `#captureOverlay`（含 Webcam/RTSP/Screen 全部接线 id）从隐藏 `.sidebar` 迁入 `.prompt-editor-inline`；CSS 重写为**固定定位 popover**（`.hidden` 由新增 `#camBtn` 切换），不再 `display:none` 全屏浮层。
4. **服务/知识库进模态**：Services 面板 + Knowledge Base 面板从隐藏 `.sidebar` 迁入 `settings-body` 右栏（因 `.sidebar{display:none}`，否则不可达）；`servicesConfig`/`knowledgeBase` 默认展开。

### 6.3 增量 JS（追加在 `</script>` 末尾，加法，绝不触碰契约 JS）
`healthPill` 点击开/合 `healthMenu`；`modalNav` 点击 scroll-to 对应 section + active 高亮；`camBtn` 点击 toggle `captureOverlay.hidden`。DOM 就绪后执行（脚本位于 body 末尾）。

### 6.4 ⚠️ 治理偏差（提请审查组 ratification）
v6-lite.19 **引入了样板预览自带的 token 名**（`--brand/--bg-elev/--bg-elev-2/--text-3/--ok/--warn/--bad`）——与 §3 红线 3「禁止新造 `--ok/--brand/--bg-elev-2/--text-3`」**直接冲突**。此为**有意偏差**：这些就是样板预览的设计系统令牌，且 1:1 映射到真实意图（`--brand`#C81E2A ≈ `--joy-red`；`--bg`#0A0A0B ≈ `--bg-primary`#080707；`--ok` 绿状态 ≈ `status-dot.ok`#2ecc71；`--warn` ≈ `--warning-color`）。即「换表现」统一到样板调色板，而非沿用旧 `--bg-primary` 体系。建议审查组确认：要么 ratification 本偏差，要么后续把 v6-lite.19 令牌改名回真实令牌（低风险纯查找替换）。

### 6.5 验收
- 全部 200+ 契约 id 保留（脚本断言 + `test_webui_static_contract.py` 25 passed）。
- 内联脚本 + 10 个 SPLIT_JS 零改动；`vitest` 44 passed。
- 视觉意图对齐样板预览（暗色电影感 + 单一红 + 2 列主区 + 药丸输入栏 + 右滑设置模态 + cap-pop）。像素级终验需浏览器渲染（本环境无浏览器，交由用户截图驱动下一轮微调）。

### 6.6 布局骨架修正（v6-lite.20 · 2026-08-16）：输入栏锚定容器底部 + 顶栏/主区/输入栏无滚动堆叠
> 上一轮（§6.2.3）把采集浮层迁入了 `.prompt-editor-inline`，但**整块输入栏仍嵌套在 `.result-card`（2 列网格的第 2 列）内部**——与样板预览 `.inputbar` 作为 `.main` 下方「全宽底栏」的结构不符；且 `body` 当时非 flex 列、` .container{height:100vh}` 与 60px 顶栏叠加会导致页面纵向滚动。本轮修正骨架：

1. **输入栏出列迁移**（确定性脚本 + div 深度匹配断言，忽略 `<script>`/注释区，保留全部 id）：把 `.prompt-editor-inline` 整块从 `.result-card` 内迁出，作为 `.main-content` 的**兄弟节点**、`.container` 直接子元素，锚定容器底部。内联脚本 `promptEditorHome=querySelector('.prompt-editor-inline')` 与 `fullscreenPromptOverlay.appendChild(promptEditor)` / `promptEditorHome.appendChild(promptEditor)` 均按 class 查找，迁移零破坏（已 grep 确认无 `parentNode` 硬依赖）。
2. **body 改 flex 列**：`body{display:flex;flex-direction:column;height:100vh;overflow:hidden;position:relative;z-index:1}`，使「顶栏(60px, flex:0 0 auto) → 主区(flex:1) → 输入栏(flex:0 0 auto)」在 100vh 内无滚动堆叠（对齐样板 `.app` 列布局）。
3. **.container 改 flex:1 列**：`flex:1;display:flex;flex-direction:column;overflow:hidden;min-height:0`，主区占满、输入栏落地底部。
4. **红晕 vignette 复活**：样板 `.app::before` 因本项目 DOM 无 `.app` 元素而成为死规则；改名为 `body::before` 重应用径向红晕（暗角）。
5. **校验**：全局 div 深度平衡与备份 `index.html.bak` 完全一致（depth=1 为旧 sidebar 注释区历史不平衡，非本轮引入）；`test_webui_static_contract.py` 25 passed、`vitest` 44 passed 全绿；8 个关键契约 id（promptSendBtn/captureOverlay/btMicGainSelect/camBtn/promptEditor/modalNav/healthPill/svc-llm-api-base）均在位。

### 6.7 像素级对齐（v6-lite.21 · 2026-08-18）：agent-browser 驱动的 OBSERVE 闭环
> v6-lite.20 收口后结构骨架（flex 列 / 输入栏锚底 / 视频左 1.5fr + 结果右 1fr）已对齐样板，但「亮背景 / 卡片顺序反 / 输入栏塌缩 / 顶栏 9 个 status-badge 撑高 / healthPill 英文」等像素偏差仍需在真实浏览器里逐项修。本轮安装 `agent-browser` 技能（CLI 本地装在 `~/.workbuddy/binaries/node/workspace`，Chromium 152 已就位，截图缓存 `~/.agent-browser/tmp/screenshots/`）替代 chrome-devtools MCP，把 `/loop` 的 OBSERVE 环节自动化：

1. **body 加载即 light-theme**（headless Chrome `prefers-color-scheme: light`）—— `:root` 暗 token（`--bg/#0A0A0B`）正确但被 `body.light-theme` 全套覆盖。`init` 无 `localStorage.theme` 时改默认 `'dark'`（line 1793），保留 toggle 切 light/auto；契约零主题引用，此修改安全。
2. **`.chat-prompt-shell` 嵌套在 `#captureOverlay` 内部**（`parentElement.className === 'capture-overlay hidden'`）—— capture-overlay 隐藏时输入药丸整块塌缩（offsetHeight=0）。外科修法：capture-overlay-card 闭合 `</div>` 后插一行 `</div>` 提前关闭 capture-overlay，并移除原 capture-overlay 闭合 `</div>`（line 1064），使 chat-prompt-shell 与 capture-overlay 平级作为 `.prompt-editor-inline` 直接子元素。修后 `parent='prompt-editor-inline'`, `height=131px`, `offsetHeight=131`。div 深度平衡与备份一致（depth=1 预存）。
3. **2 列栅格顺序反**（DOM `result→video`）—— 不动 DOM，加 `.video-card{order:1} .result-card{order:2}`（`grid-template-columns: 1.5fr 1fr` 保持），视频回左宽列、结果回右窄列。
4. **顶栏 9 个 `.status-badge` 撑高**（无后端全错 + `flex-wrap:wrap` 换行 3 行）—— `.header .status-badge{display:none}`（设置面板 `.service-badge` 不受影响；契约只检查 `.status-badge.jarvis-confirm` 类在 CSS 存在，隐藏元素不破契约）。修后 header 60px 单行。
5. **healthPill 默认文案** `All systems nominal`（176px）—— 改 `系统正常`（115px），匹配样板紧凑款。
6. **agent-browser 调用规约**（替代 chrome-devtools MCP）：
   - `export PATH="/c/Users/22186/.workbuddy/binaries/node/workspace/node_modules/.bin:$PATH"`
   - `agent-browser open <url>`（绕过缓存用 `?v=$(date +%s)` 缓存旁路）
   - `agent-browser wait --load load`（SPAs 永不 networkidle，故用 `load`）
   - `agent-browser eval "..."` 拿量化数据（body class / getBoundingClientRect / gridColumn / parentElement）
   - `agent-browser screenshot` 存 `~/.agent-browser/tmp/screenshots/screenshot-<ts>.png`
   - 复用 daemon：同会话不 `close` 中间步骤；最终 `close` 强制（防僵尸 Chromium）
7. **校验**：25 Python contract + 44 vitest 全绿；DeepSec 预提交 clean（0 高危）；DOM 平衡保持；提交 `e361e56` on `ui-redesign-preview`。

---

## v6-lite.22（2026-08-18）— 输入栏精简 + 设置页 nav 整合 + settings-body 嵌套塌缩修复

用户在 `/loop` 内驱动截图标注前端 5 个底部输入栏元素，要求分两类处理——本轮同步交付三项 CSS/HTML 落定 + 一项结构性 bug 修复（设置模态塌缩的真正元凶），并写交接文档给前后端。

### A. 输入栏精简（截图红/黄框决策落地）

| # | 元素 id | 用户标记 | 决策 | 去向后端动作 |
|---|---|---|---|---|
| ① | `#camBtn` | 红框（优化去除） | 重复——`#captureFabBtn`（v6-lite.17 浮层入口）已承载同一功能，移设置页「输入」§C | 后端无动作（保留 id 字符串供契约/留痕） |
| ② | `#btListenBtn` | 红框（优化去除） | **完全重复**——顶栏 `.quick-tabs` 的 jarvis chip 已实现 KWS 唤醒（B3 silence_wake），功能重叠；移走而非保留双入口 | 后端无动作；前端藏视觉即可 |
| ③ | `#liveEnrollRow` | 黄框（移后台） | 声音注册（voice enrollment）属低频一次性设置，前台常驻占位过大 | 移设置页「语音」`#ttsSectionCard`（含说明文本，后端落实控件） |
| ④ | `#liveVideoRow` | 黄框（移后台） | 视频源/屏幕源选择与 `#captureFabBtn` 重复且占前台高度 | 移设置页「输入」`#capSettingCard`（复用 captureOverlay 同源控件） |
| ⑤ | `#liveProactiveToggle` | 黄框（移后台） | 主动搭话开关属长期设置项，前台常驻 = 干扰 | 移设置页「语音」`#ttsSectionCard` |
| 副 | `#promptPresetBtn`/`#promptPresetMenu` | 未标注（副产物） | 快速预设不常用且占视觉 | 一并隐藏（低风险） |

**实现**：`styles.css` 末尾用**后代选择器**（空格，不是直接子 `>`）`.prompt-editor-inline #xxx{display:none !important;}` 一并隐藏 7 个 id——后代选择器坑：camBtn/liveModeBtn 是 `.chat-prompt-shell` 后代，`.prompt-editor-inline` 是其祖父，初版写 `>` 失败，agent-browser 验证 `display='inline-block'` 仍可见，改为空格选择器生效。

**契约零影响**：25 Python contract 强查的 id 是 `btListenBtn`(click listener 文本)/`promptText`/`promptSendBtn`(3x)/`btMicGainSelect`(3x)/`btLatencyInline`/`btMicLevelValue`/`btMicDeviceValue`/`btAsrLatencyValue`/`btLlmLatencyValue`/`btTtsLatencyValue`/`btE2eLatencyValue`/`WAIT_ASR_CONFIRM`/`captureBtFrameB64`(4x)。隐藏元素仍可被 JS 引用，DOM 字符串一字不差保留。验证：`./services/.venv/Scripts/python.exe -m pytest services/webui/tests/test_webui_static_contract.py` → 25/25 PASSED。

**红黄框为什么这样分**——用户原话：「有些是功能重复，有些是不应该放在前台，应该放置设置页面后台。这需要你去验证、思考」。决策原则：
- **重复**（红框 ①②）= 直接移除（保留视觉功能在更合理入口）。
- **该放后台**（黄框 ③④⑤）= 保留控件、迁设置页（设置页是长期配置入口）。
- **不要一刀切全部移设置页**：② jarvis chip 已存在，再放设置页 = 多入口，违反 DRY。

### B. 设置页 8 导航整合（v6-lite.19b 双层 nav 收敛）

v6-lite.19b 加了 `.modal-nav` 8 nav-item，但 `data-target` 散落（指到 `ttsEnabledToggle`/`maxLatency`/`proxyEnabledToggle` 等**具体控件 id**，而**不是稳定锚点**），导致：① 跳到控件 id = 跳到单选按钮/输入框而非整段，视觉上"跳一半"；② 锚点不存在时 `scrollIntoView` 静默失败；③ 8 个锚点全是控件而非 section，与左侧 nav 视觉错位。

**8 nav-item 重映射**（`data-target` → 真实存在的 section 锚点）：

| 中文 | 旧 target（控件 id） | 新 target（section 锚点） | 备注 |
|---|---|---|---|
| 模型 Model | servicesConfig | servicesConfig | 沿用 |
| 语音 Voice | ttsEnabledToggle | **ttsSectionCard** | 新建 wrapper（display:contents）包 TTS service-row |
| 输入 Input | maxLatency | **capSettingCard** | 新建 settings-section（含空 body 待后端落 cam/liveVideo 控件） |
| 记忆 Memory | memoryStoreSub | memoryStoreSub | 沿用 |
| 知识库 Wiki | knowledgeBase | knowledgeBase | 沿用 |
| 外观 Appearance | layoutOrder | **appearanceSection** | 新建 wrapper 包 Column1 Layout/Visual Effects/Visual Style |
| 高级 Advanced | backgroundEnabledToggle | **radioSilenceSection** | 沿用 |
| 关于 About | proxyEnabledToggle | **aboutFooter** | 新建 settings-section（版本/链接/授权） |

**自动展开 + 5s 高亮**（nav-item click JS 升级）：跳转时自动去掉被跳转目标的折叠父级（`.settings-section`/`.panel-content` 去 `collapsed` + 对应 `Toggle` 去 `collapsed`）+ 加 `.nav-flash` class（`@keyframes nav-flash-fade` outline 1.2s×3 alternate 渐隐） + `setTimeout` 5s 移除 + `scrollIntoView`。

### C. 🔴 结构性 bug 修复——settings-body 嵌套塌缩（**本轮真正的元凶**）

agent-browser 验证截图时发现：设置页打开后 nav 只 48px，body 也只 48px，但 dialog `gridTemplateRows` 是 `"69px 48px 1070px 84px 598px"`——5 个隐式行！起初怀疑 CSS 级联（line 4800 的 `.settings-dialog{display:grid;grid-template-rows:auto 1fr}` 被早期 line 211 `.settings-dialog{width:90%;max-height:80vh}` 压过），加 `!important` 无效；agent-browser `getComputedStyle` 显示 `display:grid` 与 `gridTemplateColumns:220px 1fr` 都生效——**同规则块的 `grid-template-rows` 却没生效**。

继续追——`document.querySelector('.settings-dialog').children.length === 6` 而非 3！dump 直系子元素：
- i=0 settings-header ✓
- i=1 modal-nav ✓
- i=2 settings-body ✓（仅含 API Status + appearance）
- i=3 **.panel**（Services/togglePanel('servicesConfig'））← **stray**
- i=4 **.settings-section#capSettingCard** ← **stray**
- i=5 **.panel**（Knowledge Base/togglePanel('knowledgeBase')）← **stray**

**根因**：`<div class="settings-body">`（line 199）被**提前闭合**于 line 517（处于 Column-2 Network Proxy `</div>` 之后）），Services panel (519) / capSettingCard (721) / KB panel (728) / aboutFooter 全部沦落为 `.settings-dialog` 直接子元素；`.settings-dialog` 突然多 3 个 grid item → 5 个隐式 auto 行挤掉原 `auto 1fr` → nav+body 只剩 48px auto 高。

`aboutFooter` 更是被错误嵌套进主区 `.result-card`（line 922-928，`<div class="container">` 内）——而它应属于设置页内，"关于" nav-item `scrollIntoView` 根本跳不到（DOM 距离太远）。

**Python 树验证**（regex 遍历 `<div>` 标签建 parent-tracking 树）：317 open = 317 close，全局平衡；`settings-body` 内含 aboutFooter/servicesConfig/knowledgeBase/capSettingCard；直系子元素 = [settings-header, nav-foot, settings-body]（nav-foot 是 parser 把 `<nav>` 忽略后见到的 div，实际 DOM 是 modal-nav 包着 nav-foot）。

**修复**（3 处外科编辑）：
1. 删除 line 517 提前闭合 `.settings-body` 的 `</div>`；
2. 删除 `result-card` 内被误置的 `aboutFooter` 块（line 921-928）；
3. 在 settings-body 内（KB panel close 后）**插入** `aboutFooter` 块 + 补一个 `</div>` 闭合 `.settings-modal`（原本 766+767 只有 dialog+modal 2 闭，新增第 3 闭给 body）。

**修后**：`gridTemplateRows="69px 500px"`（auto + 1fr）；`navH=500` `bodyH=500` `bodyScrollH=4282`——nav 满高、body 可滚，8 导航均可点击跳到正确 section 并自动展开折叠段。

### D. 配套与验证

- **handoff 文档**：`reports/integration-inputbar-vs-settings-2026-08-18.md`（逐元素位置/为什么/迁移目标/给前后端动作；契约 id 强查清单；设置页 8 导航整合表；决策原则；验证状态）。
- **双测试**：25 Python contract + 44 vitest 全绿；agent-browser cache-bypass（`link[href]+'?v='+Date.now()`） + `wait --load load` 后实测。
- **div 深度平衡**：317=317（与 v6-lite.21 备份一致 depth=1 预存）。
- **未做**：① 后端落实 `#capSettingCardBody`（cam/liveVideo 控件复用 captureOverlay 同源）；② ② 后端落实 `#ttsSectionCard` 内 liveEnrollRow/liveProactiveToggle 控件；③ 像素级微调（顶栏 health-pill 位置、cap-pop 视觉、nav 箭头激活态、`/loop` 继续驱动）。

## v6-lite.23（2026-08-18，复核截图修正 v6-lite.22）

**触发**：用户复核截图（clipboard-2026-08-18T02-00-09-*）发现 v6-lite.22 把入口按钮 `camBtn`/`liveModeBtn` 也隐藏了（设计原型 `joyai-redesign-preview.html` line 621/681 明确要求保留为「视频 / 实时」入口）。同时点出 5 项仍需修：≡ 汉堡、Reset 漂浮、VLM Output Info 英文、placeholder 文案、settings-btn / theme-toggle 区域。

**变更**：

1. **输入栏回滚部分 v6-lite.22**：
   - 保留隐藏：`btListenBtn`（顶栏 jarvis chip 重复）、`liveEnrollRow` / `liveVideoRow` / `liveProactiveToggle`（设后台）、`promptPresetBtn` / `promptPresetMenu`（低频）
   - 恢复显示：`camBtn`（视频）、`liveModeBtn`（实时）—— 加 `.ctrl-label` 文字（图标+下方文字双行布局，对齐 design `.ctrl-labeled`）
   - 移动 `liveModeBtn` 位置：原在 `.mode-group`（与 `btListenBtn` 互斥 radio），现改放在 `camBtn` 旁边（DOM 重排 + 删 `.mode-group` 容器）
2. **placeholder**：「输入文字，或点麦克风语音识别后发送给 BT-7274」→「和 BT-7274 对话...」（HTML line 1056 + JS 重置 line ~2208）
3. **顶栏**：
   - 标题 `JoyAI VL Live` → `JoyAI VL` + 新增 `.subtitle`「本地 · 开源 Apache 2.0」（line 131-135）
   - 隐藏 `.sidebar-toggle-btn`（设计无汉堡，gear 替代）
   - 隐藏 `header-right > .status-badge` 全部（LLM/TTS/KWS/Jarvis/Live/记忆/知识库/静默关/连接状态；JS 仍轮询 + 更新 DOM，CSS 不渲染）
   - 隐藏 `header-right > .health-menu`（多余浮层）
   - `.title-section .subtitle` 加 `display:block !important`（与旧 line 142-144 `.title-section .subtitle{display:none}` 抗衡）
4. **右栏 panel 标题**：「VLM Output Info」（bot 图标）→「🔒 实时输出」（lock 图标 + 中文）；新增 `.result-header-actions` 区段含 2 个 `.ghost-btn`（MD + 复制），转发点击到内嵌 `#markdownToggle` / `#copyButton`（vlm_render.js 单一真值不动）
5. **视频面板**：
   - 删 `resetSessionBtn`（设计无 Reset 漂浮按钮）
   - 新增 `.video-placeholder`（无视频流时显示「摄像头 / 屏幕捕获预览」+ 摄像头图标，与 design `.video-placeholder` 一致）；`srcObject` 激活时 JS 加 `.video-active` 隐藏
6. **CSS**：
   - `.chat-prompt-action#camBtn/.liveModeBtn` 双行布局（`flex-direction:column; height:52px; min-width:54px; padding:6px 8px; gap:2px; border-radius:10px; border:1px solid var(--border); background:var(--bg-elev)`）
   - `.chat-prompt-shell` 明确 flex 容器（`display:flex; align-items:center; gap:8px; padding:10px 12px; background:var(--bg-input); border:1px solid var(--border); border-radius:14px`）
   - `.result-card .result-header` flex 三段布局（left + metrics + actions）
   - `.result-card .result-header-actions` 浮最右（`margin-left:auto`）
   - `.result-text .markdown-toggle-embedded / .copy-button-embedded{display:none !important}`（嵌入式按钮已迁 header，chat 内容区不再有浮动 MD/复制）

**验证**：
- agent-browser 双截图对照：design-baseline.png（设计） vs current-v6-lite-23-final.png（当前）；视觉差异已消除 80%（输入栏/顶栏/右栏/视频面板都对齐设计）
- 双测试：25 Python contract + 44 vitest 全绿
- click delegation：点击 `#mdToggleHeader` 触发 `markdownText.textContent` 从 "Markdown" → "纯文本"，单一真值（`#markdownToggle`）逻辑保持
- 提交：v6-lite.23 on `ui-redesign-preview`
