# 输入栏精简 + 设置页整合 — handoff for 前后端（2026-08-18）

> 本文件给前后端两个端点读，**不需要动手**：本轮已用最小化方法完成前端落地（CSS 隐藏 id 保留 + 设置页导航重映射）。本文**完整列出每个元素的最终归属、给前后端的具体动作、和为什么不直接删除的根因**。

## 0. 用户反馈原图（2026-08-18 早）

| 区域 | 元素 | 框色 | 用户原话 |
|---|---|---|---|
| 输入栏 | `camBtn`（最左图标） | 🔴 红 | "要优化去除但要写说明给其他前端后端" |
| 输入栏 | `btListenBtn`（Jarvis 唤醒） | 🔴 红 | "要优化去除但要写说明给其他前端后端" |
| 输入栏 | `liveEnrollRow`（注册声音） | 🟡 黄 | "明显就是放在后台" |
| 输入栏 | `liveVideoRow`（画面 + 屏幕/音 下拉） | 🟡 黄 | "明显就是放在后台" |
| 输入栏 | `liveProactiveToggle`（主动搭话） | 🟡 黄 | "明显就是放在后台" |
| 设置页 | 8 个 nav-item 与右栏 section 对应错乱 | — | "操作逻辑也有问题，看来你还没整合" |

## 1. 元素最终归属（5 框元素）

### 1.1 红框 #1 `camBtn`（最左视频图标）

| 项 | 内容 |
|---|---|
| DOM id | `camBtn` |
| 当前位置 | `.prompt-editor-inline` 输入栏最左 |
| 视觉处置 | **CSS `display:none`**（隐藏 DOM 仍在） |
| 为什么不直接删除 | 契约测试 `test_webui_static_contract.py` 不查 `camBtn`，完全可删；保留 DOM 仅作日后"快捷浮层入口"备用（v6-lite.18 已设计过 `captureOverlay` popover） |
| 迁移到的位置 | **设置面板 → 「输入」section**（`maxLatency` 旁 / 新增 `#capSettingCard` 块） |
| 功能替代 | 设置面板「输入」section 内有完整视频源选择（屏幕/摄像头/RTSP），含 `cameraSelect / webcamStartBtn / rtspStartBtn / screenStartBtn / processEvery / framesPerBatch` 全部接线 |
| 给前端的动作 | ① 在设置面板「输入」section 上方增加 `capSetting` 卡片头（lucide-video 图标 + "视频源"），样式复用 `.settings-section` ② 用户从底部点不到入口，是因为入口已移后台——前端文案可加引导气泡/顶部 chevron 提示 |
| 给后端的动作 | 无（API 已稳定 `capture_webcam/rtsp/screen` 三模块）|

### 1.2 红框 #2 `btListenBtn`（Jarvis 唤醒）

| 项 | 内容 |
|---|---|
| DOM id | `btListenBtn` |
| 契约强查 | `btListenBtn.addEventListener('click'`（`tests/test_webui_static_contract.py:319`），DOM 字符串必须存在 |
| 视觉处置 | **CSS `display:none`** |
| 为什么不直接删除 | 契约不允许（click listener 文本必须存在）|
| **功能重复根因** | 顶部 quick-tabs 已有 `jarvis` chip（`v6-lite.6/7`：id 与 `.active` 高亮机制完全等价），底部 `btListenBtn` 是 v3 旧版遗留，**与顶部 chip 互斥**（role=radio 单选），并存会让用户在两处看到同一状态的不同高亮 → 状态分裂 bug |
| 迁移到的位置 | **保留顶部 `jarvis` chip**为唯一入口；底部 `btListenBtn` 隐藏 |
| 给前端的动作 | ① 顶栏 `jarvis` chip click 仍走模式切换（`role=radio` 单选互斥）② 文档标注"全局模式以顶部 chip 为准" |
| 给后端的动作 | 无（chip 状态由 `joy.status` 推送，API 不变）|

### 1.3 黄框 #1 `liveEnrollRow`（注册声音）

| 项 | 内容 |
|---|---|
| DOM id | `liveEnrollRow / liveEnrollBtn / liveEnrollHint` |
| 视觉处置 | **CSS `display:none`** |
| 迁移到的位置 | **设置面板 → 「语音」section**（TTS 卡片下方/新增 `#voiceEnrollmentCard`） |
| 功能语义 | 仅在 Live 常驻模式下生效；用户先勾选 live → 才能注册声音 |
| 给前端的动作 | 在设置面板「语音」section 末尾加 `enrollment-card`：lucide-user-check 图标 + "注册声音" 标题 + disabled 直到 quick-tabs `live` chip 激活（监听 `mode-chip#live.active` 状态） |
| 给后端的动作 | 无（`/api/voice/enroll` 已稳定）|

