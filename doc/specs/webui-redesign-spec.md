# WebUI 重设计 Spec（草案）

> 生命周期标记：`<草案>`（前端端点产出，待审查组 ratification 后升 `<正式>` 并归档进 `决策/`）
> 作者端点：`<前端>`
> 关联：`design/joyai-redesign-preview.html`（v5→v6-lite.8 视觉验证件）、`doc/specs/webui-component-consistency-spec.md`（D1–D5）、`doc/adr/0019-webui-component-consistency.md`、`doc/specs/unified-api-config-ui.md`（provider 槽位数据契约，正式）、`doc/subsystems/voice-ui.md` §9（token 源）、`reports/handoff-settings-modal-feedback-2026-08-16.md`（逐轮反馈日志 §1–§21）
> 合规：套用 `决策/spec编写规范.md` 四要素（因果链 / 条件 harness / 负面约束 / 生命周期标记）。
> 边界声明：本 spec **只管前端呈现决策**；provider 槽位的数据字段 / 热重载端点以 `unified-api-config-ui.md`（正式）为准，不重复规定；元件形态受 `webui-component-consistency-spec.md` D1–D5 白名单约束。

---

## 1. 因果链（Why / Why-this-choice）

- **Problem**：设置弹窗 + 主视图经多轮（v5→v6-lite.8）在预览文件里反复返工，根因有二：
  ① 缺跨表面元件一致性硬约束（已由 `webui-component-consistency-spec.md` 补，ADR-0019）；
  ② 各槽位 / 区域的**前端呈现决策**散落在 handoff 工作日志（§1–§18），无单一 SSOT，回灌 `services/webui/static/` 时易漂移、易与已正式的 `unified-api-config-ui.md` 冲突。
- **Why this choice**：把分散的「前端决定怎么做」固化为一份 spec，引用一致性 spec D1–D5 与统一数据契约，作为回灌与跨对话交接的 SSOT。
- **被否方案及理由**：
  ① 继续以 handoff 工作日志当 SSOT → 否决：日志是流水、非决策件，无 harness、无负面约束、不被审查组 ratification；
  ② 把呈现决策写进 `unified-api-config-ui.md` → 否决：该 spec 是正式数据契约，混进前端呈现会模糊边界；
  ③ 每区域各写一份 spec → 否决：漂移、重复、不可比对。
  → 选「一份前端呈现 spec，绑定 D1–D5 + 引用统一数据契约」。

---

## 2. 范围

- **做什么（前端呈现决策 SSOT）**：
  - **模型 tab**：主模型 / 摘要模型各自独立 `本地/云端` 两段 Seg + 子表单；上下文长度默认 `16384`；输出长度新增（默认 `2048`）；移除 方案预设 / 密钥显示策略 / 并行槽数。
  - **Provider 槽位本云 idiom**：所有 provider 槽位（TTS / 嵌入 / 主模型 / 摘要模型 / 未来语音扩展）顶层一律 `本地/云端` 二选一 Seg（数据契约见 `unified-api-config-ui.md`）。
  - **状态分层与统一**：header `mode-chip`（live / jarvis / kws）复用 `.chip`；三层（模式入口 / 健康菜单 / 折叠项）。
  - **视频采集**：底部摄像头按钮 → 浮层 `.seg.seg-3` 三段（摄像头 / 屏幕 / RTSP 流）+ 子表单。
  - **输入栏**：麦克风嵌入 `.prompt` 内；发送按钮反馈；删 jarvis chip；删左缘脉冲灯。
- **不做什么（负面约束）**：
  1. 不规定 provider 槽位的数据字段 / 热重载端点（以 `unified-api-config-ui.md` 为准）。
  2. 不引入 CSS 框架 / 新设计 token（继承 `voice-ui.md` §9 + 一致性 spec D1–D5）。
  3. 不重复后端 P0 待办（TTS schema 扩字段、persona 槽位、inference 热调整、KWS 超参）——仅引用 handoff §3，不在此展开。
  4. 不规定业务布局之外的元件形态（元件形态受一致性 spec 白名单约束，新形态须在该 spec §3.1 登记）。

---

## 3. 设计（核心决策点）

### 3.1 模型 tab

- 主模型 / 摘要模型 各自独立 `本地/云端` 两段 Seg（`.seg` + `.seg-indicator` 滑动指示器 + `.subform` `display` 切换 + `.fade-in` 淡入）。
  - **本地**子表单：模型名 + 走共享后端端口（llama.cpp 7060）。
  - **云端**子表单：API 地址 + API Key（👁 掩码切换，**仅前端显示加密**，后端明文落盘 chmod 0600）+ 模型名。
- 上下文长度 `16384`（下限硬约束，过短易触发截断与推理错误）；输出长度 `2048`（对应 `max_tokens`）。
- 移除：方案预设 group、密钥显示策略 group、并行槽数 行。

### 3.2 Provider 槽位本云 idiom（统一选择器语言）

- 所有 provider 槽位顶层一律 `本地/云端` 二选一 Seg（`.seg` + `.seg-indicator` + `.subform`）；不展开 provider 下拉（v6-lite.9 用户确认：endpoint 即选择，没必要再选一遍 provider）。
- **本地** = env 自动探测 pill（绿点 + VAR + value，`.env-pill .pill`）+ 自填端口/路径字段，不暴露 api_key。
- **云端** = 三件套（API 地址 + API Key + 模型）；
  - **API 地址**：input + `<datalist>` 端点建议（常用 OpenAI / SiliconFlow / NVIDIA / DashScope …），可自由填；
  - **API Key**：input[type=password] + 眼睛图标切换显示/隐藏，**仅前端显示加密**（`.row-note` 绿点 + 一行小字说明 key 永远走后端、不入日志），后端明文落盘 chmod 0600（binding 在 `unified-api-config-ui.md` §X）；
  - **模型**：input + `<datalist>` 模型名建议，可自由填。
