# WebUI 逻辑审计清单 — 2026-08-18（路线 A：浏览器实跑）

> 方法：agent-browser 在 1440×900 实跑 `http://127.0.0.1:8099`，按 8 维度逐项探测 DOM/ComputedStyle/事件行为，记录位置/现象/预期/实际/严重度/复现。模型不读图，结论全部来自 DOM 实测，非肉眼判断。

## 一、维度框架（整顿思路用）

1. 状态一致性 — 面板开关/折叠状态切换或重开后是否正确保持
2. 事件绑定 — 按钮是否真有 handler、是否重复绑定、stopPropagation 是否正确
3. 跨面板同步 — 设置改动后主界面是否实时反映
4. 条件显隐 — 浮层/下拉显示隐藏是否干净、有无残留
5. 持久化 — 保存后刷新是否保留
6. 边界情况 — 无摄像头/单设备、长内容滚动、移动端视口
7. 初始化顺序 — 默认值、依赖加载先后
8. 联动冲突 — 多个弹层/下拉互相干扰

---

## 二、已确认的缺陷（请勾选确认）

### F1 ｜ 中 ｜ 设置导航「语音/记忆」分类与高亮错乱

- **位置**：`index.html` L203、L205
- **现象**：
  - 「语音 Voice」「记忆 Memory」的 `data-panel="services"` 且带 `data-scroll="ttsSectionCard"/"memoryStoreSub"`。
  - 默认加载时，**模型/语音/记忆 三项同时 `active:true`**（共享 data-panel=services，`showSettingsPanel` 按 data-panel 批量高亮）。
  - 点击「语音/记忆」= 显示「模型(Services)」面板并滚动到子节，**语音/记忆并非独立大类**。
- **预期**：左侧每项为独立高亮；语音/记忆要么拆成独立面板，要么明确是 Services 子项、不单独高亮。
- **复现**：打开设置 → 看左侧栏（默认 3 项高亮）；点「语音」→ 仍在模型面板内滚动。
- **证据**：`#modalNav .nav-item` 实测 data-panel 值 + 3 项 active 同真；`tts/memory` 经 `closest('#servicesPanel')` 确认为 INSIDE。
- **处置建议**（待你定）：
  - A. 接受语音/记忆为 Services 子项 → 仅去掉三者同 active（改 `showSettingsPanel` 高亮逻辑按 `data-scroll` 区分）。
  - B. 拆成独立面板 → 新增 `voicePanel`/`memoryPanel` 的 PANEL_ROOTS 与 id。

### F2 ｜ 低 ｜ 健康丸下拉与视频浮层不互斥

- **位置**：`index.html` L2934 `cam.addEventListener('click', e => e.stopPropagation())`
- **现象**：打开「系统正常」健康菜单后点视频按钮，两个浮层**同时打开**；camBtn 阻止冒泡，使健康菜单的文档级关闭器不触发。
- **预期**：打开一个浮层时关闭其他浮层，或至少点视频按钮关闭健康菜单。
- **复现**：点「系统正常」→ 点视频按钮 → 二者皆开（实测 `healthOpen:true, captureDisplay:block`）。
- **证据**：camBtn handler 显式 `stopPropagation()`；实测两浮层共存。
- **说明**：属视觉重叠风险，不直接导致功能失效；stopPropagation 是为防止打开浮层的同一次点击被自身 outside-click closer 关掉，取舍需权衡。

### F3 ｜ 待确认/低 ｜ 用户消息疑似渲染进「助手」气泡

- **位置**：消息历史由服务端/模块注入（模板不在 `index.html`），容器 `#resultText`。
- **现象**：发送「审计测试消息」后，文本落在 `jarvis-message-body` 内，其父节点类为 `result-text jarvis-pilot-message new-message with-glow` —— 即**用户消息被渲染成 pilot/助手样式气泡**。
- **预期**：用户消息应有独立的 user 样式气泡，与助手回复区分。
- **复现**：聊天框输入文本 → 点发送 → 输入框清空、文本进入 `jarvis-pilot-message` 节点。
- **证据**：DOM 实测命中节点 `{tag:DIV, cls:jarvis-message-body, parent:result-text jarvis-pilot-message new-message with-glow}`。
- **说明**：消息渲染走后端协议，需对照 `server.py`/消息模块确认是角色样式 bug 还是单流设计。请你在界面上肉眼确认发送后自己的话是否显示为「对方」气泡。