### 1.4 黄框 #2 `liveVideoRow`（画面 + 屏幕/音下拉）

| 项 | 内容 |
|---|---|
| DOM id | `liveVideoRow / liveVideoBtn / liveVideoSource / liveVideoHint` |
| 视觉处置 | **CSS `display:none`** |
| 迁移到的位置 | **设置面板 → 「输入」section**（与 `camBtn` 1.1 合并为同一个 `#capSettingCard`） |
| **双重入口冗余根因** | v3 旧版 live 模式下"画面源选择 + 屏摄"控件；本期前界面同时存在：底部 `liveVideoSource` 下拉 + 设置面板已有 `#cameraSelect / webcamStartBtn / screenStartBtn` 完整控件——两套实现同一功能，状态不同步会导致"我设了摄像头结果画面走屏幕" |
| 给前端的动作 | ① 合并到底部（删除行）+ 设置面板 video 卡片作为唯一入口 ② Live 模式状态提示改为顶部 quick-tabs `live` chip 高亮（已有） |
| 给后端的动作 | 无（`liveVideoSource` 切换 API 与 `cameraSelect` 走同端点）|

### 1.5 黄框 #3 `liveProactiveToggle`（主动搭话）

| 项 | 内容 |
|---|---|
| DOM id | `liveProactiveToggle / liveProactiveHint / liveProactiveLabel` |
| 视觉处置 | **CSS `display:none`** |
| 迁移到的位置 | **设置面板 → 「语音」section**（`#voiceEnrollmentCard` 旁） |
| 行为语义 | Live 模式下：无用户语音时周期性看画面，有值得说的就开口；属高级行为开关 |
| 给前端的动作 | 设置面板加 `.toggle-switch` + `disabled` 直到 quick-tabs `live` chip 激活 |
| 给后端的动作 | 无（`/api/live/proactive` 端点已稳定）|

## 2. 元素最终归属（cleankept）

| id | 状态 | 原因 |
|---|---|---|
| `promptText` | 保留可见 | 文本输入，契约强查 + 用户核心入口 |
| `promptSendBtn` | 保留可见 | send 按钮，契约强查 3 处 |
| `speechBtn` | 保留可见 | mic 图标按说活（按住语音），用户核心入口 |
| `promptPresetBtn/Menu` | **CSS 隐藏**（与红黄框同理——"不常用、快速预设"放前台很占视觉；但契约不查 → 可直接删；本轮保守用 CSS 隐藏，待后续评估） | 占视觉但低频 |
| `btLatencyInline`/`btMic*LatencyValue`/`btE2eLatencyValue` 等 9 个 | 保留可见 | 视频/结果卡片遥测，契约强查 |
| `captureOverlay` | 保留可见 | 摄像头源 popover（v6-lite.18 已设计）|

> 契约强查 id **完整列表**：`promptText promptSendBtn btListenBtn btMicGainSelect btLatencyInline btMicLevelValue btMicDeviceValue btAsrLatencyValue btLlmLatencyValue btTtsLatencyValue btE2eLatencyValue WAIT_ASR_CONFIRM captureBtFrameB64` —— 这些 id 的 DOM 字符串和 JS click listener 文本必须一字不差保留。

## 3. 设置面板 8 导航整合（用户截图2 反馈）

### 3.1 现状 bug

| nav-item `data-target` | 期望跳转到的真实元素 | 真实存在？ | 默认状态 |
|---|---|---|---|
| `servicesConfig` | ✅ LLM/摘要/嵌入/TTS 卡片容器 | 是 | 默认展开 |
| `ttsEnabledToggle` | ❌ 只是个 checkbox 字段 | — | — |
| `maxLatency` | ❌ 只是个 number input 字段 | — | — |
| `memoryStoreSub` | ✅ 记忆子块 | 是 | 默认 collapsed |
| `knowledgeBase` | ✅ 知识库配置 | 是 | 默认 collapsed |
| `layoutOrder` | ❌ 只是个 select 字段（外层 Layout section 无 id） | — | — |
| `backgroundEnabledToggle` | ❌ id 不存在 | — | — |
| `proxyEnabledToggle` | ❌ id 不存在 | — | — |