- **切换实现硬约束**：纯 CSS 滑动指示器 + `display` 切换 + keyframe 淡入；**禁止依赖 `window.confirm`**（预览 webview 屏蔽 confirm，依赖它会导致切换「没反映」—— v4-lite.2 实证教训）。
- 适用范围：主模型 / 摘要模型 / TTS / ASR / Embedding（v6-lite.9 补齐 ASR）；未来所有 provider 槽位默认套用本 idiom，不重复规定。
- 详见 ADR-0020（本云选择器作为统一 UI idiom 的架构决策）。

### 3.3 状态分层与统一（遵循一致性 spec D1–D5）

- header `mode-chip`（live / jarvis / kws）复用 `.chip`（圆角药丸 + 6px 状态点 + 文字）；状态点：绿=激活、灰=未激活；`title` 悬浮补状态说明；可点切换（live 与底部实时按钮双向同步）。
- 三层：header 模式快捷入口（轻量）/ 健康菜单（详细健康态，含 KWS / Jarvis / Live 实际值）/ 折叠项（装饰性：provider / LIVE 录制）。主视图不单独占卡片展示运行态（噪音 > 价值）。
- 截图 / 口头示意 = 传达意图，落地时优先复用既有元件 + 补信息缺口（如状态说明），**非像素级照搬**（v6-lite.3 教训）。

### 3.4 视频采集

- 触发入口：底部工具栏摄像头按钮 → 弹出 `.cap-pop` 浮层（**非主视图平铺**）。
- 浮层内 `.seg.seg-3` 三段（摄像头 / 屏幕 / RTSP 流）1:1 映射 `capture_webcam.js` / `screen_capture.js` / `capture_rtsp.js`；仅显示当前源子表单（设备 / 分辨率、帧率 / 间隔 / 每批帧数、RTSP 地址 / 分辨率）。
- 状态 chip 实时显示当前源 + 本地/网络归属（本地 `--ok` 绿、RTSP `--warn` 黄）。浮层开合独立保留 subform 选择状态。

### 3.5 输入栏

- 麦克风嵌入 `.prompt` 内、紧贴发送按钮左侧（参考微信式语音输入），复用 `.ctrl` 视觉语言（`.prompt-mic` 圆形 ghost 按钮）。
- 激活：图标变红 + 输入框红色 glow ring（`.prompt.voice-on`）；**不使用左缘脉冲灯**（v6-lite.8 已删）。
- 发送按钮：`.send:active` 缩放 + `.sent` 闪烁动画（点击反馈）。
- 顶部 `quick-tabs` 删 `jarvis` chip（保留 live / kws）；Jarvis 常驻并入麦克风激活态。

---

## 4. Harness（仅当满足条件才存在）

> 触发条件：本 spec 是回灌 `services/webui/static/` 与跨对话交接的 SSOT（≥2 agent 复用）→ 保留。

- **可复现工作流**：
  - 前端契约对照：`design/joyai-redesign-preview.html` ↔ `services/webui/static/styles.css` + `index.html` + `config_services.js` + `capture_*.js`。
  - 回灌自查清单（按 D1–D5）：元件是否全来自白名单；状态色是否复用 `--ok/--warn/--brand/--err`；有无裸文字状态标签；间距是否 8px 网格；字体是否 `--font`。
- **验证仪式（回灌 / 回归必跑，真实预览点击，非静态校验）**：
  1. 各 Seg（模型本云 / 嵌入本云 / KWS 三段 / 视频三段）滑动指示器跟随正确，无 `confirm` 依赖。
  2. 状态 chip 颜色/文字清晰（本地绿、RTSP 黄；激活绿点、未激活灰点）。
  3. 视频浮层：点摄像头弹出、外部点击 / × / 取消 / 开始采集 关闭、subform 选择保留。
  4. 麦克风激活 → 图标变红 + 输入框 glow；发送按钮点击有反馈。
  5. 上下文长度默认 `16384`、输出长度 `2048`、无 `tokens` 后缀。

---

## 5. 生命周期标记

`<草案>` — 前端端点产出，待审查组 ratification 后升 `<正式>` 并归档进 `决策/`。ratification 前本 spec 仅作回灌参考，不具硬约束效力（硬约束以 `webui-component-consistency-spec.md` + `unified-api-config-ui.md` 已正式件为准）。

---

## 6. 变更日志（端点内迭代的轻量修订，供审查组对比审稿）

- **v6-lite.9（2026-08-17）** — 删除嵌入云端残留 Provider 下拉（用户截图明确「本地/云端切换多好用，为什么还要下拉」→ endpoint 即选择，用 datalist 建议常用端点）；ASR 加本地/云端 seg 完成 6 槽位视觉统一；新增 `.row-note` 视觉小字工具类，把「仅前端显示加密」从 label-chip 改成 input 下方一行小字（贴近截图样式）。
- **v6-lite.7/.8（2026-08-16）** — 麦克风嵌入输入框；删 jarvis chip；发送按钮加反馈；移除左缘脉冲灯。
- **v6 → v6-lite.6（2026-08-16）** — 模型 tab 本云分拆；状态统一 `.chip`；视频采集浮层；底部按钮图标区分与文字标签。
- **v5** — 初版。