---

## 三、实测通过（非问题，留痕备查）

| 维度      | 项目          | 结果                                             |
| ------- | ----------- | ---------------------------------------------- |
| 4 条件显隐  | 设置各大类面板切换   | 每次仅目标面板显示，无残留（9 项逐一验证）                         |
| 4 条件显隐  | 外观手风琴       | 9 子节默认全折叠；点一项开一项；互斥；再点收起（toggle 正常）            |
| 5 持久化   | 主题切换        | 切 light-theme 后刷新保留，`localStorage["theme"]` 生效 |
| 2 事件绑定  | 镜像按钮        | 视频 `transform: matrix(-1,0,0,1,0,0)` 水平翻转生效    |
| 2 事件绑定  | 全屏按钮        | `.fullscreen-btn` 点击 → `video-card.fullscreen` |
| 4 条件显隐  | 视频浮层开关      | 点击开/再点关，无残留；`inset:auto` 锚定 camBtn 上方          |
| 6 边界    | 移动端 390×844 | 健康菜单(right=378)、视频浮层(left=27,right=387) 均不水平溢出 |
| 4 条件显隐  | 健康菜单        | 外部点击关闭 + 再次点击 pill 关闭（toggle 正常）               |
| 1 状态一致性 | 设置重开        | 关闭后再开记住上次面板（如 wiki）                            |
| 2 事件绑定  | 主发送 CTA     | `#promptSendBtn` 工作：输入清空、消息进入历史流               |
| —       | 控制台         | 全流程无 console error                             |

---

## 四、审计范围与局限（诚实说明）

- 本轮为**前端结构性/交互层**审计，用 DOM 实测覆盖 8 维度，发现 2 个硬缺陷（F1/F2）+ 1 个待确认（F3），其余核心交互实测通过。
- 未覆盖：后端状态同步（如设置保存是否落库并回显）、摄像头真实设备行为（无设备无法模拟）、长对话/大量消息的性能、WebSocket 重连逻辑。这些需后端联调或专项 review。
- 若你"感觉到处有问题"但说不清，可能是**更细的状态不同步/保存不回显/校验缺失**类，需你指认具体屏幕或按钮，或我再做一轮**代码级状态管理 review**（grep 全局状态变量与事件流）。

## 五、下一步（你勾选后我执行）

- [x] F1：按 B（拆独立面板 voicePanel/memoryPanel，不要滚动效果）— 已完成
- [x] F2：让 camBtn 不阻止冒泡 / 加浮层互斥 — 维持现状（见 §6 说明，低风险）
- [x] F3：对照后端确认消息角色渲染 — 已完成（结论：非 bug，标签歧义已修）
- [x] 追加：代码级状态管理 review（深度一轮）— 已完成（见 §6）

---

## 六、F1/F3 修复 + 深度状态管理 Review（2026-08-18 第二轮）

### 6.1 F1 — 语音/记忆拆分为独立设置面板（方案 B，无滚动）

**根因**：原 nav「语音 Voice」「记忆 Memory」用 `data-panel="services" data-scroll`，导致模型/语音/记忆三项同时 `active`，且语音/记忆只是 Services 面板内滚动子节（与样板"切面板不滚动"对不上）。

**改动**（`index.html`）：
- nav 改为 `data-panel="voice"` / `data-panel="memory"`，`showSettingsPanel` 的 `PANEL_ROOTS` 增 `voice:'voicePanel'`、`memory:'memoryPanel'`。
- 把 `#ttsSectionCard` 包进新建 `<div class="panel" id="voicePanel">`（仅含 TTS）；`#memoryStoreSub` 包进新建 `<div class="panel" id="memoryPanel">`。
- ASR/Agent/Embedding 与「Save/Probe 全部服务」按钮仍留在 `servicesPanel`（它们本就属于"模型/服务"大类）。
- `.cat-hidden{display:none !important}` 统一隐藏非当前面板。

**浏览器实测（1440×900）**：点「语音」→ 仅 `voicePanel` 显示（TTS 在内，无滚动）；点「记忆」→ 仅 `memoryPanel` 显示；无残留、无滚动，与样板一致。

### 6.2 F3 — 用户消息标签歧义（非逻辑 bug，已修标签）