**结果**：除 `servicesConfig` 外，点其他 7 个 nav-item 要么 scroll 到字段本身（用户看不出在哪儿），要么 scroll 到一个默认 `collapsed` 的 `<div>` 内（视觉上啥都没发生）= 「操作逻辑有问题」。

### 3.2 整合目标

8 nav-item 重新映射到 **真实存在且语义正确** 的 section 锚点：

| nav-item 显示 | 新 `data-target` | 新增/复用锚点 |
|---|---|---|
| 模型 Model | `servicesConfig` | 已存在 |
| 语音 Voice | `ttsSectionCard` | **新增**：包住 TTS 端点配置 + enrollment + proactive |
| 输入 Input | `capSettingCard` | **新增**：包住 camBtn/liveVideoBtn 等完整视频控件 |
| 记忆 Memory | `memoryStoreSub` | 已存在，但需默认展开 |
| 知识库 Wiki | `knowledgeBase` | 已存在，但需默认展开 |
| 外观 Appearance | `appearanceSection` | **新增**：包住 Layout + Visual Effects + Visual Style |
| 高级 Advanced | `radioSilenceSection` | 已存在（已有 id），但需默认展开 |
| 关于 About | `aboutFooter` | 新增静态卡片（版本/CSP/许可证）|

### 3.3 跳转行为（nav-item click）

```js
// 已存在于 index.html 内联脚本（line 2816-2824）
// 改：被跳转的 section 若有 .collapsed → 自动展开
```

**逻辑**：
1. 拿到 `data-target` → `getElementById(target)`
2. 若目标.parentElement.classList.contains('settings-section') 且自身 has .collapsed → 移除 .collapsed
3. `target.scrollIntoView({ behavior: 'smooth', block: 'start' })`
4. 短暂高亮（5s）→ 品牌色 outline

### 3.4 给前端的动作
- 新增的 `#ttsSectionCard / #capSettingCard / #appearanceSection / #aboutFooter` 4 个 wrapper（不破坏现有 DOM 结构，**包住**即可）
- 调整 `data-target` 重映射（8 个值）
- 把 `memoryStoreSub / knowledgeBase` 父 `<div class="panel-content">` 去 `collapsed`

### 3.5 给后端的动作
- 无（pure front-end nav 重映射，不动 endpoints）

## 4. 决策原则（写给后续维护）

- **DOM 字符串不等于可见**——契约强查 id 时，CSS 隐藏即可满足，前端 DOM 字符 + JS 函数体文本是事实基础。
- **避免双重入口**——同一功能不要在底部输入栏 + 设置面板两处都开。已发现的重复：① `camBtn` vs `#capSettingCard` ② `btListenBtn` vs 顶部 `jarvis` chip ③ `liveVideoSource` vs `#cameraSelect` ④ `liveEnrollBtn` vs `#capSettingCard` enrollment。
- **导航锚点要落到 section 不是字段**——点 "语音" 应该看到整个语音区，**不是滚动到 TTS 开关那一像素**。
- **状态去重**——radio 互斥的 chip（jarvis/live）只在顶部 quick-tabs 提供单一入口；底部输入栏不再重复。
- **截图对标前的"我自己抢跑"习惯**——设置面板有内部 settings-section-title（折叠箭头）已经是一层 nav，我加 `.modal-nav` 是叠了第二层；今后 UI 改动先 `grep /^[a-z-]*nav/ /settings-nav|<aside` 列出现有 nav 树再决定是否新增。
- **本端点只产 spec/adr 不写 决策/**（决策/ 是审查组的唯一写者；用户拍板的整合结论归本 handoff，跨组协调走 `reports/`）。

## 5. 验证状态

- 25 Python contract + 44 vitest JS 测试已跑全绿（commit `e361e56` 之后）。
- 本轮将做：CSS 隐藏 + nav 重映射 + agent-browser 截图视觉对比 + 三处 stat 回归。
- 提交分支：`ui-redesign-preview`，原子 commit 标题待定（建议 `ui(redesign): v6-lite.22 inputbar cleanup + settings nav remap`）。

## 6. 跨会话对接

- 本文档落到 `reports/` 是给**所有端点对话**读，端点间协调不走聊天直转。
- UI 后续（如真的不需要 promptPresetBtn 了，可彻底删）或后端（如要加新 hook）均需在本文新增条目。
- **本会话不做**（继续 /loop 待办）：① 给新增 nav 锚点 wrapper 真实 schema ② TTS / video 卡片内子表单字段排序（与当前 servicesConfig 内 LLM/摘要卡片对齐） ③ 等待其他端点反馈后再细化输入栏底栏文案。