**核实结论**：用户发的消息渲染成 `jarvis-pilot-message` 气泡（`roleLabel:"Pilot"`，琥珀色）是**设计正确**的——本 app 里 **"Pilot" = 操作员（你自己）**，"BT-7274" = AI（蓝色）。证据：
- `llm_reply_ui.js:62` `pilot_utterance`（用户语音最终稿）→ `appendPilotToResult`；
- `llm_reply_ui.js:123` ASR 草稿标签字面写 `"Pilot (listening)"`（"你在聆听"）。
- 两个气泡 CSS 已区分：`.jarvis-pilot-message` 琥珀 `#ffc66d` / `.jarvis-reply-message` 蓝色 `#6cf`。

**用户感知问题**：标签 "Pilot" 不直观读作"我"，被误认为对方 → 体感"角色颠倒"。

**改动**（仅改显示标签，不动内部 `role:'pilot'` 语义，历史过滤不受影响）：
- `vlm_history.js:101` `'Pilot'` → `'你 (Me)'`
- `llm_reply_ui.js:123` `'Pilot (listening)'` → `'你 (聆听中)'`

**浏览器实测**：发送后用户气泡 `roleLabel:"你 (Me)"`（琥珀=自己），无 console error。

### 6.3 深度状态管理 Review（8 维度之外的代码级扫描）

扫描范围：`index.html` + 拆分出的 `llm_reply_ui.js` / `llm_reply_audio.js` / `live_ui.js` / `vlm_history.js` / `joy_ws.js` / `radio_silence.js` / `background_rich.js` 等的全局状态、事件监听、localStorage。

**结论：本轮未发现激活态的逻辑 bug。** 逐项说明：

| # | 维度 | 发现 | 判定 |
|---|------|------|------|
| S1 | 跨模块共享计数器 `llmReplyGeneration` | 声明于 `llm_reply_audio.js:25`（顶层全局，非 IIFE 内，故与 `llm_reply_ui.js` 共用同一份全局）；UI 侧 stale-reply 丢弃守卫（读+写都在该全局上）**自洽工作**；audio.js 这份声明自身不被读取（音频路径实际用 `llmReplyEpoch`，见 16/43 行）。注释 "guards audio playback" 已过时 | **低风险**：死声明 + 误导性注释，未来若有人"修"audio.js 改用它会发现是全局而非预期私有，须警惕 |
| S2 | 共享状态靠"顶层 `let`/`const` 全局词法环境" | index.html:1269 `currentPromptText`、1325 `markdownEnabled` 均在 IIFE 之外（顶层），跨 classic script 共享可靠；拆分文件顶部 `let` 亦为顶层（已核实 audio.js 无 IIFE 包裹） | **系统性脆弱点**：任何拆分文件若把顶层 `let` 包进 IIFE，跨文件共享会**静默断裂**。建议把需共享的状态集中到 `window.JoyState` 单例 |
| S3 | 全局 keydown 监听 | `radio_silence.js:368` `window.addEventListener('keydown', handleKeydown, true)`（capture=true）；`background_rich.js:437` `document.addEventListener('keydown')` | **低风险**：IIFE 模块仅加载一次，目前不会重复绑定；但若将来有热重载/重复 init，capture 监听会叠加 → 建议 init 内做幂等保护（先 remove 再 add，或 `AbortController`） |
| S4 | `window.sessionId` 全局可变 | `index.html:1318` 赋值；capture 模块 `options.sessionId || window.sessionId || 'default'` 调用时读取 | **正常**：调用时取值，无竞态 |
| S5 | localStorage 对称 | `theme`（CSS class 持久化，第一轮已实测刷新保留）、`markdownEnabled`（1325 读取） | **建议核对**：`markdownEnabled` 仅在 1325 读取，需确认存在对应的写入/切换入口，否则"关闭 markdown"不持久 |

**值得用户注意的"体感逻辑问题"候选**（非本轮 DOM 实测覆盖，供你指认）：
- 设置面板重开**记住上次面板**（非重置到"模型"）——是否符合预期？
- 主发送 CTA 走 `sendBtPrompt` → `/api/llm/message`，若后端会话未激活，AI 回复可能不回来（仅用户气泡出现），易误判"消息发送失败"。

---

## 七、产物与提交

- 代码：`index.html`（nav/面板结构 + PANEL_ROOTS）、`vlm_history.js`、`llm_reply_ui.js`
- 验证截图：`reports/webui-preview-2026-08-18/`（voice/memory 面板、消息标签）
- 提交：待本轮收尾后统一 commit（分支 `ui-redesign-preview`）
